"""三键反馈。M1 只提前落地「有用，完成任务」这一键，用来跑通 DRAFT→VERIFIED 的最小闭环；
stale / not-found 仍留在 M3。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    AssetFramework,
    KnowledgeAsset,
    ReuseEvent,
    Status,
    Trigger,
    UserFeedback,
    ValidationRecord,
)
from ..schemas import NotFoundIn, StaleIn, UsefulIn, UsefulOut
from ..services import state_machine
from .assets import USER_ID_MAX, load_asset

router = APIRouter()


def _fw_version_at_use(db: Session, asset: KnowledgeAsset) -> str:
    """复用时的框架版本，自动带出（低负担：不让用户填）。"""
    af = db.scalar(select(AssetFramework).where(AssetFramework.asset_id == asset.id).order_by(AssetFramework.id))
    return af.verified_on if af else ""


@router.post("/feedback/useful", response_model=UsefulOut)
def useful(body: UsefulIn, db: Session = Depends(get_db),
           x_user: str = Header(default="anonymous", max_length=USER_ID_MAX)):
    """「有用，完成任务」：记一次成功复用；非作者的这条证据可把 DRAFT 升为 VERIFIED。

    作者本人点也照常记复用事件（复用次数是真实的），但不作为升级证据 —— 校验在状态机里。
    """
    asset = load_asset(db, body.asset_id)
    task_note = body.task_note.strip()

    reuse = ReuseEvent(
        asset_id=asset.id,
        version_id=asset.current_version_id,
        user_id=x_user,
        task_note=task_note,
        outcome="success",
        search_event_id=body.search_event_id,
        fw_version_at_use=_fw_version_at_use(db, asset),
    )
    db.add(reuse)
    db.add(UserFeedback(
        user_id=x_user, asset_id=asset.id, search_event_id=body.search_event_id,
        kind="useful", note=task_note,
    ))
    db.flush()

    # 原子自增：asset.reuse_count += 1 是读改写，两个并发的「有用」会互相覆盖，
    # 让这个冗余计数和 ReuseEvent 事件表长期对不上。
    db.execute(
        update(KnowledgeAsset)
        .where(KnowledgeAsset.id == asset.id)
        .values(reuse_count=KnowledgeAsset.reuse_count + 1)
    )
    db.refresh(asset)

    promoted = False
    note = ""
    if asset.status == Status.DRAFT:
        if x_user == asset.author_id:
            note = "你是作者本人，本次复用不作为升级 VERIFIED 的证据。"
        else:
            # 非作者成功复用 —— 唯一能产出 VERIFIED 的两类证据之一（design.md §4）
            validation = ValidationRecord(
                asset_id=asset.id,
                version_id=asset.current_version_id,
                validator_id=x_user,
                kind="reuse_success",
                result="pass",
                note=f"非作者复用成功：{task_note}" if task_note else "非作者复用成功（未填写任务说明）",
            )
            db.add(validation)
            db.flush()
            state_machine.transition(
                db, asset, Status.VERIFIED, Trigger.nonauthor_reuse,
                actor=x_user, evidence_type="reuse_event", evidence_id=reuse.id,
                note=f"由非作者（{x_user}）成功复用，自动升级为 VERIFIED。",
            )
            promoted = True

    db.commit()
    return UsefulOut(
        reuse_event_id=reuse.id,
        asset_id=asset.id,
        status=asset.status,
        reuse_count=asset.reuse_count,
        promoted=promoted,
        note=note,
    )


@router.post("/feedback/stale")
def stale(body: StaleIn, db: Session = Depends(get_db), x_user: str = Header(default="anonymous")):
    """「内容可能过时」。TODO(M3)：UserFeedback(maybe_stale) + review_queue.open_task(user_feedback)。"""
    raise NotImplementedError("M3")


@router.post("/feedback/not-found")
def not_found(body: NotFoundIn, db: Session = Depends(get_db), x_user: str = Header(default="anonymous")):
    """「没有找到答案」。TODO(M3)：KnowledgeGap 建新/语义相似累计（hit_count/last_at/reporters）。"""
    raise NotImplementedError("M3")
