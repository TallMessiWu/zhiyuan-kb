"""知识缺口：POST /feedback/not-found 的建新与累计、POST /gaps/{id}/claim 的认领（M3）。

缺口是看板有效复用率的分母之一（design.md §9），所以这里盯两件事：
不漏记（每次「没找到」都有事件），也不重复记（同一个需求累计到同一条上）。
"""
import pytest

from app.models import KnowledgeGap, UserFeedback
from app.services.gaps import is_same_need, similarity

from .conftest import publish

PD = "PD 分离（Prefill/Decode disaggregation）在 vllm-ascend 的部署方式与 KV 传输配置"


def _not_found(client, query, user, search_event_id=None, expect=200):
    resp = client.post(
        "/api/v1/feedback/not-found",
        json={"query": query, "search_event_id": search_event_id},
        headers={"X-User": user},
    )
    assert resp.status_code == expect, resp.text
    return resp.json()


def _claim(client, gap_id, user, expect=200):
    resp = client.post(f"/api/v1/gaps/{gap_id}/claim", headers={"X-User": user})
    assert resp.status_code == expect, resp.text
    return resp.json()


# ---------- 合并判据（纯函数） ----------

@pytest.mark.parametrize("a, b", [
    (PD, "PD 分离 部署"),                                   # 搜索词是缺口问句的子集
    ("SGLang 在 Ascend NPU 上的适配现状", "sglang ascend npu 适配现状"),   # 大小写/说法差异
    ("图模式冷启动太慢怎么办", "图模式 冷启动 慢"),
])
def test_same_need_merges(a, b):
    assert is_same_need(a, b)


@pytest.mark.parametrize("a, b", [
    (PD, "SGLang 在 Ascend NPU 上的适配现状与可用特性清单"),
    ("vllm-ascend 部署", "sglang 部署"),                     # 只有一个词重合，不是同一个需求
    (PD, "vllm"),                                            # 单词查询不许把缺口全吸走
    ("图模式冷启动", ""),
])
def test_different_needs_stay_apart(a, b):
    assert not is_same_need(a, b)


def test_similarity_is_symmetric_and_bounded():
    assert similarity(PD, PD) == 1.0
    assert similarity(PD, "SGLang 适配") == similarity("SGLang 适配", PD)
    assert similarity("", PD) == 0.0


# ---------- POST /feedback/not-found ----------

def test_not_found_creates_gap(client, db):
    out = _not_found(client, PD, user="wanglei")

    assert out["created"] is True
    gap = out["gap"]
    assert gap["question"] == PD and gap["hit_count"] == 1
    assert gap["reporters"] == ["wanglei"] and gap["status"] == "open"
    assert gap["code"] == f"GAP-{gap['id']:02d}"
    assert gap["first_at"] == gap["last_at"]

    fb = db.query(UserFeedback).filter_by(kind="not_found").one()
    assert fb.user_id == "wanglei" and fb.note == PD and fb.asset_id is None


def test_similar_query_accumulates_instead_of_creating_a_second_gap(client, db):
    first = _not_found(client, PD, user="wanglei")

    second = _not_found(client, "PD 分离 部署", user="lihao")

    assert second["created"] is False
    assert second["gap"]["id"] == first["gap"]["id"]
    assert second["gap"]["hit_count"] == 2
    assert second["gap"]["reporters"] == ["wanglei", "lihao"]
    assert second["gap"]["question"] == PD           # 问句保持首次记录的说法
    assert db.query(KnowledgeGap).count() == 1
    assert db.query(UserFeedback).filter_by(kind="not_found").count() == 2   # 事件一次不落


def test_same_reporter_counted_once_in_reporters(client):
    _not_found(client, PD, user="wanglei")
    out = _not_found(client, "PD 分离 KV 传输", user="wanglei")

    assert out["gap"]["hit_count"] == 2              # 需求次数照涨
    assert out["gap"]["reporters"] == ["wanglei"]    # 提出人去重


def test_unrelated_query_opens_a_new_gap(client, db):
    _not_found(client, PD, user="wanglei")
    out = _not_found(client, "SGLang 在 Ascend NPU 上的适配现状与可用特性清单", user="chenyuwei")

    assert out["created"] is True
    assert db.query(KnowledgeGap).count() == 2


def test_empty_query_records_browse_placeholder(client):
    """浏览模式下也能记缺口（原型的「（无关键词浏览）」）。"""
    out = _not_found(client, "", user="wanglei")
    assert out["created"] is True and out["gap"]["question"] == "（无关键词浏览）"


def test_not_found_links_the_search_event(client, db):
    """搜索 → 没找到，这条链路是看板把需求事件和缺口对上的依据。"""
    publish(client, user="chenyuwei")
    search = client.get("/api/v1/search", params={"q": "PD 分离"}, headers={"X-User": "wanglei"}).json()

    _not_found(client, "PD 分离 部署", user="wanglei", search_event_id=search["search_event_id"])

    fb = db.query(UserFeedback).filter_by(kind="not_found").one()
    assert fb.search_event_id == search["search_event_id"]


def test_not_found_with_unknown_search_event_422(client):
    body = _not_found(client, PD, user="wanglei", search_event_id=4242, expect=422)
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_resolved_gap_does_not_absorb_new_reports(client, db):
    """已解决的缺口还有人报「没找到」，说明是搜不到而不是缺知识 —— 记成新的需求信号。"""
    db.add(KnowledgeGap(question=PD, hit_count=3, reporters=["wanglei"], status="resolved"))
    db.commit()

    out = _not_found(client, "PD 分离 部署", user="lihao")

    assert out["created"] is True
    assert db.query(KnowledgeGap).filter_by(status="open").count() == 1


def test_new_gap_shows_up_in_gaps_list(client):
    _not_found(client, PD, user="wanglei")
    gaps = client.get("/api/v1/gaps").json()
    assert [g["question"] for g in gaps] == [PD]


# ---------- POST /gaps/{id}/claim ----------

def test_claim_marks_gap_claimed(client, db):
    gap_id = _not_found(client, PD, user="wanglei")["gap"]["id"]

    out = _claim(client, gap_id, user="lihao")

    assert out["status"] == "claimed" and out["claimed_by"] == "lihao"
    row = db.get(KnowledgeGap, gap_id)
    db.refresh(row)
    assert row.status == "claimed" and row.claimed_by == "lihao"
    # 认领只是登记「我来写」，不产出任何资产（AI 底稿在 M5）
    assert client.get("/api/v1/home").json()["stats"]["total"] == 0


def test_claimed_gap_still_listed_but_after_open_ones(client):
    claimed_id = _not_found(client, PD, user="wanglei")["gap"]["id"]
    _claim(client, claimed_id, user="lihao")
    _not_found(client, "SGLang 在 Ascend NPU 上的适配现状与可用特性清单", user="chenyuwei")

    gaps = client.get("/api/v1/gaps").json()
    assert [g["status"] for g in gaps] == ["open", "claimed"]


def test_claim_by_another_user_409(client):
    gap_id = _not_found(client, PD, user="wanglei")["gap"]["id"]
    _claim(client, gap_id, user="lihao")

    body = _claim(client, gap_id, user="sunxiaodong", expect=409)
    assert body["error"]["code"] == "GAP_ALREADY_CLAIMED" and "lihao" in body["error"]["message"]


def test_claim_is_idempotent_for_the_same_user(client):
    gap_id = _not_found(client, PD, user="wanglei")["gap"]["id"]
    _claim(client, gap_id, user="lihao")
    assert _claim(client, gap_id, user="lihao")["claimed_by"] == "lihao"


def test_claim_resolved_gap_409(client, db):
    gap = KnowledgeGap(question=PD, hit_count=1, reporters=["wanglei"], status="resolved")
    db.add(gap)
    db.commit()

    body = _claim(client, gap.id, user="lihao", expect=409)
    assert body["error"]["code"] == "GAP_RESOLVED"


def test_claim_unknown_gap_404(client):
    body = _claim(client, 9999, user="lihao", expect=404)
    assert body["error"]["code"] == "NOT_FOUND"
