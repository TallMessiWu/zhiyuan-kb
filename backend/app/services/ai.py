"""LLM 网关（OpenAI 兼容）。所有产出都是「建议」：草稿、摘要、分类、关联。

硬约束（根 CLAUDE.md 规则 1）：本模块产出的任何内容不得直接触发 VERIFIED；
生成的正文版本必须 created_from=ai_draft，摘要必须标记 summary_source="ai"。

两个工程约定（M2 定的，改动前先读）：
1. **同步而非 async**：搜索/发布都是同步路由（FastAPI 把它们丢线程池跑），同步 httpx
   不会阻塞事件循环，也省得为了一次摘要把整条调用链改成 async。
2. **失败即降级，不抛给用户**：网关不可达时返回 None，调用方回落到规则式实现。
   配 ZY_AI_SUMMARY=on / ZY_VECTOR_SEARCH=on 可以把降级关掉（配置错了要响，别静默）。
"""
from __future__ import annotations

import json
import logging
import re
import time

import httpx

from ..config import settings

log = logging.getLogger(__name__)


def parse_json_output(text: str) -> dict | None:
    """解析 LLM 的 JSON 输出：剥掉 ``` 围栏 / 前后闲话后 json.loads。
    返回 None 表示不可解析（调用方决定重试或降级）。问答与缺口底稿共用。"""
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", body)
    if not body.startswith("{"):
        start, end = body.find("{"), body.rfind("}")
        if start == -1 or end <= start:
            return None
        body = body[start:end + 1]
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class GatewayUnavailable(RuntimeError):
    """网关不可用且当前模式为 on（要求强依赖）时抛出。"""


class _Circuit:
    """熔断：网关连不上以后静默一段时间，别让每个请求都去等一轮超时。

    没有半开重试的复杂度 —— 静默期一过就正常放行，失败再熔断。
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._blocked_until = 0.0

    def closed(self) -> bool:
        return time.monotonic() >= self._blocked_until

    def trip(self, exc: Exception) -> None:
        self._blocked_until = time.monotonic() + settings.llm_circuit_seconds
        log.warning("%s 网关不可用，%.0fs 内降级：%s", self.name, settings.llm_circuit_seconds, exc)

    def reset(self) -> None:
        self._blocked_until = 0.0


# chat 与 embedding 可能是两家服务（例：chat 走 DeepSeek、embedding 走 SiliconFlow），
# 熔断必须按端点分开 —— 否则 embedding 那路 404 一次，chat 也被静默掉 60s。
_circuits = {"chat": _Circuit("chat"), "embedding": _Circuit("embedding")}


def _endpoint(kind: str) -> tuple[str, str]:
    """(base_url, api_key)。embedding 网关/密钥留空时跟随主网关。"""
    if kind == "embedding":
        return (
            settings.embedding_gateway_url or settings.llm_gateway_url,
            settings.embedding_api_key or settings.llm_api_key,
        )
    return settings.llm_gateway_url, settings.llm_api_key


def _mode(name: str) -> str:
    """读 ZY_AI_SUMMARY / ZY_VECTOR_SEARCH 这类开关：auto（探测降级）/ on（强依赖）/ off。"""
    return {"ai_summary": settings.ai_summary, "vector_search": settings.vector_search}[name].lower()


def _post(path: str, payload: dict, *, mode: str, kind: str = "chat",
          timeout: float | None = None) -> dict | None:
    """调网关。返回 None 表示「本次不可用，请降级」。"""
    if _mode(mode) == "off":
        return None
    circuit = _circuits[kind]
    if not circuit.closed():
        return None
    base_url, api_key = _endpoint(kind)
    # key 留空 = 内网免鉴权网关，不带头；非空按 OpenAI 惯例带 Bearer
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with httpx.Client(base_url=base_url, headers=headers,
                          timeout=timeout or settings.llm_timeout) as client:
            resp = client.post(path, json=payload)
            resp.raise_for_status()
            circuit.reset()
            return resp.json()
    except Exception as exc:                      # 网络错误 / 超时 / 4xx / 5xx 一律按不可用处理
        circuit.trip(exc)
        if _mode(mode) == "on":
            raise GatewayUnavailable(f"LLM 网关不可用：{exc}") from exc
        return None


def chat(prompt: str, system: str = "", *, mode: str = "ai_summary",
         timeout: float | None = None) -> str | None:
    """生成类调用，默认用 generation_timeout（摘要/草稿/问答都是等得起的场景；
    M5 实测：拿 6s 检索超时卡摘要会频繁超时→熔断，连带问答一起被降级 60s）。"""
    data = _post("/chat/completions", {
        "model": settings.llm_model,
        "messages": ([{"role": "system", "content": system}] if system else [])
                    + [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }, mode=mode, timeout=timeout or settings.generation_timeout)
    if not data:
        return None
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError, TypeError) as exc:
        log.warning("LLM 网关返回体不符合 OpenAI 形状：%s", exc)
        return None


def embed(texts: list[str], *, mode: str = "vector_search") -> list[list[float]] | None:
    """bge-m3 embedding。返回 None 表示不可用（调用方跳过向量召回）。

    测试与离线场景通过 monkeypatch 本函数注入假向量，所以调用方一律写
    `ai.embed(...)` 而不是 `from .ai import embed`，否则打桩打不上。
    """
    if not texts:
        return []
    data = _post("/embeddings", {"model": settings.embedding_model, "input": texts},
                 mode=mode, kind="embedding")
    if not data:
        return None
    try:
        vectors = [row["embedding"] for row in data["data"]]
    except (KeyError, TypeError) as exc:
        log.warning("embedding 网关返回体异常：%s", exc)
        return None
    if len(vectors) != len(texts):
        log.warning("embedding 条数不匹配：请求 %d 条，返回 %d 条", len(texts), len(vectors))
        return None
    return vectors


SUMMARY_MAX = 140
_SUMMARY_SYSTEM = (
    "你是推理框架团队的知识库编辑。把工程记录压成一句可检索的中文摘要，"
    f"不超过 {SUMMARY_MAX} 字，直接给结论与关键参数，不要复述标题、不要加前缀、不要换行。"
)


def summarize(title: str, body_md: str) -> str | None:
    """给资产生成检索用摘要。返回 None 表示不可用，调用方回落到规则式摘要。"""
    text = chat(f"标题：{title}\n\n正文：\n{body_md}", _SUMMARY_SYSTEM)
    if not text:
        return None
    return " ".join(text.split())[:SUMMARY_MAX]


def gateway_configured() -> bool:
    """给能力上报用：开关没关且熔断未打开，就认为「这次可能能用」。"""
    return _mode("ai_summary") != "off" and _circuits["chat"].closed()


_GAP_DRAFT_SYSTEM = (
    "你是推理框架团队的知识库编辑。团队记录了一个知识缺口 —— 反复被问但库里没有答案的问题。"
    "请基于缺口问句和给出的相关资产片段，为将要沉淀这份知识的作者生成一份预填底稿。"
    "输出严格的 JSON 对象（不要 markdown 围栏、不要解释），形状："
    '{"title": "一句话问题标题", "problem": "问题描述（markdown）", '
    '"env": "适用环境（框架/版本/硬件，一两句）", '
    '"conclusion": "结论或排查思路（markdown），拿不准的逐条标注（待验证）", '
    '"tags": ["…"], "direction": "model|chain|feature", "models": ["…"], '
    '"framework": "…", "fw_version": "…", '
    '"code_refs": [{"repo": "org/repo", "path_or_key": "相关源码路径或配置键", "note": "…"}]}。'
    "相关资产只是线索：能支撑的写进结论并说明来自哪份资产；不足以下结论的照实写（待验证），"
    "绝对不要编造参数或版本号。这是给作者改的底稿，不是最终答案。"
)


def draft_from_session(question: str, context: str) -> dict | None:
    """缺口认领的 AI 底稿：从缺口问句 + 相关检索上下文生成沉淀页预填内容。

    返回 None 表示网关不可用或输出不可解析 —— 认领本身不受影响（M3 定的：认领只登记），
    调用方转 503，作者仍可打开空白沉淀页手写。产出只是「建议」：作者确认三项后走
    POST /assets 正常发布为 DRAFT（硬规则 1：AI 只到草稿为止）。
    """
    text = chat(
        f"知识缺口问句：{question}\n\n相关资产片段（可能为空）：\n{context or '（库内没有相关内容）'}",
        _GAP_DRAFT_SYSTEM,
    )
    if not text:
        return None
    data = parse_json_output(text)
    if data is None:
        log.warning("缺口底稿输出不是合法 JSON，放弃本次生成")
    return data


# 影响摘要长度上限：复核队列一条里要读完，比检索摘要（140）宽松但不该是一篇文章
IMPACT_MAX = 400
_IMPACT_SYSTEM = (
    "你是推理框架团队的知识库维护助手。对照一份知识资产的正文与其关联代码/版本的变更描述，"
    "指出资产哪些小节可能因这次变更失效、哪些小节不受影响，并给出判断依据。"
    "只做影响面分析，不判断知识本身的对错，也不要建议新的结论。"
    f"中文作答，不超过 {IMPACT_MAX} 字，不要换行、不要列表符号。"
)

_DRAFT_SYSTEM = (
    "你是推理框架团队的知识库编辑。按给出的代码/版本变更修订知识资产正文，"
    "输出完整的更新后 markdown 正文：未受影响的小节保留原文，只改写受影响的部分，"
    "并在改动处以（待验证）标注。拿不准的内容保留原文并注明存疑。"
    "直接输出正文，不要任何解释性前言或代码围栏。"
)

# 送入 prompt 的截断：正文取前 6000 字（结论都在前面），变更描述取前 2000 字。
_PROMPT_BODY_CHARS = 6000
_PROMPT_CHANGE_CHARS = 2000


def impact_summary(asset_body: str, change_text: str) -> str | None:
    """生成「可能受影响内容」摘要。返回 None 表示网关不可用 —— 复核任务照建，只是没有摘要。"""
    text = chat(
        f"资产正文：\n{asset_body[:_PROMPT_BODY_CHARS]}\n\n代码/版本变更：\n{change_text[:_PROMPT_CHANGE_CHARS]}",
        _IMPACT_SYSTEM,
    )
    if not text:
        return None
    return " ".join(text.split())[:IMPACT_MAX]


def update_draft(asset_body: str, change_text: str) -> str | None:
    """生成更新草稿正文（markdown）。返回 None 表示网关不可用，调用方跳过建草稿版本。

    调用方（services/review_queue.py::attach_ai_review）负责以 created_from=ai_draft 建版本；
    草稿版本在被复核「接受」前不改 current_version_id（硬规则 1：AI 只到草稿为止）。
    """
    text = chat(
        f"资产正文：\n{asset_body[:_PROMPT_BODY_CHARS]}\n\n代码/版本变更：\n{change_text[:_PROMPT_CHANGE_CHARS]}",
        _DRAFT_SYSTEM,
    )
    if not text:
        return None
    return text.strip()
