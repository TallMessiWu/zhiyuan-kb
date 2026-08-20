"""POST /feedback/useful — M1 的最小闭环：非作者成功复用把 DRAFT 升为 VERIFIED。

对应硬规则 1/3：AI 与自动路径不产出可信状态；升级证据必须来自非作者。
"""
from app.models import KnowledgeAsset, ReuseEvent, Status, StatusTransition, UserFeedback, ValidationRecord

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
