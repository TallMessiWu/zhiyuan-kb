"""GET /dashboard 与 /home 复用率 —— design.md §9 口径的手工对账。

时间相关的合并逻辑（30 分钟窗口、跨月分桶）走 metrics.demand_sessions 纯函数测试；
API 用例都发生在「现在」，落在 30 天窗口与当月 trend 点里，对账用小数字手算。
"""
from datetime import datetime, timezone

from app.config import settings
from app.models import SearchEvent
from app.services.metrics import demand_sessions, topic_key

from .conftest import publish


def dash(client):
    resp = client.get("/api/v1/dashboard")
    assert resp.status_code == 200, resp.text
    return resp.json()


def search(client, q, user):
    resp = client.get("/api/v1/search", params={"q": q}, headers={"X-User": user})
    assert resp.status_code == 200
    return resp.json()


def useful(client, asset_id, user, event_id=None):
    resp = client.post("/api/v1/feedback/useful",
                       json={"asset_id": asset_id, "search_event_id": event_id},
                       headers={"X-User": user})
    assert resp.status_code == 200, resp.text
    return resp.json()


def not_found(client, query, user, event_id=None):
    resp = client.post("/api/v1/feedback/not-found",
                       json={"query": query, "search_event_id": event_id},
                       headers={"X-User": user})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------- 需求会话去重（纯函数） ----------

def _event(user, q, minutes, mode="search", results=()):
    return SearchEvent(
        id=minutes * 7 + hash((user, q)) % 5, user_id=user, query=q, mode=mode,
        result_ids=list(results),
        at=datetime(2026, 8, 20, 10, minutes, tzinfo=timezone.utc),
    )


def test_sessions_merge_same_user_same_topic_within_window():
    events = [_event("wanglei", "MLA 限制", 0), _event("wanglei", "MLA 限制", 10),
              _event("wanglei", "MLA 限制", 25)]
    assert len(demand_sessions(events)) == 1


def test_sessions_split_when_gap_exceeds_window():
    events = [_event("wanglei", "MLA 限制", 0), _event("wanglei", "MLA 限制", 40)]
    assert len(demand_sessions(events)) == 2


def test_sessions_split_by_user_and_topic():
    events = [_event("wanglei", "MLA 限制", 0), _event("zhangsan", "MLA 限制", 1),
              _event("wanglei", "PD 分离", 2)]
    assert len(demand_sessions(events)) == 3


def test_topic_key_ignores_word_order():
    """同一需求换个说法要归成同一主题 —— 词集合归一（与缺口合并同一思路）。"""
    assert topic_key("MLA 的限制") == topic_key("限制 MLA")
    assert topic_key("PD 分离") != topic_key("MLA 限制")


def test_qa_and_search_share_the_dedupe(client, monkeypatch):
    """同人同主题先搜后问是一次需求：search 与 qa 一起去重（§9「搜索/问答会话」）。"""
    events = [_event("wanglei", "MLA 限制", 0),
              _event("wanglei", "MLA 的限制", 5, mode="qa")]
    sessions = demand_sessions(events)
    assert len(sessions) == 1 and sessions[0].modes == {"search", "qa"}


# ---------- 复用率（硬规则 5） ----------

def test_reuse_rate_counts_only_nonauthor_success(client):
    asset = publish(client, user="wanglei")

    ev = search(client, "TTFT 调度", user="zhangsan")["search_event_id"]
    search(client, "TTFT 调度", user="zhangsan")          # 同人同主题：并进同一会话
    useful(client, asset["id"], user="zhangsan", event_id=ev)   # 非作者 → 分子 +1
    useful(client, asset["id"], user="wanglei")                 # 作者本人 → 分子不动

    data = dash(client)["reuse_rate"]
    assert data["num"] == 1
    assert data["den"] == 1            # 两次搜索去重成一个需求会话
    assert data["pct"] == 100.0
    assert data["trend"][-1]["value"] == 100.0

    # 首页第五格与看板同一口径、同一数字
    home = client.get("/api/v1/home").json()["stats"]["reuse_rate"]
    assert home == {"num": 1, "den": 1, "pct": 100.0}


def test_empty_database_shows_dash_not_zero(client):
    data = dash(client)
    assert data["reuse_rate"] == {"num": 0, "den": 0, "pct": None,
                                  "trend": data["reuse_rate"]["trend"]}
    assert data["search_ok"]["pct"] is None


def test_denominator_adds_only_orphan_not_found(client):
    """带 search_event_id 的缺口反馈，需求已被那次会话计入 —— 再加就是双算。"""
    ev = search(client, "PD 分离 部署", user="wanglei")["search_event_id"]
    not_found(client, "PD 分离 部署", user="wanglei", event_id=ev)     # 不另加
    not_found(client, "K8s 上的 LoRA 热加载", user="zhangsan")          # 详情页入口 → +1

    data = dash(client)
    assert data["reuse_rate"]["den"] == 2       # 1 会话 + 1 孤儿反馈
    assert data["not_found_30d"] == 2           # 反馈计数本身两条都算
    assert data["gaps_total"] == 2 and data["open_gaps"] == 2


# ---------- 搜索成功率 ----------

def test_search_ok_needs_results_and_no_not_found(client, db):
    asset = publish(client, user="wanglei")

    search(client, "TTFT 调度", user="zhangsan")                        # 有结果、无反馈 → ok
    ev = search(client, "PD 分离 部署", user="lisi")["search_event_id"]  # 零结果
    not_found(client, "PD 分离 部署", user="lisi", event_id=ev)          # 且反馈没找到 → 不 ok
    db.add(SearchEvent(user_id="wangwu", query="MLA 限制", mode="qa",
                       result_ids=[asset["id"]]))
    db.commit()                                                          # 纯问答会话不进搜索成功率

    data = dash(client)["search_ok"]
    assert data["total_sessions"] == 2
    assert data["ok_sessions"] == 1
    assert data["pct"] == 50.0


# ---------- 覆盖矩阵与库存 ----------

def test_coverage_matrix_and_backlog_counts(client):
    a = publish(client, user="wanglei", direction="model")
    publish(client, user="wanglei", direction="chain", title="链路资产")
    useful(client, a["id"], user="zhangsan")     # 非作者复用 → model 方向 DRAFT→VERIFIED

    stale_target = publish(client, user="wanglei", direction="feature", title="要过时的")
    resp = client.post("/api/v1/feedback/stale", json={"asset_id": stale_target["id"]},
                       headers={"X-User": "lisi"})
    assert resp.status_code == 200

    data = dash(client)
    assert data["coverage"]["model"]["VERIFIED"] == 1
    assert data["coverage"]["chain"]["DRAFT"] == 1
    assert data["coverage"]["feature"]["REVIEW_DUE"] == 1
    assert data["coverage"]["model"]["STALE"] == 0          # 空格子也要在（前端矩阵直接渲染）
    assert data["reuse_by_direction"] == {"model": 1, "chain": 0, "feature": 0}
    assert data["review_backlog"] == 1
    assert data["verified_count"] == 1
    assert data["draft_count"] == 1


# ---------- 重复探索工时（估算） ----------

def test_rework_hours_count_repeat_topics_across_users(client):
    publish(client, user="wanglei")
    search(client, "TTFT 调度", user="zhangsan")
    search(client, "调度 TTFT", user="lisi")        # 同主题第 2 个会话（跨用户）→ 重复 ×1

    data = dash(client)
    assert data["rework_hours_estimated"] is True   # 估算指标必须自报，不冒充实测
    assert data["rework_hours_per_miss"] == settings.rework_hours_per_miss
    assert data["rework_hours_trend"][-1]["value"] == settings.rework_hours_per_miss


def test_rework_ignores_browse_sessions(client):
    search(client, "", user="zhangsan")
    search(client, "", user="lisi")                 # 浏览不构成主题，不算重复探索

    assert dash(client)["rework_hours_trend"][-1]["value"] == 0.0


def test_trend_has_five_month_points(client):
    trend = dash(client)["reuse_rate"]["trend"]
    assert len(trend) == 5
    assert all(p["label"].endswith("月") for p in trend)
