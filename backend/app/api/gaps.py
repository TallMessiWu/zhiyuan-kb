from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from ..db import get_db

router = APIRouter()


@router.get("/gaps")
def list_gaps(db: Session = Depends(get_db)):
    """知识缺口列表（open/claimed，按 hit_count 降序）。TODO(M3)"""
    raise NotImplementedError("M3")


@router.post("/gaps/{gap_id}/claim")
def claim(gap_id: int, db: Session = Depends(get_db), x_user: str = Header(default="anonymous")):
    """认领缺口 -> status=claimed，异步调 ai.draft_from_session 生成 DRAFT 底稿。TODO(M3)"""
    raise NotImplementedError("M3")
