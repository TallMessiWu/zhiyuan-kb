"""LLM 网关接口（占位）。所有产出都是「建议」：草稿、摘要、分类、关联。

硬约束（根 CLAUDE.md 规则 1）：本模块产出的任何内容不得直接触发 VERIFIED；
生成的正文版本必须 created_from=ai_draft。
"""
from __future__ import annotations

import httpx

from ..config import settings


async def _chat(prompt: str, system: str = "") -> str:
    """调内部 LLM 网关（OpenAI 兼容）。M1 阶段可返回固定占位文本以便联调。"""
    async with httpx.AsyncClient(base_url=settings.llm_gateway_url, timeout=60) as client:
        resp = await client.post("/chat/completions", json={
            "model": settings.llm_model,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + [{"role": "user", "content": prompt}],
        })
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def draft_from_session(transcript: str) -> dict:
    """从会话/Diff/Issue 提取草稿：{title, problem, env, conclusion, tags, direction, code_refs}。
    TODO(M1): prompt 模板 + JSON 结构化输出校验。"""
    raise NotImplementedError


async def impact_summary(asset_body: str, diff_text: str) -> str:
    """生成「可能受影响内容」摘要：指出资产哪一节受哪些 diff 影响、哪些节不受影响。
    TODO(M4)"""
    raise NotImplementedError


async def update_draft(asset_body: str, diff_text: str) -> str:
    """生成更新草稿正文（markdown）。调用方负责以 created_from=ai_draft 建版本。
    TODO(M4)"""
    raise NotImplementedError


async def embed(texts: list[str]) -> list[list[float]]:
    """bge-m3 embedding。TODO(M2)"""
    raise NotImplementedError
