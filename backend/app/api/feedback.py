"""三键反馈：有用（M1）· 内容可能过时（M3）· 没有找到答案（M3）。

三键都必须是「一次点击就完成」（根 CLAUDE.md 硬规则 6），所以除了「有用」那一个可选的
任务说明，没有任何一键会要求用户填表；版本、时间、使用者一律由服务端带出。

三键各自产出的东西不同，但都落 UserFeedback 事件：
    useful     → ReuseEvent(+ValidationRecord) → 非作者时 DRAFT→VERIFIED
    stale      → ReviewTask(user_feedback)     → DRAFT/VERIFIED→REVIEW_DUE
    not-found  → KnowledgeGap 建新或累计       （不碰任何资产状态）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    AssetFramework,
    KnowledgeAsset,
    ReuseEvent,
    SearchEvent,
    Status,
    Trigger,
    UserFeedback,
    ValidationRecord,
)
from ..schemas import GapOut, NotFoundIn, NotFoundOut, StaleIn, StaleOut, UsefulIn, UsefulOut
from ..services import gaps as gaps_service
from ..services import review_queue, state_machine
from .assets import USER_ID_MAX, load_asset

router = APIRouter()


def _check_search_event(db: Session, search_event_id: int | None) -> None:
    """反馈可以挂在一次搜索上（看板要用这条链路把需求事件和复用事件对上）。

    这里要显式校验：search_event_id 是外键，编错了在 PG 上是 IntegrityError → 500，
    对调用方就是一个没法自查的错误。
    """
    if search_event_id is None:
        return
    if db.get(SearchEvent, search_event_id) is None:
        raise HTTPException(422, detail=("VALIDATION_ERROR", f"搜索事件 {search_event_id} 不存在"))


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
    _check_search_event(db, body.search_event_id)
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


@router.post("/feedback/stale", response_model=StaleOut)
def stale(body: StaleIn, db: Session = Depends(get_db),
          x_user: str = Header(default="anonymous", max_length=USER_ID_MAX)):
    """「内容可能过时」：置 REVIEW_DUE 并进复核队列（design.md §4 的第三类流转）。

    这一键是普通成员唯一能主动降低一份资产可信度的入口，所以它只把资产送进 REVIEW_DUE
    ——「确认失效」是复核环节的人工判断（M4），一次反馈不能直接把知识判死。

    去抖窗口内的重复反馈并进同一条复核任务（merged=True），队列里不会因为三个人先后
    点了同一份资产就多出三条要分别处理的任务。
    """
    asset = load_asset(db, body.asset_id)
    if asset.status in (Status.STALE, Status.ARCHIVED):
        # 已经是死状态：搜索里本来就见不到它，再建复核任务只是给队列添噪音。
        raise HTTPException(409, detail=(
            "ASSET_NOT_ACTIVE", f"资产已是 {asset.status.value}，不需要再反馈「内容可能过时」"))

    note = body.note.strip()
    feedback = UserFeedback(user_id=x_user, asset_id=asset.id, kind="maybe_stale", note=note)
    db.add(feedback)
    db.flush()

    detail = f"{x_user} 反馈「内容可能过时」（原状态 {asset.status.value}）"
    if note:
        detail = f"{detail}：{note}"
    task, created = review_queue.open_task(
        db, asset, Trigger.user_feedback, trigger_detail=detail, actor=x_user,
    )

    db.commit()
    db.refresh(asset)
    return StaleOut(
        feedback_id=feedback.id,
        asset_id=asset.id,
        status=asset.status,
        review_task_id=task.id,
        merged=not created,
        note=("该资产已在复核队列中，本次反馈已并入现有任务。" if not created
              else "已加入复核队列：搜索中它将降权并标注「可能过时」。"),
    )


@router.post("/feedback/not-found", response_model=NotFoundOut)
def not_found(body: NotFoundIn, db: Session = Depends(get_db),
              x_user: str = Header(default="anonymous", max_length=USER_ID_MAX)):
    """「没有找到答案」：记一个知识缺口，同一个需求累计到同一条上（services/gaps.py）。

    这一键不碰任何资产 —— 它说的是「库里缺这份知识」，不是「这份资产不好」。
    缺口同时是看板有效复用率的分母之一（design.md §9），所以哪怕本次搜索有结果，
    用户说没解决问题也要记：需求事件确实发生了。
    """
    _check_search_event(db, body.search_event_id)
    question = body.query.strip() or gaps_service.BROWSE_QUERY

    feedback = UserFeedback(
        user_id=x_user, search_event_id=body.search_event_id,
        kind="not_found", note=question[:500],
    )
    db.add(feedback)
    db.flush()

    gap, created = gaps_service.record(db, question=question, user_id=x_user)

    db.commit()
    return NotFoundOut(feedback_id=feedback.id, gap=GapOut.model_validate(gap), created=created)
