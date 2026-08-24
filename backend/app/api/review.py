"""复核队列接口（M4）：列表 + 四选一处理。

硬规则提醒：接受 AI 草稿只能把资产送回 DRAFT（状态机强制）；正文切换后
必须刷新检索索引 —— 都在 services/review_queue.resolve 里，本模块只做参数与前置校验。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AssetVersion, KnowledgeAsset, ReviewTask
from ..schemas import ReviewListOut, ReviewResolveIn, ReviewResolveOut, ReviewTaskOut
from ..services import review_queue
from .assets import USER_ID_MAX
from .home import briefs_for

router = APIRouter()


@router.get("/review", response_model=ReviewListOut)
def list_review(db: Session = Depends(get_db)):
    """open 任务按 priority 降序；按需治理过滤（design.md §4）：
    只列「近 90 天有使用 / tier=core / 高风险标签」的资产，其余 REVIEW_DUE 只降权不打扰。
    """
    rows = review_queue.governed_open_tasks(db)
    briefs = briefs_for(db, [asset for _, asset in rows])
    usage = review_queue.usage_counts(db, [asset.id for _, asset in rows], days=30)

    draft_ids = [task.ai_draft_version_id for task, _ in rows if task.ai_draft_version_id]
    drafts = {
        v.id: v.body_md
        for v in db.scalars(select(AssetVersion).where(AssetVersion.id.in_(draft_ids)))
    } if draft_ids else {}

    items = [
        ReviewTaskOut(
            id=task.id,
            asset=briefs[asset.id],
            trigger=task.trigger,
            trigger_detail=task.trigger_detail,
            diff_ref=task.diff_ref,
            ai_impact_summary=task.ai_impact_summary,
            ai_draft_version_id=task.ai_draft_version_id,
            ai_draft=drafts.get(task.ai_draft_version_id, ""),
            priority=task.priority,
            priority_label=review_queue.priority_label(task.priority),
            usage_30d=usage.get(asset.id, 0),
            created_at=task.created_at,
        )
        for task, asset in rows
    ]
    return ReviewListOut(items=items, total=len(items))


@router.post("/review/{task_id}/resolve", response_model=ReviewResolveOut)
def resolve(task_id: int, body: ReviewResolveIn, db: Session = Depends(get_db),
            x_user: str = Header(default="anonymous", max_length=USER_ID_MAX)):
    """四选一：confirm / accept_draft / stale / archive，全部走状态机并留证据。

    非法流转（资产已不在 REVIEW_DUE 等）由状态机抛 InvalidTransition → 409。
    """
    task = db.get(ReviewTask, task_id)
    if task is None:
        raise HTTPException(404, detail=("NOT_FOUND", f"复核任务 {task_id} 不存在"))
    if task.state != "open":
        raise HTTPException(409, detail=(
            "TASK_ALREADY_DONE",
            f"复核任务 {task_id} 已由 {task.handled_by} 处理（{task.action}），不能重复处理"))
    if body.action == "accept_draft" and task.ai_draft_version_id is None:
        raise HTTPException(409, detail=(
            "NO_AI_DRAFT", "该任务没有 AI 更新草稿（生成时网关不可用），请选择其他处理方式"))

    replaced_by: KnowledgeAsset | None = None
    if body.replaced_by is not None:
        replaced_by = db.get(KnowledgeAsset, body.replaced_by)
        if replaced_by is None:
            raise HTTPException(422, detail=(
                "VALIDATION_ERROR", f"替代资产 {body.replaced_by} 不存在"))

    note = review_queue.resolve(
        db, task, action=body.action, actor=x_user,
        note=body.note.strip(), replaced_by=replaced_by,
    )
    db.commit()

    asset = db.get(KnowledgeAsset, task.asset_id)
    return ReviewResolveOut(
        task_id=task.id,
        action=body.action,
        asset_id=asset.id,
        status=asset.status,
        current_version_id=asset.current_version_id,
        note=note,
    )
