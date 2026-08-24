"""五态状态机规则测试 — sqlite 内存库，不依赖 PG。

覆盖 docs/design.md §4 的关键规则：
- 非作者复用才能升 VERIFIED；作者本人被拒
- 接受 AI 草稿只能回 DRAFT，任何 AI/自动触发器直达 VERIFIED 被拒
- 每次流转都追加 StatusTransition 流水
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KnowledgeAsset, Direction, Status, StatusTransition, Trigger
from app.services import state_machine as sm


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def draft_asset(db):
    asset = KnowledgeAsset(title="测试资产", direction=Direction.feature, author_id="chenyuwei")
    db.add(asset)
    db.flush()
    sm.create_as_draft(db, asset, actor="chenyuwei")
    return asset


def test_new_asset_is_draft_with_audit(db, draft_asset):
    assert draft_asset.status == Status.DRAFT
    rows = db.query(StatusTransition).filter_by(asset_id=draft_asset.id).all()
    assert len(rows) == 1 and rows[0].to_status == Status.DRAFT


def test_nonauthor_reuse_promotes_to_verified(db, draft_asset):
    sm.transition(db, draft_asset, Status.VERIFIED, Trigger.nonauthor_reuse,
                  actor="wanglei", evidence_type="reuse_event", evidence_id=1)
    assert draft_asset.status == Status.VERIFIED


def test_author_reuse_rejected(db, draft_asset):
    with pytest.raises(sm.InvalidTransition):
        sm.transition(db, draft_asset, Status.VERIFIED, Trigger.nonauthor_reuse,
                      actor="chenyuwei", evidence_type="reuse_event", evidence_id=1)


def test_verified_requires_evidence(db, draft_asset):
    with pytest.raises(sm.InvalidTransition):
        sm.transition(db, draft_asset, Status.VERIFIED, Trigger.nonauthor_reuse, actor="wanglei")


def test_feedback_sends_to_review_due(db, draft_asset):
    sm.transition(db, draft_asset, Status.VERIFIED, Trigger.nonauthor_reuse,
                  actor="wanglei", evidence_type="reuse_event", evidence_id=1)
    sm.transition(db, draft_asset, Status.REVIEW_DUE, Trigger.user_feedback,
                  actor="sunxiaodong", evidence_type="user_feedback", evidence_id=2)
    assert draft_asset.status == Status.REVIEW_DUE


def test_accept_ai_draft_goes_back_to_draft_never_verified(db, draft_asset):
    sm.transition(db, draft_asset, Status.REVIEW_DUE, Trigger.code_change,
                  actor="system", evidence_type="review_task", evidence_id=3)
    # 接受草稿 -> DRAFT 合法
    sm.transition(db, draft_asset, Status.DRAFT, Trigger.review_accept_draft,
                  actor="wanglei", evidence_type="asset_version", evidence_id=9)
    assert draft_asset.status == Status.DRAFT
    # 任何路径下 accept_draft 直达 VERIFIED 都被拒
    with pytest.raises(sm.InvalidTransition):
        sm.transition(db, draft_asset, Status.VERIFIED, Trigger.review_accept_draft, actor="wanglei")


def test_review_confirm_restores_verified(db, draft_asset):
    sm.transition(db, draft_asset, Status.REVIEW_DUE, Trigger.version_change,
                  actor="system", evidence_type="review_task", evidence_id=4)
    sm.transition(db, draft_asset, Status.VERIFIED, Trigger.review_confirm,
                  actor="wanglei", evidence_type="validation", evidence_id=5)
    assert draft_asset.status == Status.VERIFIED


def test_review_confirm_can_return_to_draft(db, draft_asset):
    """从 DRAFT 进入 REVIEW_DUE 的资产，复核确认「未受影响」只能回 DRAFT ——
    复核不判断知识对错，确认不构成验证证据（M4，与 review_queue.resolve 配套的边）。"""
    sm.transition(db, draft_asset, Status.REVIEW_DUE, Trigger.code_change,
                  actor="system", evidence_type="review_task", evidence_id=8)
    sm.transition(db, draft_asset, Status.DRAFT, Trigger.review_confirm,
                  actor="wanglei", evidence_type="validation", evidence_id=9)
    assert draft_asset.status == Status.DRAFT


def test_stale_then_archive(db, draft_asset):
    sm.transition(db, draft_asset, Status.REVIEW_DUE, Trigger.code_change,
                  actor="system", evidence_type="review_task", evidence_id=6)
    sm.transition(db, draft_asset, Status.STALE, Trigger.review_stale,
                  actor="wanglei", evidence_type="validation", evidence_id=7)
    sm.transition(db, draft_asset, Status.ARCHIVED, Trigger.review_replace,
                  actor="wanglei", note="被 KA-016 替代")
    assert draft_asset.status == Status.ARCHIVED
    assert db.query(StatusTransition).filter_by(asset_id=draft_asset.id).count() == 4


def test_illegal_jump_rejected(db, draft_asset):
    with pytest.raises(sm.InvalidTransition):
        sm.transition(db, draft_asset, Status.STALE, Trigger.user_feedback, actor="wanglei")
