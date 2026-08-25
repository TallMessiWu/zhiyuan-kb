"""RAG 问答（M5）— docs/design.md §6 五条硬性规则的落点。

流程：复用 M2 检索召回（run_search）→ rel 阈值筛出「命中」→ 组上下文 → 约束生成（JSON）
→ 服务端校验（fragment 逐字校验、conflict/citation 索引校验、REVIEW_DUE 风险回填）。

规则怎么落（逐条对 §6）：
1. 只基于命中段落作答：上下文只含检索命中的资产正文；citations 由服务端组装完整元数据。
2. 无命中/低于阈值 → not_found：**不调 LLM**，固定话术由本模块给出 —— 禁止通用知识补位
   不能靠提示词自觉，得靠根本不给它开口的机会。LLM 自报 insufficient / 零引用也按无据处理。
3. STALE/ARCHIVED 不入上下文：run_search(hist=False) 在召回层就隔离了，这里不需要再滤。
   （问答页 MVP 无历史模式，与原型一致；「已失效仅供追溯」的引用横幅记在 V1.1。）
4. 冲突并列：LLM 判互斥并给出双方立场，服务端只校验索引有效性，不改写、不裁决。
5. REVIEW_DUE 引用挂「可能过时」：风险提示由**服务端**从该资产的 open ReviewTask 取
   M4 的 ai_impact_summary 组装 —— 这不是生成任务，不许 LLM 代笔。

问答没有规则式兜底（摘要可以截正文凑合，答案不能编）：网关不可用或输出解析失败一律抛
AskUnavailable，api 层转 503 AI_UNAVAILABLE（「问答暂不可用」，不是 500）。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import KnowledgeAsset, ReviewTask, Status
from . import ai, indexing
from .search import REL_LABEL, ScoredAsset, run_search
from .text import query_terms, tokenize

log = logging.getLogger(__name__)


class AskUnavailable(RuntimeError):
    """网关不可用或输出不可解析。问答无兜底，api 层转 503 AI_UNAVAILABLE。"""


# §6 规则 2 的固定话术。前端把它渲染成 bad callout，并配「记录为知识缺口」按钮。
NOT_FOUND_ANSWER = (
    "没有找到经过验证的知识。知识库中没有状态为 VERIFIED 或 DRAFT 的资产能够回答该问题，"
    "系统不会用模型的通用知识补位。可以把它记录为知识缺口，供后续沉淀。"
)

# 每条资产正文送入 prompt 的截断（结论都在前面）；引用片段兜底选段的长度上限。
_CTX_BODY_CHARS = 3000
_FRAGMENT_MAX = 240

_ASK_SYSTEM = (
    "你是推理框架团队的知识库问答助手。只允许基于用户消息里给出的知识资产内容回答，"
    "绝对禁止用你自己的通用知识补充结论或参数。"
    "输出严格的 JSON 对象（不要 markdown 围栏、不要任何解释性文字），形状："
    '{"answer_md": "中文回答（markdown）", '
    '"citations": [{"index": 1, "fragment": "从该资产正文里逐字摘录的支撑句"}], '
    '"conflict": {"a": {"index": 1, "stand": "立场概括"}, "b": {"index": 2, "stand": "立场概括"}} 或 null, '
    '"insufficient": false}。'
    "规则：1) answer_md 直接给结论与关键参数，提到资产用它的 KA 编号；"
    "2) citations 只列你实际依据的资产，fragment 必须逐字摘录原文，不许改写；"
    "3) 若不同资产的结论互斥，填 conflict（双方 index 与立场），且 answer_md 不替任何一方下结论；"
    "4) 资产的状态（VERIFIED/DRAFT/REVIEW_DUE）是可信度分层，不是能否引用的开关：DRAFT 或"
    "标注（待验证）的内容只要覆盖问题就照常作答并引用，在 answer_md 里如实转述其（待验证）"
    "属性即可，系统会随引用展示状态标注；"
    "5) 只有当资产内容确实没有覆盖问题时，insufficient 才填 true、citations 留空，不要硬答。"
)


@dataclass
class AskCandidate:
    """一条命中：检索结果 + rel 分项 + 正文（送入上下文用）。"""

    scored: ScoredAsset
    rel: float
    body_md: str

    @property
    def asset(self) -> KnowledgeAsset:
        return self.scored.asset


@dataclass
class AskAnswer:
    answer_md: str
    citations: list[dict] = field(default_factory=list)   # Citation 的构造参数
    risks: list[dict] = field(default_factory=list)       # AskRisk 的构造参数
    conflict: dict | None = None                          # AskConflict 的构造参数
    not_found: bool = False


# 从搜索取多少条候选再按 rel 挑。搜索的总分是给结果列表排序的（trust/fresh/proof 都算），
# 问答只关心「谁与问题相关」—— M5 真链路验收踩过：aclgraph 提问时 KA-010(REVIEW_DUE)
# rel=30 全场最高，却被 6 条弱相关 VERIFIED 的 trust+fresh 总分挤出 top5，问答答不出来，
# 还顺带违反了 §6 规则 5（REVIEW_DUE 应可引用、挂提示，而不是被排序悄悄排除）。
_RETRIEVE_POOL = 20


def retrieve(db: Session, question: str) -> list[AskCandidate]:
    """召回命中集合。STALE/ARCHIVED 在召回层被隔离（hist=False）；
    按 rel 分项（不是总分）选前 N 条，阈值也打在 rel 上。"""
    outcome = run_search(db, q=question, limit=max(settings.ask_max_context * 4, _RETRIEVE_POOL))
    hits: list[tuple[float, ScoredAsset]] = []
    for scored in outcome.items:
        rel = next((p.value for p in scored.score.parts if p.label == REL_LABEL), 0.0)
        if rel >= settings.ask_min_rel:
            hits.append((rel, scored))
    hits.sort(key=lambda pair: (-pair[0], pair[1].asset.id))
    return [
        AskCandidate(scored, rel, indexing.body_md_of(db, scored.asset))
        for rel, scored in hits[:settings.ask_max_context]
    ]


def not_found_answer() -> AskAnswer:
    return AskAnswer(answer_md=NOT_FOUND_ANSWER, not_found=True)


def _context_block(index: int, cand: AskCandidate) -> str:
    a = cand.asset
    fw = f"{cand.scored.framework} {cand.scored.fw_version}".strip()
    meta = " · ".join(x for x in (
        f"状态 {a.status.value}",
        f"适用 {fw}" if fw else "",
        f"模型 {'/'.join(cand.scored.models)}" if cand.scored.models else "",
        f"更新 {a.updated_at:%Y-%m-%d}",
    ) if x)
    return (f"[{index}] KA-{a.id:03d}「{a.title}」（{meta}）\n"
            f"正文：\n{cand.body_md[:_CTX_BODY_CHARS]}")


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _fragment_ok(fragment: str, body: str) -> bool:
    """「逐字摘录」校验：忽略空白差异后必须是正文子串 —— 防 LLM 编造引用。"""
    frag = _squash(fragment)
    return bool(frag) and frag in _squash(body)


def _fallback_fragment(body: str, terms: list[str]) -> str:
    """服务端选段兜底：按查询词重合度取最相关的一段。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip() and not p.strip().startswith("#")]
    if not paras:
        return body[:_FRAGMENT_MAX]
    want = set(terms)
    best = max(paras, key=lambda p: len(set(tokenize(p)) & want))
    return best[:_FRAGMENT_MAX]


def _build_risks(db: Session, cited: list[AskCandidate]) -> list[dict]:
    """§6 规则 5：被引用的 REVIEW_DUE 附「可能过时」，链其复核任务与 M4 的 AI 影响摘要。"""
    risks: list[dict] = []
    for cand in cited:
        a = cand.asset
        if a.status is not Status.REVIEW_DUE:
            continue
        task = db.scalar(
            select(ReviewTask)
            .where(ReviewTask.asset_id == a.id, ReviewTask.state == "open")
            .order_by(ReviewTask.created_at.desc(), ReviewTask.id.desc())
        )
        reason = f"：{a.status_reason}" if a.status_reason else "。"
        risks.append({
            "type": "warn",
            "text": (f"引用的 KA-{a.id:03d} 处于 REVIEW-DUE（可能过时）{reason}"
                     "结论大概率仍可参考，但请以复核结果为准。"),
            "asset_id": a.id,
            "review_task_id": task.id if task else None,
            "ai_impact_summary": task.ai_impact_summary if task else "",
        })
    return risks


def _citation_payload(cand: AskCandidate, fragment: str) -> dict:
    a = cand.asset
    return {
        "asset_id": a.id,
        "title": a.title,
        "fragment": fragment,
        "status": a.status,
        "framework": cand.scored.framework,
        "fw_version": cand.scored.fw_version,
        "models": cand.scored.models,
        "updated_at": a.updated_at,
    }


def generate(db: Session, question: str, candidates: list[AskCandidate]) -> AskAnswer:
    """约束生成 + 服务端校验。candidates 非空；网关/解析失败抛 AskUnavailable。"""
    prompt = (
        f"问题：{question}\n\n"
        f"候选知识资产（{len(candidates)} 条，编号从 1 开始）：\n\n"
        + "\n\n".join(_context_block(i + 1, c) for i, c in enumerate(candidates))
    )

    data: dict | None = None
    for attempt in range(2):     # 输出偶发不合形状时重试一次，再失败就是真不可用
        text = ai.chat(prompt, _ASK_SYSTEM)
        if text is None:
            raise AskUnavailable("LLM 网关不可用")
        data = ai.parse_json_output(text)
        if data is not None:
            break
        log.warning("问答输出不是合法 JSON（第 %d 次），重试", attempt + 1)
    if data is None:
        raise AskUnavailable("问答输出无法解析为 JSON")

    answer_md = str(data.get("answer_md") or "").strip()
    raw_cites = data.get("citations") if isinstance(data.get("citations"), list) else []

    # 校验引用索引：无效的丢弃；同一资产多条只留第一条
    terms = query_terms(question)
    citations: list[dict] = []
    cited: list[AskCandidate] = []
    seen: set[int] = set()
    for row in raw_cites:
        if not isinstance(row, dict):
            continue
        idx = row.get("index")
        if not isinstance(idx, int) or not (1 <= idx <= len(candidates)) or idx in seen:
            continue
        seen.add(idx)
        cand = candidates[idx - 1]
        fragment = str(row.get("fragment") or "").strip()
        if not _fragment_ok(fragment, cand.body_md):
            fragment = _fallback_fragment(cand.body_md, terms)
        citations.append(_citation_payload(cand, fragment[:_FRAGMENT_MAX]))
        cited.append(cand)

    # LLM 自报资料不足，或一条有效引用都没有 → 无据（规则 2：没有依据就不许有答案）
    if data.get("insufficient") or not answer_md or not citations:
        return not_found_answer()

    conflict = None
    raw_conflict = data.get("conflict")
    if isinstance(raw_conflict, dict):
        sides = {}
        for key in ("a", "b"):
            side = raw_conflict.get(key)
            idx = side.get("index") if isinstance(side, dict) else None
            if isinstance(idx, int) and 1 <= idx <= len(candidates):
                sides[key] = {
                    "asset_id": candidates[idx - 1].asset.id,
                    "stand": str(side.get("stand") or "").strip(),
                }
        if len(sides) == 2 and sides["a"]["asset_id"] != sides["b"]["asset_id"]:
            conflict = sides

    return AskAnswer(
        answer_md=answer_md,
        citations=citations,
        risks=_build_risks(db, cited),
        conflict=conflict,
    )
