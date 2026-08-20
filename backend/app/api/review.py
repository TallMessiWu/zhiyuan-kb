from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import ReviewResolveIn

router = APIRouter()


@router.get("/review")
def list_review(db: Session = Depends(get_db)):
    """复核队列（open，按 priority 降序）。TODO(M4)：
    按需治理过滤：只返回 近90天有使用 / tier=core / 高风险 的任务（design.md §4）。
    """
    raise NotImplementedError("M4")


@router.post("/review/{task_id}/resolve")
def resolve(task_id: int, body: ReviewResolveIn, db: Session = Depends(get_db),
            x_user: str = Header(default="anonymous")):
    """四选一处理：confirm / accept_draft / stale / archive。TODO(M4)：
    调 services.review_queue.resolve()；非法流转返回 409 INVALID_TRANSITION。
    accept_draft 生成 AssetVersion(created_from=ai_draft) 并回到 DRAFT —— 绝不直达 VERIFIED。
    """
    raise NotImplementedError("M4")
