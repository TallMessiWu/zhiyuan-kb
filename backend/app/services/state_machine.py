"""五态状态机 — 修改资产状态的唯一合法入口。

规则来源：docs/design.md §4。要点：
- 每次流转同事务追加 StatusTransition（证据、执行者），禁止绕过本模块改 status。
- DRAFT→VERIFIED 必须由非作者的复用/验证证据触发（validator != author 强校验）。
- 接受 AI 更新草稿只能把资产送回 DRAFT，绝不直达 VERIFIED。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import KnowledgeAsset, Status, StatusTransition, Trigger

# 合法流转表：(from, to) -> 允许的触发器集合
ALLOWED: dict[tuple[Status, Status], set[Trigger]] = {
    (Status.DRAFT, Status.VERIFIED): {Trigger.nonauthor_reuse, Trigger.manual_validation},
    (Status.DRAFT, Status.REVIEW_DUE): {Trigger.code_change, Trigger.version_change, Trigger.user_feedback},
    (Status.VERIFIED, Status.REVIEW_DUE): {Trigger.code_change, Trigger.version_change, Trigger.user_feedback},
    (Status.REVIEW_DUE, Status.VERIFIED): {Trigger.review_confirm},
    (Status.REVIEW_DUE, Status.DRAFT): {Trigger.review_accept_draft},
    (Status.REVIEW_DUE, Status.STALE): {Trigger.review_stale},
    (Status.DRAFT, Status.ARCHIVED): {Trigger.review_replace},
    (Status.VERIFIED, Status.ARCHIVED): {Trigger.review_replace},
    (Status.REVIEW_DUE, Status.ARCHIVED): {Trigger.review_replace},
    (Status.STALE, Status.ARCHIVED): {Trigger.review_replace},
}

# AI 相关触发器永远不允许产出 VERIFIED（防御性双保险，见根 CLAUDE.md 硬规则 1）
_FORBIDDEN_TO_VERIFIED = {Trigger.review_accept_draft, Trigger.auto_create,
                          Trigger.code_change, Trigger.version_change, Trigger.user_feedback}


class InvalidTransition(Exception):
    """非法流转；API 层映射为 409 INVALID_TRANSITION。"""


def transition(
    db: Session,
    asset: KnowledgeAsset,
    to: Status,
    trigger: Trigger,
    *,
    actor: str = "system",
    evidence_type: str = "",
    evidence_id: int | None = None,
    note: str = "",
) -> StatusTransition:
    """执行一次状态流转：校验合法性 → 更新当前态 → 追加审计流水。"""
    frm = asset.status
    if to == Status.VERIFIED and trigger in _FORBIDDEN_TO_VERIFIED:
        raise InvalidTransition(f"trigger {trigger.value} 不允许产出 VERIFIED")
    allowed = ALLOWED.get((frm, to))
    if not allowed or trigger not in allowed:
        raise InvalidTransition(f"{frm.value} → {to.value} (trigger={trigger.value}) 不在允许表中")
    if to == Status.VERIFIED and trigger in {Trigger.nonauthor_reuse, Trigger.manual_validation}:
        if actor == asset.author_id:
            raise InvalidTransition("DRAFT→VERIFIED 的证据提供者不能是作者本人")
        if not evidence_type:
            raise InvalidTransition("升级 VERIFIED 必须携带证据（evidence_type/evidence_id）")

    asset.status = to
    if note:
        asset.status_reason = note
    row = StatusTransition(
        asset_id=asset.id, from_status=frm, to_status=to, trigger=trigger,
        evidence_type=evidence_type, evidence_id=evidence_id, actor=actor, note=note,
    )
    db.add(row)
    db.flush()
    return row


def create_as_draft(db: Session, asset: KnowledgeAsset, *, actor: str, note: str = "") -> StatusTransition:
    """新资产入库：状态置 DRAFT 并记录首条流水。"""
    asset.status = Status.DRAFT
    row = StatusTransition(
        asset_id=asset.id, from_status=None, to_status=Status.DRAFT,
        trigger=Trigger.auto_create, actor=actor, note=note or "发布为 DRAFT",
    )
    db.add(row)
    db.flush()
    return row
