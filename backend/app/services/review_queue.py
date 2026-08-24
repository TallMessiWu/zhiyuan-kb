"""复核队列：创建（含 24h 去抖）、优先级、四选一处理。

处理动作与状态机的映射（docs/design.md §4）：
    confirm      -> REVIEW_DUE→VERIFIED（写 ValidationRecord(review_confirm)）
    accept_draft -> 新 AssetVersion(ai_draft) + REVIEW_DUE→DRAFT
    stale        -> REVIEW_DUE→STALE（写 ValidationRecord(stale_confirm)）
    archive      -> →ARCHIVED（note 填替代资产回链）
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import KnowledgeAsset, ReviewTask, Status, Trigger, utcnow
from . import state_machine


def open_task(
    db: Session,
    asset: KnowledgeAsset,
    trigger: Trigger,
    *,
    trigger_detail: str,
    actor: str = "system",
    diff_ref: str = "",
    priority: int = 0,
) -> tuple[ReviewTask, bool]:
    """将资产置 REVIEW_DUE 并建任务。返回 (任务, 是否新建)。

    同资产在去抖窗口内已有 open 任务时不新建，只把这次的触发说明并进那条任务
    （created=False）—— 一次代码变更命中多个 watch 路径、或几个人先后反馈同一份资产
    过时，都不该在队列里堆出几条要人分别处理的任务。

    资产已是 REVIEW_DUE（或 STALE/ARCHIVED）时不再流转状态，只挂任务。
    """
    window = utcnow() - timedelta(hours=settings.review_debounce_hours)
    existing = db.scalar(
        select(ReviewTask).where(
            ReviewTask.asset_id == asset.id,
            ReviewTask.state == "open",
            ReviewTask.created_at >= window,
        )
    )
    if existing:
        existing.trigger_detail = f"{existing.trigger_detail}\n[合并] {trigger_detail}"
        db.flush()
        return existing, False

    task = ReviewTask(
        asset_id=asset.id, trigger=trigger, trigger_detail=trigger_detail,
        diff_ref=diff_ref, priority=priority,
    )
    db.add(task)
    db.flush()
    if asset.status in (Status.DRAFT, Status.VERIFIED):
        state_machine.transition(
            db, asset, Status.REVIEW_DUE, trigger, actor=actor,
            evidence_type="review_task", evidence_id=task.id, note=trigger_detail,
        )
    return task, True


# TODO(M4): compute_priority(asset) = 近30天复用次数 × 风险系数(core=3, 高风险标签=2, 其他=1)
# TODO(M4): resolve(db, task, action, actor, note) —— 四选一，调 state_machine + 写证据记录
# TODO(M4): 按需治理过滤 —— 队列查询只返回「近90天有使用 / core / 高风险」的 open 任务（design.md §4）
