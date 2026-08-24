"""GET /review 与 POST /review/{id}/resolve：排序、按需治理过滤、四选一流转与各处 409/422。

造 REVIEW_DUE 任务的两条路：
- 走 API：POST /feedback/stale（user_feedback 触发，自带近期 UserFeedback，必过治理过滤）
- 走 service：review_queue.open_task（webhook 语义，资产没有任何使用记录时会被治理过滤掉，
  正好用来验证过滤本身）
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import (
    AssetSearchDoc,
    AssetVersion,
    KnowledgeAsset,
    ReviewTask,
    Status,
    Tier,
    Trigger,
    ValidationRecord,
    VersionOrigin,
    utcnow,
)
from app.services import review_queue

from .conftest import publish

REVIEW = "/api/v1/review"


def stale_task(client, asset_id: int, *, user="sunxiaodong") -> int:
    resp = client.post("/api/v1/feedback/stale", json={"asset_id": asset_id},
                       headers={"X-User": user})
    assert resp.status_code == 200, resp.text
    return resp.json()["review_task_id"]


def make_draft(db, asset_id: int, task_id: int, body_md: str) -> int:
    """给任务手工挂一份 AI 草稿版本（等价于 attach_ai_review 在网关可用时做的事）。"""
    asset = db.get(KnowledgeAsset, asset_id)
    task = db.get(ReviewTask, task_id)
    seq = max((v.seq for v in asset.versions), default=0) + 1
    version = AssetVersion(asset_id=asset_id, seq=seq, body_md=body_md,
                           change_note="AI 更新草稿", created_by="ai",
                           created_from=VersionOrigin.ai_draft)
    db.add(version)
    db.flush()
    task.ai_draft_version_id = version.id
    db.commit()
    return version.id


# ---------- 列表 ----------

def test_list_orders_by_priority_desc(client, db):
    low = publish(client, title="低优先：无人复用的记录")
    hot = publish(client, title="高优先：被反复复用的记录")
    for user in ("lihao", "chenyuwei", "zhangqiyuan"):
        client.post("/api/v1/feedback/useful", json={"asset_id": hot["id"]},
                    headers={"X-User": user})
    stale_task(client, low["id"])
    stale_task(client, hot["id"])

    items = client.get(REVIEW).json()["items"]
    assert [i["asset"]["id"] for i in items] == [hot["id"], low["id"]]
    assert items[0]["priority"] == 3 and items[0]["usage_30d"] == 3   # 3 次复用 × 风险 1
    assert items[0]["priority_label"] == "中" and items[1]["priority_label"] == "低"
    assert items[0]["trigger"] == "user_feedback"


def test_governance_hides_untouched_noncore(client, db):
    """webhook 命中一个无人使用、非 core、无高风险标签的资产：任务存在但不进人工队列。"""
    asset = publish(client)
    row = db.get(KnowledgeAsset, asset["id"])
    review_queue.open_task(db, row, Trigger.code_change,
                           trigger_detail="上游 push 命中 watch 路径")
    db.commit()

    assert db.scalars(select(ReviewTask).where(ReviewTask.state == "open")).one()
    assert client.get(REVIEW).json()["items"] == []   # 只降权不打扰


def test_governance_lists_core_and_high_risk(client, db):
    core = publish(client, title="core 资产")
    risky = publish(client, title="高风险标签资产", tags=["高风险", "精度"])
    db.get(KnowledgeAsset, core["id"]).tier = Tier.core
    for asset_id in (core["id"], risky["id"]):
        review_queue.open_task(db, db.get(KnowledgeAsset, asset_id), Trigger.code_change,
                               trigger_detail="上游 push 命中 watch 路径")
    db.commit()

    listed = {i["asset"]["id"] for i in client.get(REVIEW).json()["items"]}
    assert listed == {core["id"], risky["id"]}


def test_list_skips_tasks_whose_asset_left_review_due(client, db):
    """历史数据可能出现「任务 open 但资产已离开 REVIEW_DUE」（如 seed 的 STALE 记录）：
    队列只展示 REVIEW_DUE 资产（原型行为），这类任务列出来四选一只会 409。"""
    asset = publish(client)
    stale_task(client, asset["id"])
    row = db.get(KnowledgeAsset, asset["id"])
    row.status = Status.STALE          # 模拟历史脏数据，绕过状态机
    db.commit()
    assert client.get(REVIEW).json()["items"] == []


def test_list_carries_ai_payload(client, db):
    asset = publish(client)
    task_id = stale_task(client, asset["id"])
    task = db.get(ReviewTask, task_id)
    task.ai_impact_summary = "第 2 节的目录结构描述可能失效"
    db.commit()
    make_draft(db, asset["id"], task_id, "## 结论\n\n目录结构改为分层（待验证）。")

    item = next(i for i in client.get(REVIEW).json()["items"] if i["id"] == task_id)
    assert item["ai_impact_summary"].startswith("第 2 节")
    assert "分层" in item["ai_draft"]
    assert item["asset"]["code"] == f"KA-{asset['id']:03d}"


# ---------- 四选一 ----------

def test_resolve_confirm_restores_verified(client, db):
    asset = publish(client)                                   # wanglei 发布
    client.post("/api/v1/feedback/useful", json={"asset_id": asset["id"]},
                headers={"X-User": "lihao"})                  # 非作者复用 → VERIFIED
    task_id = stale_task(client, asset["id"])                 # VERIFIED → REVIEW_DUE

    resp = client.post(f"{REVIEW}/{task_id}/resolve", json={"action": "confirm"},
                       headers={"X-User": "chenyuwei"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "VERIFIED" and "恢复 VERIFIED" in body["note"]

    validation = db.scalars(select(ValidationRecord).where(
        ValidationRecord.asset_id == asset["id"],
        ValidationRecord.kind == "review_confirm")).one()
    assert validation.result == "pass" and validation.validator_id == "chenyuwei"
    task = db.get(ReviewTask, task_id)
    assert task.state == "done" and task.action == "confirm" and task.handled_by == "chenyuwei"

    transitions = client.get(f"/api/v1/assets/{asset['id']}/transitions").json()
    assert transitions[-1]["trigger"] == "review_confirm"
    assert transitions[-1]["evidence_type"] == "validation"


def test_resolve_confirm_returns_to_draft_when_entered_from_draft(client, db):
    """DRAFT 进的 REVIEW_DUE，确认「未受影响」只回 DRAFT —— 不绕过非作者校验（硬规则 3）。"""
    asset = publish(client)
    task_id = stale_task(client, asset["id"])                 # DRAFT → REVIEW_DUE

    body = client.post(f"{REVIEW}/{task_id}/resolve", json={"action": "confirm"},
                       headers={"X-User": "chenyuwei"}).json()
    assert body["status"] == "DRAFT" and "尚未验证" in body["note"]


def test_resolve_accept_draft_switches_version_and_reindexes(client, db):
    asset = publish(client)
    task_id = stale_task(client, asset["id"])
    draft_id = make_draft(db, asset["id"], task_id,
                          "## 结论\n\n调度已改为零拷贝直通路径（待验证）。")

    resp = client.post(f"{REVIEW}/{task_id}/resolve", json={"action": "accept_draft"},
                       headers={"X-User": "chenyuwei"})
    body = resp.json()
    assert body["status"] == "DRAFT" and body["current_version_id"] == draft_id

    detail = client.get(f"/api/v1/assets/{asset['id']}").json()
    assert detail["current_version"]["id"] == draft_id
    assert detail["current_version"]["created_from"] == "ai_draft"
    transitions = client.get(f"/api/v1/assets/{asset['id']}/transitions").json()
    assert transitions[-1]["trigger"] == "review_accept_draft"

    # 接受草稿后正文必须立刻可检索（backend/CLAUDE.md 的坑：索引不会自己跟上）
    doc = db.get(AssetSearchDoc, asset["id"])
    assert "零拷贝" in doc.raw_text


def test_resolve_accept_draft_without_draft_409(client):
    asset = publish(client)
    task_id = stale_task(client, asset["id"])
    resp = client.post(f"{REVIEW}/{task_id}/resolve", json={"action": "accept_draft"},
                       headers={"X-User": "chenyuwei"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "NO_AI_DRAFT"


def test_resolve_stale_writes_stale_confirm(client, db):
    asset = publish(client)
    task_id = stale_task(client, asset["id"])
    body = client.post(f"{REVIEW}/{task_id}/resolve",
                       json={"action": "stale", "note": "EP 配置已整体迁移，本文命令不可执行"},
                       headers={"X-User": "chenyuwei"}).json()
    assert body["status"] == "STALE"

    validation = db.scalars(select(ValidationRecord).where(
        ValidationRecord.asset_id == asset["id"],
        ValidationRecord.result == "stale_confirm")).one()
    assert "EP 配置" in validation.note
    assert client.get(REVIEW).json()["items"] == []


def test_resolve_archive_with_replacement_link(client, db):
    old = publish(client, title="老版本部署手册")
    new = publish(client, title="新版本部署手册")
    task_id = stale_task(client, old["id"])

    body = client.post(f"{REVIEW}/{task_id}/resolve",
                       json={"action": "archive", "replaced_by": new["id"]},
                       headers={"X-User": "chenyuwei"}).json()
    assert body["status"] == "ARCHIVED"
    transitions = client.get(f"/api/v1/assets/{old['id']}/transitions").json()
    assert f"KA-{new['id']:03d}" in transitions[-1]["note"]


def test_resolve_archive_unknown_replacement_422(client):
    asset = publish(client)
    task_id = stale_task(client, asset["id"])
    resp = client.post(f"{REVIEW}/{task_id}/resolve",
                       json={"action": "archive", "replaced_by": 99999},
                       headers={"X-User": "chenyuwei"})
    assert resp.status_code == 422


def test_resolve_twice_409(client):
    asset = publish(client)
    task_id = stale_task(client, asset["id"])
    client.post(f"{REVIEW}/{task_id}/resolve", json={"action": "confirm"},
                headers={"X-User": "chenyuwei"})
    resp = client.post(f"{REVIEW}/{task_id}/resolve", json={"action": "confirm"},
                       headers={"X-User": "lihao"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "TASK_ALREADY_DONE"


def test_resolve_unknown_task_404(client):
    resp = client.post(f"{REVIEW}/424242/resolve", json={"action": "confirm"})
    assert resp.status_code == 404


def test_resolve_invalid_action_422(client):
    asset = publish(client)
    task_id = stale_task(client, asset["id"])
    resp = client.post(f"{REVIEW}/{task_id}/resolve", json={"action": "promote"})
    assert resp.status_code == 422


def test_resolve_closes_sibling_open_tasks(client, db):
    """跨去抖窗口的同资产旧任务，随本次处理一并关闭 —— 资产已离开 REVIEW_DUE，
    留着旧任务只会让下一个人点出 409。"""
    asset = publish(client)
    first_id = stale_task(client, asset["id"])
    # 把第一条任务推出 24h 去抖窗，再触发一条新任务
    first = db.get(ReviewTask, first_id)
    first.created_at = utcnow() - timedelta(hours=25)
    db.commit()
    second_id = stale_task(client, asset["id"], user="lihao")
    assert second_id != first_id

    client.post(f"{REVIEW}/{second_id}/resolve", json={"action": "confirm"},
                headers={"X-User": "chenyuwei"})
    db.expire_all()
    first = db.get(ReviewTask, first_id)
    assert first.state == "done" and f"[随任务 #{second_id} 一并处理]" in first.trigger_detail
