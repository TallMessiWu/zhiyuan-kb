"""GET /home 与 GET /gaps —— 首页那一屏的数据来源。"""
from datetime import datetime, timezone

from app.models import KnowledgeAsset, KnowledgeGap, Status, Trigger
from app.services import state_machine

from .conftest import publish


def gap(db, **kwargs) -> KnowledgeGap:
    row = KnowledgeGap(**{
        "question": "PD 分离的部署方式", "hit_count": 1,
        "first_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "last_at": datetime(2026, 8, 18, tzinfo=timezone.utc),
        "reporters": ["wanglei"], "status": "open", "claimed_by": "", **kwargs,
    })
    db.add(row)
    db.commit()
    return row


def home(client) -> dict:
    resp = client.get("/api/v1/home")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_stats_count_live_assets_by_status(client, db):
    draft = publish(client, title="草稿一条")
    verified = publish(client, title="会被验证的一条")
    client.post("/api/v1/feedback/useful", json={"asset_id": verified["id"]},
                headers={"X-User": "chenyuwei"})
    archived = publish(client, title="要归档的一条")
    state_machine.transition(db, db.get(KnowledgeAsset, archived["id"]), Status.ARCHIVED,
                             Trigger.review_replace, actor="chenyuwei", note="被替代")
    db.commit()
    gap(db)
    gap(db, question="已认领的缺口", status="claimed", claimed_by="lihao")

    stats = home(client)["stats"]
    assert stats["total"] == 2                # 在库资产不含 ARCHIVED
    assert stats["verified"] == 1
    assert stats["review_due"] == 0
    assert stats["open_gaps"] == 1            # claimed 的不算「待认领」
    assert draft["id"]                        # 草稿仍在库里，只是不单独计数


def test_recent_validated_carries_the_evidence(client):
    """首页「最近验证」要给出谁验证的、说明是什么 —— 可信度必须能溯源。"""
    asset = publish(client, title="被非作者复用的记录")
    client.post("/api/v1/feedback/useful",
                json={"asset_id": asset["id"], "task_note": "照着改完 TTFT 降到 2s"},
                headers={"X-User": "chenyuwei"})

    recent = home(client)["recent_validated"]
    assert len(recent) == 1
    assert recent[0]["asset"]["id"] == asset["id"]
    assert recent[0]["asset"]["status"] == "VERIFIED"
    assert recent[0]["validator_id"] == "chenyuwei"
    assert "TTFT 降到 2s" in recent[0]["note"]


def test_recent_validated_shows_each_asset_once(client):
    asset = publish(client, title="被反复复用的记录")
    for user in ("chenyuwei", "sunxiaodong"):
        client.post("/api/v1/feedback/useful", json={"asset_id": asset["id"]},
                    headers={"X-User": user})

    assert [r["asset"]["id"] for r in home(client)["recent_validated"]] == [asset["id"]]


def test_hot_ranks_by_reuse_and_excludes_dead_assets(client, db):
    quiet = publish(client, title="没人用过的记录")
    popular = publish(client, title="热门记录")
    dead = publish(client, title="已失效的记录")
    db.get(KnowledgeAsset, popular["id"]).reuse_count = 9
    asset = db.get(KnowledgeAsset, dead["id"])
    state_machine.transition(db, asset, Status.REVIEW_DUE, Trigger.code_change, actor="system")
    state_machine.transition(db, asset, Status.STALE, Trigger.review_stale, actor="chenyuwei")
    db.commit()

    hot = home(client)["hot"]
    assert [a["id"] for a in hot] == [popular["id"], quiet["id"]]
    assert hot[0]["reuse_count"] == 9


def test_list_items_carry_framework_and_models_for_the_meta_line(client):
    publish(client, title="带维度的记录")
    item = home(client)["hot"][0]

    assert item["framework"] == "vllm-ascend"
    assert item["fw_version"] == "v0.10.0rc1"
    assert item["models"] == ["Qwen3-30B-A3B"]
    assert item["code"].startswith("KA-")


def test_gaps_put_open_first_then_most_asked(client, db):
    gap(db, question="被问得最多的", hit_count=5)
    gap(db, question="已认领的", hit_count=9, status="claimed", claimed_by="lihao")
    gap(db, question="已解决的", hit_count=9, status="resolved")

    resp = client.get("/api/v1/gaps")
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["question"] for r in rows] == ["被问得最多的", "已认领的"]   # resolved 不出现
    assert rows[0]["code"] == "GAP-01"
    assert rows[0]["reporters"] == ["wanglei"]


def test_home_and_gaps_share_the_same_ordering(client, db):
    gap(db, question="A", hit_count=3)
    gap(db, question="B", hit_count=7)

    from_home = [g["question"] for g in home(client)["gaps"]]
    from_list = [g["question"] for g in client.get("/api/v1/gaps").json()]
    assert from_home == from_list == ["B", "A"]


def test_home_on_an_empty_database(client):
    data = home(client)
    assert data["stats"] == {"total": 0, "verified": 0, "review_due": 0, "open_gaps": 0}
    assert data["recent_validated"] == [] and data["hot"] == [] and data["gaps"] == []
