from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import NotFoundIn, StaleIn, UsefulIn

router = APIRouter()


@router.post("/feedback/useful")
def useful(body: UsefulIn, db: Session = Depends(get_db), x_user: str = Header(default="anonymous")):
    """「有用，完成任务」。TODO(M3)：
    建 ReuseEvent(outcome=success, fw_version_at_use 自动带出) + UserFeedback(useful)；
    asset.reuse_count += 1；
    若 asset.status==DRAFT 且 x_user != author -> state_machine.transition(
        VERIFIED, nonauthor_reuse, evidence=reuse_event) + ValidationRecord(reuse_success)。
    """
    raise NotImplementedError("M3")


@router.post("/feedback/stale")
def stale(body: StaleIn, db: Session = Depends(get_db), x_user: str = Header(default="anonymous")):
    """「内容可能过时」。TODO(M3)：UserFeedback(maybe_stale) + review_queue.open_task(user_feedback)。"""
    raise NotImplementedError("M3")


@router.post("/feedback/not-found")
def not_found(body: NotFoundIn, db: Session = Depends(get_db), x_user: str = Header(default="anonymous")):
    """「没有找到答案」。TODO(M3)：KnowledgeGap 建新/语义相似累计（hit_count/last_at/reporters）。"""
    raise NotImplementedError("M3")
