"""复核队列：创建（含 24h 去抖）、优先级、AI 摘要/草稿回填、四选一处理、按需治理过滤。

处理动作与状态机的映射（docs/design.md §4）：
    confirm      -> REVIEW_DUE→回到进入前的状态（写 ValidationRecord(review_confirm)）
                    从 VERIFIED 进来的恢复 VERIFIED；从 DRAFT 进来的只回 DRAFT ——
                    复核回答的是「变更是否影响了这份知识」，不是「知识对不对」，
                    确认「未受影响」不构成验证证据，不能替一份从未被验证的资产升级。
    accept_draft -> 切到已生成的 AssetVersion(ai_draft) + REVIEW_DUE→DRAFT（绝不直达 VERIFIED）
    stale        -> REVIEW_DUE→STALE（写 ValidationRecord(result=stale_confirm)）
    archive      -> →ARCHIVED（note 填替代资产回链）
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    AssetVersion,
    KnowledgeAsset,
    ReuseEvent,
    ReviewTask,
    Status,
    StatusTransition,
    Tier,
    Trigger,
    UserFeedback,
    ValidationRecord,
    VersionOrigin,
    utcnow,
)
from . import ai, indexing, state_machine

# 复核队列展示与证据文案用的触发器中文名（与前端 Review 页一致）
TRIGGER_ZH = {
    Trigger.code_change: "代码变更",
    Trigger.version_change: "版本变更",
    Trigger.user_feedback: "人工反馈",
}

# priority = max(近30天复用次数, 1) × 风险系数(core=3, 高风险标签=2, 其他=1)。
# 保底 1：能被变更命中/被人反馈就说明至少有人在关注，不该压成 0 一刀切排到最后。
# 标签阈值（高≥8 / 中≥2）与 scripts/seed.py 的 PRIORITIES 档位对齐，调整要同步两处。
PRIORITY_HIGH = 8
PRIORITY_MID = 2

RISK_CORE = 3
RISK_TAGGED = 2


def _high_risk_tags() -> set[str]:
    return {t.strip() for t in settings.high_risk_tags.split(",") if t.strip()}


def compute_priority(db: Session, asset: KnowledgeAsset) -> int:
    since = utcnow() - timedelta(days=30)
    usage = db.scalar(
        select(func.count()).select_from(ReuseEvent)
        .where(ReuseEvent.asset_id == asset.id, ReuseEvent.at >= since)
    ) or 0
    if asset.tier is Tier.core:
        risk = RISK_CORE
    elif _high_risk_tags() & set(asset.tags or []):
        risk = RISK_TAGGED
    else:
        risk = 1
    return max(usage, 1) * risk


def priority_label(priority: int) -> str:
    if priority >= PRIORITY_HIGH:
        return "高"
    if priority >= PRIORITY_MID:
        return "中"
    return "低"


def usage_counts(db: Session, asset_ids: list[int], *, days: int = 30) -> dict[int, int]:
    """近 N 天复用次数（复核队列每行的「近 30 天复用」）。"""
    if not asset_ids:
        return {}
    since = utcnow() - timedelta(days=days)
    rows = db.execute(
        select(ReuseEvent.asset_id, func.count())
        .where(ReuseEvent.asset_id.in_(asset_ids), ReuseEvent.at >= since)
        .group_by(ReuseEvent.asset_id)
    ).all()
    return dict(rows)


def open_task(
    db: Session,
    asset: KnowledgeAsset,
    trigger: Trigger,
    *,
    trigger_detail: str,
    actor: str = "system",
    diff_ref: str = "",
    priority: int | None = None,
) -> tuple[ReviewTask, bool]:
    """将资产置 REVIEW_DUE 并建任务。返回 (任务, 是否新建)。

    同资产在去抖窗口内已有 open 任务时不新建，只把这次的触发说明并进那条任务
    （created=False）—— 一次代码变更命中多个 watch 路径、或几个人先后反馈同一份资产
    过时，都不该在队列里堆出几条要人分别处理的任务。

    资产已是 REVIEW_DUE（或 STALE/ARCHIVED）时不再流转状态，只挂任务。
    priority 省略时按 compute_priority 现算（近 30 天复用 × 风险系数）。
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
        diff_ref=diff_ref,
        priority=compute_priority(db, asset) if priority is None else priority,
    )
    db.add(task)
    db.flush()
    if asset.status in (Status.DRAFT, Status.VERIFIED):
        state_machine.transition(
            db, asset, Status.REVIEW_DUE, trigger, actor=actor,
            evidence_type="review_task", evidence_id=task.id, note=trigger_detail,
        )
    return task, True


def attach_ai_review(db: Session, asset: KnowledgeAsset, task: ReviewTask, change_text: str) -> None:
    """给复核任务回填 AI 影响摘要与更新草稿（可降级：网关不可用就留空，任务照常存在）。

    草稿落成 AssetVersion(created_from=ai_draft) 但**不改 current_version_id** ——
    AI 只到草稿为止（硬规则 1），采不采纳等复核四选一（resolve）。
    已有摘要/草稿的任务不重复生成：去抖合并把新触发并进旧任务时只花一次网关钱；
    反过来，上次网关瞬断漏生成的，这次触发会补上。
    """
    if task.ai_impact_summary and task.ai_draft_version_id:
        return
    body = indexing.body_md_of(db, asset)
    if not task.ai_impact_summary:
        summary = ai.impact_summary(body, change_text)
        if summary:
            task.ai_impact_summary = summary
    if task.ai_draft_version_id is None:
        draft = ai.update_draft(body, change_text)
        if draft:
            seq = (db.scalar(
                select(func.max(AssetVersion.seq)).where(AssetVersion.asset_id == asset.id)
            ) or 0) + 1
            version = AssetVersion(
                asset_id=asset.id, seq=seq, body_md=draft,
                change_note=f"AI 更新草稿（{TRIGGER_ZH.get(task.trigger, task.trigger.value)}，待复核采纳）",
                created_by="ai", created_from=VersionOrigin.ai_draft,
            )
            db.add(version)
            db.flush()
            task.ai_draft_version_id = version.id
    db.flush()


def governed_open_tasks(db: Session) -> list[tuple[ReviewTask, KnowledgeAsset]]:
    """open 任务按 priority 降序，且只保留值得占用人工时间的（design.md §4 按需治理）：

    近 90 天有使用 / tier=core / 高风险标签，三者满足其一。其余 REVIEW_DUE 只在搜索里
    降权，不进队列打扰人。「有使用」以 ReuseEvent ∪ UserFeedback 近似 —— 点击流水
    （SearchEvent.clicked_ids）MVP 前端尚未上报，反馈本身就是最强的「有人在用」信号。
    """
    rows = db.execute(
        select(ReviewTask, KnowledgeAsset)
        .join(KnowledgeAsset, ReviewTask.asset_id == KnowledgeAsset.id)
        # 资产必须仍在 REVIEW_DUE（原型行为）：历史数据可能有资产已离开 REVIEW_DUE
        # 但任务还 open 的记录，列出来四选一只会 409。
        .where(ReviewTask.state == "open", KnowledgeAsset.status == Status.REVIEW_DUE)
        .order_by(ReviewTask.priority.desc(), ReviewTask.created_at, ReviewTask.id)
    ).all()
    if not rows:
        return []
    since = utcnow() - timedelta(days=settings.governance_usage_days)
    ids = [asset.id for _, asset in rows]
    used = set(db.scalars(
        select(ReuseEvent.asset_id).where(ReuseEvent.asset_id.in_(ids), ReuseEvent.at >= since)
    ).all())
    used |= set(db.scalars(
        select(UserFeedback.asset_id).where(UserFeedback.asset_id.in_(ids), UserFeedback.at >= since)
    ).all())
    risky = _high_risk_tags()
    return [
        (task, asset) for task, asset in rows
        if asset.tier is Tier.core or (risky & set(asset.tags or [])) or asset.id in used
    ]


def _status_before_review(db: Session, asset: KnowledgeAsset) -> Status:
    """进入 REVIEW_DUE 前的状态（confirm 要恢复到哪）。查最近一条 to=REVIEW_DUE 的流水。"""
    prior = db.scalar(
        select(StatusTransition.from_status)
        .where(StatusTransition.asset_id == asset.id,
               StatusTransition.to_status == Status.REVIEW_DUE)
        .order_by(StatusTransition.at.desc(), StatusTransition.id.desc())
        .limit(1)
    )
    return prior if prior in (Status.VERIFIED, Status.DRAFT) else Status.VERIFIED


def resolve(
    db: Session,
    task: ReviewTask,
    *,
    action: str,
    actor: str,
    note: str = "",
    replaced_by: KnowledgeAsset | None = None,
) -> str:
    """四选一处理。返回给前端 toast 的结果说明；非法流转由状态机抛 InvalidTransition（409）。

    调用方（api/review.py）负责先挡住：task 已 done、accept_draft 但没有草稿、
    replaced_by 不存在。这里默认前置条件成立，专注执行与留证。
    """
    asset = db.get(KnowledgeAsset, task.asset_id)
    code = f"KA-{asset.id:03d}"
    trigger_zh = TRIGGER_ZH.get(task.trigger, task.trigger.value)

    if action == "confirm":
        target = _status_before_review(db, asset)
        validation = ValidationRecord(
            asset_id=asset.id, version_id=asset.current_version_id, validator_id=actor,
            kind="review_confirm", result="pass",
            note=note or f"复核确认：针对「{trigger_zh}」核对后内容仍然有效",
        )
        db.add(validation)
        db.flush()
        restored = ("恢复 VERIFIED" if target is Status.VERIFIED
                    else "回到 DRAFT（尚未验证，仍需非作者复用）")
        state_machine.transition(
            db, asset, target, Trigger.review_confirm, actor=actor,
            evidence_type="validation", evidence_id=validation.id,
            note=f"复核确认仍有效（{actor}）。",
        )
        result = f"{code} 复核通过 → {restored}，验证记录已留痕。"
    elif action == "accept_draft":
        draft = db.get(AssetVersion, task.ai_draft_version_id)
        asset.current_version_id = draft.id
        state_machine.transition(
            db, asset, Status.DRAFT, Trigger.review_accept_draft, actor=actor,
            evidence_type="asset_version", evidence_id=draft.id,
            note=f"已按 AI 草稿更新为 v{draft.seq}，等待非作者复用后可重新升级 VERIFIED。",
        )
        # 正文换了，检索索引必须同事务跟上，否则更新完的内容搜不到（backend/CLAUDE.md 的坑 1）
        indexing.refresh_doc(db, asset, body_md=draft.body_md)
        indexing.refresh_embedding(db, asset, body_md=draft.body_md)
        result = f"{code} 已按 AI 草稿更新为 v{draft.seq}（DRAFT · 尚未验证），不会被自动标记为 VERIFIED。"
    elif action == "stale":
        validation = ValidationRecord(
            asset_id=asset.id, version_id=asset.current_version_id, validator_id=actor,
            kind="review_confirm", result="stale_confirm",
            note=note or "复核确认失效",
        )
        db.add(validation)
        db.flush()
        state_machine.transition(
            db, asset, Status.STALE, Trigger.review_stale, actor=actor,
            evidence_type="validation", evidence_id=validation.id,
            note=f"复核确认失效（{actor}）：{note or task.trigger_detail.splitlines()[0]}",
        )
        result = f"{code} 已标记 STALE，退出正常搜索，仅历史入口可见。"
    elif action == "archive":
        replace_note = f"已被 KA-{replaced_by.id:03d}「{replaced_by.title}」替代" if replaced_by else "已被更新的资产替代"
        if note:
            replace_note = f"{replace_note}：{note}"
        state_machine.transition(
            db, asset, Status.ARCHIVED, Trigger.review_replace, actor=actor,
            evidence_type="review_task", evidence_id=task.id,
            note=f"复核确认{replace_note}（{actor}）。",
        )
        result = f"{code} 已归档。" + (f"替代资产：KA-{replaced_by.id:03d}。" if replaced_by else "")
    else:  # pragma: no cover - schema 层 Literal 已挡住
        raise ValueError(f"未知的复核动作 {action}")

    now = utcnow()
    task.state, task.handled_by, task.action, task.handled_at = "done", actor, action, now
    # 同资产可能还有跨去抖窗口的其它 open 任务；资产已离开 REVIEW_DUE，留着它们
    # 只会让下一个人点出 409。随本次处理一并关闭并留痕。
    siblings = db.scalars(
        select(ReviewTask).where(
            ReviewTask.asset_id == asset.id,
            ReviewTask.state == "open",
            ReviewTask.id != task.id,
        )
    ).all()
    for sibling in siblings:
        sibling.state, sibling.handled_by, sibling.action, sibling.handled_at = "done", actor, action, now
        sibling.trigger_detail = f"{sibling.trigger_detail}\n[随任务 #{task.id} 一并处理]"
    db.flush()
    return result
