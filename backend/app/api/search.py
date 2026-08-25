"""检索与问答接口（docs/api-contract.md 「搜索与问答」节）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Direction, SearchEvent, Status
from ..schemas import (
    AskConflict,
    AskIn,
    AskResponse,
    AskRisk,
    Citation,
    RecallOut,
    ScoreOut,
    ScorePartOut,
    SearchItem,
    SearchResponse,
)
from ..services import ask as ask_service
from ..services.search import ScoredAsset, run_search
from .assets import USER_ID_MAX, build_brief

router = APIRouter()

QUERY_MAX = 500      # SearchEvent.query 的列宽


def _enum_param(value: str | None, enum, name: str):
    """把查询串解析成枚举。空串按「不筛选」处理 —— 前端的下拉框「全部」就是空值，
    直接用 FastAPI 的枚举类型会让 ?status= 变成 422。"""
    if not value:
        return None
    try:
        return enum(value)
    except ValueError:
        allowed = "/".join(e.value for e in enum)
        raise HTTPException(422, detail=("VALIDATION_ERROR", f"{name} 只能是 {allowed}")) from None


def _to_item(scored: ScoredAsset) -> SearchItem:
    return SearchItem(
        asset=build_brief(
            scored.asset, models=scored.models,
            framework=scored.framework, fw_version=scored.fw_version,
        ),
        score=ScoreOut(
            total=scored.score.total,
            parts=[ScorePartOut(label=p.label, value=p.value) for p in scored.score.parts],
        ),
    )


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(default="", max_length=QUERY_MAX),
    direction: str | None = None,
    model: str | None = None,
    framework: str | None = None,
    status: str | None = None,
    hist: bool = False,
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    x_user: str = Header(default="anonymous", max_length=USER_ID_MAX),
):
    """混合检索：双路召回 → RRF → 业务重排，逐条返回分项得分（docs/design.md §5）。

    同时落一条 SearchEvent —— 它是「知识需求事件」，看板的有效复用率分母要用（§9），
    所以哪怕零结果也必须记，不能只在有结果时记。
    """
    outcome = run_search(
        db,
        q=q,
        direction=_enum_param(direction, Direction, "direction"),
        model=model or None,
        framework=framework or None,
        status=_enum_param(status, Status, "status"),
        hist=hist,
        limit=limit,
        offset=offset,
    )
    items = [_to_item(s) for s in outcome.items]

    event = SearchEvent(
        user_id=x_user,
        query=q[:QUERY_MAX],
        filters={
            "direction": direction or "", "model": model or "", "framework": framework or "",
            "status": status or "", "hist": hist,
        },
        mode="search",
        result_ids=[item.asset.id for item in items],
    )
    db.add(event)
    db.commit()

    return SearchResponse(
        items=items,
        search_event_id=event.id,
        hist=hist,
        total=outcome.total,
        terms=outcome.terms,
        recall=RecallOut(
            keyword=outcome.recall_backends["keyword"],
            vector=outcome.recall_backends["vector"],
            keyword_hits=outcome.recall_hits["keyword"],
            vector_hits=outcome.recall_hits["vector"],
        ),
    )


@router.post("/ask", response_model=AskResponse)
def ask(body: AskIn, db: Session = Depends(get_db),
        x_user: str = Header(default="anonymous", max_length=USER_ID_MAX)):
    """RAG 问答（M5，硬性规则见 docs/design.md §6，实现在 services/ask.py）。

    SearchEvent(mode=qa) 在检索完成后、生成之前就提交 —— 问答会话是需求事件（§9 分母），
    需求在提问那一刻已经发生，AI 后续失败不该把它抹掉；503 的会话也要进分母。

    问答没有规则式兜底：网关不可用 / 输出不可解析返回 503 AI_UNAVAILABLE
    （「问答暂不可用」），绝不悄悄用模型通用知识把答案编出来。
    """
    question = body.question.strip()
    candidates = ask_service.retrieve(db, question)

    event = SearchEvent(
        user_id=x_user,
        query=question[:QUERY_MAX],
        mode="qa",
        result_ids=[c.asset.id for c in candidates],
    )
    db.add(event)
    db.commit()

    if not candidates:
        answer = ask_service.not_found_answer()
    else:
        try:
            answer = ask_service.generate(db, question, candidates)
        except ask_service.AskUnavailable as exc:
            raise HTTPException(503, detail=(
                "AI_UNAVAILABLE",
                "问答暂不可用（LLM 网关不可达或输出异常），请稍后再试；检索与浏览不受影响。",
            )) from exc

    return AskResponse(
        answer_md=answer.answer_md,
        citations=[Citation(**c) for c in answer.citations],
        risks=[AskRisk(**r) for r in answer.risks],
        conflict=AskConflict(**answer.conflict) if answer.conflict else None,
        not_found=answer.not_found,
        search_event_id=event.id,
    )
