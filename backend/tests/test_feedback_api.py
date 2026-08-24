"""POST /feedback/useful — M1 的最小闭环：非作者成功复用把 DRAFT 升为 VERIFIED。

对应硬规则 1/3：AI 与自动路径不产出可信状态；升级证据必须来自非作者。
"""
from app.models import (
    KnowledgeAsset,
    ReuseEvent,
    ReviewTask,
    Status,
    StatusTransition,
    Trigger,
    UserFeedback,
    ValidationRecord,
)
from app.services import state_machine

from .conftest import publish


def _useful(client, asset_id, user, task_note="客户环境部署"):
    resp = client.post(
        "/api/v1/feedback/useful",
        json={"asset_id": asset_id, "task_note": task_note},
        headers={"X-User": user},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_nonauthor_useful_promotes_draft_to_verified(client, db):
    asset_id = publish(client, user="chenyuwei")["id"]

    out = _useful(client, asset_id, user="wanglei")

    assert out["promoted"] is True
    assert out["status"] == "VERIFIED"
    assert out["reuse_count"] == 1

    asset = db.get(KnowledgeAsset, asset_id)
    assert asset.status is Status.VERIFIED
    assert asset.reuse_count == 1

    # 复用事件 + 验证记录 + 用户反馈都落了库
    reuse = db.query(ReuseEvent).filter_by(asset_id=asset_id).one()
    assert reuse.user_id == "wanglei" and reuse.outcome == "success"
    assert reuse.version_id == asset.current_version_id
    assert reuse.fw_version_at_use == "v0.10.0rc1"      # 框架版本自动带出，不用用户填

    validation = db.query(ValidationRecord).filter_by(asset_id=asset_id).one()
    assert validation.validator_id == "wanglei" != asset.author_id
    assert validation.kind == "reuse_success" and validation.result == "pass"

    assert db.query(UserFeedback).filter_by(asset_id=asset_id, kind="useful").count() == 1

    # 审计流水：→DRAFT 之后多一条 DRAFT→VERIFIED，证据指向复用事件
    rows = db.query(StatusTransition).filter_by(asset_id=asset_id).order_by(StatusTransition.id).all()
    assert [r.to_status for r in rows] == [Status.DRAFT, Status.VERIFIED]
    assert rows[1].trigger.value == "nonauthor_reuse"
    assert rows[1].evidence_type == "reuse_event" and rows[1].evidence_id == reuse.id
    assert rows[1].actor == "wanglei"


def test_author_useful_records_reuse_but_never_promotes(client, db):
    asset_id = publish(client, user="chenyuwei")["id"]

    out = _useful(client, asset_id, user="chenyuwei")

    assert out["promoted"] is False
    assert out["status"] == "DRAFT"
    assert out["reuse_count"] == 1
    assert "作者本人" in out["note"]

    asset = db.get(KnowledgeAsset, asset_id)
    assert asset.status is Status.DRAFT
    assert db.query(ReuseEvent).filter_by(asset_id=asset_id).count() == 1   # 复用照记
    assert db.query(ValidationRecord).filter_by(asset_id=asset_id).count() == 0
    assert db.query(StatusTransition).filter_by(asset_id=asset_id).count() == 1


def test_second_useful_on_verified_asset_only_counts_reuse(client, db):
    asset_id = publish(client, user="chenyuwei")["id"]
    _useful(client, asset_id, user="wanglei")

    out = _useful(client, asset_id, user="lihao", task_note="CI 环境升级")

    assert out["promoted"] is False and out["status"] == "VERIFIED"
    assert out["reuse_count"] == 2
    # 已是 VERIFIED，不应再产生新的状态流转
    assert db.query(StatusTransition).filter_by(asset_id=asset_id).count() == 2
    assert db.query(ReuseEvent).filter_by(asset_id=asset_id).count() == 2


def test_useful_on_unknown_asset_404(client):
    resp = client.post("/api/v1/feedback/useful", json={"asset_id": 9999}, headers={"X-User": "wanglei"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_useful_without_task_note_is_allowed(client, db):
    """低负担：任务说明可留空，一次点击即可完成反馈。"""
    asset_id = publish(client, user="chenyuwei")["id"]
    out = _useful(client, asset_id, user="wanglei", task_note="")
    assert out["promoted"] is True
    validation = db.query(ValidationRecord).filter_by(asset_id=asset_id).one()
    assert "未填写任务说明" in validation.note


def test_reuse_count_increment_is_atomic(client, db):
    """自增走 UPDATE ... SET reuse_count = reuse_count + 1，不是读改写。"""
    asset_id = publish(client, user="chenyuwei")["id"]
    for user in ("wanglei", "lihao", "sunxiaodong"):
        _useful(client, asset_id, user=user)
    assert db.get(KnowledgeAsset, asset_id).reuse_count == 3
    assert db.query(ReuseEvent).filter_by(asset_id=asset_id).count() == 3


def test_oversized_x_user_rejected_with_422(client):
    asset_id = publish(client, user="chenyuwei")["id"]
    resp = client.post("/api/v1/feedback/useful", json={"asset_id": asset_id},
                       headers={"X-User": "u" * 65})
    assert resp.status_code == 422


# ---------- POST /feedback/stale（M3）：一次点击把资产送进复核队列 ----------

def _stale(client, asset_id, user, note="", expect=200):
    resp = client.post(
        "/api/v1/feedback/stale",
        json={"asset_id": asset_id, "note": note},
        headers={"X-User": user},
    )
    assert resp.status_code == expect, resp.text
    return resp.json()


def test_stale_moves_verified_asset_to_review_due(client, db):
    asset_id = publish(client, user="chenyuwei")["id"]
    _useful(client, asset_id, user="wanglei")                    # 先升到 VERIFIED

    out = _stale(client, asset_id, user="sunxiaodong", note="0.10 起 EP 配置改成一级参数")

    assert out["status"] == "REVIEW_DUE" and out["merged"] is False
    asset = db.get(KnowledgeAsset, asset_id)
    db.refresh(asset)
    assert asset.status is Status.REVIEW_DUE

    task = db.query(ReviewTask).filter_by(asset_id=asset_id).one()
    assert task.id == out["review_task_id"]
    assert task.trigger is Trigger.user_feedback and task.state == "open"
    assert "sunxiaodong" in task.trigger_detail and "EP 配置" in task.trigger_detail

    fb = db.query(UserFeedback).filter_by(asset_id=asset_id, kind="maybe_stale").one()
    assert fb.user_id == "sunxiaodong"

    # 审计流水：→DRAFT →VERIFIED →REVIEW_DUE，最后一条的证据指向复核任务
    rows = db.query(StatusTransition).filter_by(asset_id=asset_id).order_by(StatusTransition.id).all()
    assert [r.to_status for r in rows] == [Status.DRAFT, Status.VERIFIED, Status.REVIEW_DUE]
    assert rows[2].trigger is Trigger.user_feedback
    assert rows[2].evidence_type == "review_task" and rows[2].evidence_id == task.id
    assert rows[2].actor == "sunxiaodong"
    assert asset.status_reason and "内容可能过时" in asset.status_reason


def test_stale_on_draft_also_opens_task(client, db):
    """DRAFT 同样能被反馈过时 —— 状态机允许 DRAFT→REVIEW_DUE（design.md §4）。"""
    asset_id = publish(client, user="chenyuwei")["id"]

    out = _stale(client, asset_id, user="wanglei")

    assert out["status"] == "REVIEW_DUE"
    assert db.query(ReviewTask).filter_by(asset_id=asset_id).count() == 1


def test_second_stale_within_debounce_window_merges_into_one_task(client, db):
    """同一份资产被两个人先后反馈，队列里只能有一条任务（24h 去抖）。"""
    asset_id = publish(client, user="chenyuwei")["id"]
    first = _stale(client, asset_id, user="wanglei", note="命令跑不通")

    second = _stale(client, asset_id, user="lihao", note="参数名变了")

    assert second["merged"] is True
    assert second["review_task_id"] == first["review_task_id"]
    assert "已在复核队列" in second["note"]

    task = db.query(ReviewTask).filter_by(asset_id=asset_id).one()   # 仍然只有一条
    assert "[合并]" in task.trigger_detail and "lihao" in task.trigger_detail
    # 两次反馈都留了事件；状态只流转了一次
    assert db.query(UserFeedback).filter_by(asset_id=asset_id, kind="maybe_stale").count() == 2
    assert db.query(StatusTransition).filter_by(
        asset_id=asset_id, to_status=Status.REVIEW_DUE).count() == 1


def test_stale_on_archived_asset_409(client, db):
    asset_id = publish(client, user="chenyuwei")["id"]
    asset = db.get(KnowledgeAsset, asset_id)
    state_machine.transition(db, asset, Status.ARCHIVED, Trigger.review_replace,
                             actor="wanglei", note="被 KA-002 替代")
    db.commit()

    body = _stale(client, asset_id, user="wanglei", expect=409)

    assert body["error"]["code"] == "ASSET_NOT_ACTIVE"
    assert db.query(ReviewTask).filter_by(asset_id=asset_id).count() == 0


def test_stale_on_unknown_asset_404(client):
    body = _stale(client, 9999, user="wanglei", expect=404)
    assert body["error"]["code"] == "NOT_FOUND"


def test_feedback_with_unknown_search_event_422(client):
    """search_event_id 是外键，编错了不能变成 500。"""
    asset_id = publish(client, user="chenyuwei")["id"]
    resp = client.post("/api/v1/feedback/useful",
                       json={"asset_id": asset_id, "search_event_id": 4242},
                       headers={"X-User": "wanglei"})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "VALIDATION_ERROR"
