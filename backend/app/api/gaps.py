from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import KnowledgeGap
from ..schemas import GapOut

router = APIRouter()


def load_gaps(db: Session, *, limit: int = 20) -> list[KnowledgeGap]:
    """未解决的缺口：open 排在 claimed 前，再按被问次数、最近时间排序。
    首页与缺口列表共用，排序口径只有这一处。"""
    order_by_state = case((KnowledgeGap.status == "open", 0), else_=1)
    return list(db.scalars(
        select(KnowledgeGap)
        .where(KnowledgeGap.status != "resolved")
        .order_by(order_by_state, KnowledgeGap.hit_count.desc(), KnowledgeGap.last_at.desc())
        .limit(limit)
    ).all())


@router.get("/gaps", response_model=list[GapOut])
def list_gaps(limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)):
    """知识缺口列表。缺口的写入路径（POST /feedback/not-found）在 M3。"""
    return [GapOut.model_validate(g) for g in load_gaps(db, limit=limit)]


@router.post("/gaps/{gap_id}/claim")
def claim(gap_id: int, db: Session = Depends(get_db), x_user: str = Header(default="anonymous")):
    """认领缺口 -> status=claimed，异步调 ai.draft_from_session 生成 DRAFT 底稿。TODO(M3)"""
    raise NotImplementedError("M3")
