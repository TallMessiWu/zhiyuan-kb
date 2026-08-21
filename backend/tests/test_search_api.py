"""GET /search 端到端 —— 召回、过滤、排序、分项得分、SearchEvent 落库。

跑在 sqlite 上，所以关键词走 recall.py 的可移植路径；PG 的 tsvector 路径在真实
PostgreSQL 上验收（见 backend/CLAUDE.md「M2 验收」）。两条路的**排序契约**由这里保证：
可移植路径过不了的用例，PG 路径也不该过。
"""
import zlib

import pytest
from sqlalchemy import select, update

from app.config import settings
from app.models import AssetEmbedding, KnowledgeAsset, SearchEvent, Status, Trigger
from app.services import ai, recall, state_machine
from app.services.text import tokenize

from .conftest import publish

MLA = {
    "title": "DeepSeek-V3 MLA 在图模式下的算子限制",
    "direction": "model",
    "body_md": (
        "## 问题\n\nMLA 走 aclgraph 捕获时部分算子回退到 eager。\n\n"
        "## 环境\n\nvllm-ascend v0.9.1 · CANN 8.2.RC1\n\n"
        "## 结论\n\n关闭图模式或等待算子补齐，收益有限。\n"
    ),
    "models": ["DeepSeek-V3"],
    "framework": "vllm-ascend",
    "fw_version": "v0.9.1–v0.10.0",
    "tags": ["mla", "图模式"],
}

SGLANG = {
    "title": "SGLang overlap 调度在 NPU 流上的行为",
    "direction": "chain",
    "body_md": (
        "## 问题\n\noverlap 调度与 NPU 流的同步点冲突。\n\n"
        "## 环境\n\nsglang v0.4.6\n\n"
        "## 结论\n\n关掉 overlap 后吞吐反而更稳。\n"
    ),
    "models": ["通用"],
    "framework": "sglang",
    "fw_version": "v0.4.6",
    "tags": ["调度", "overlap"],
}


def search(client, user="wanglei", **params):
    resp = client.get("/api/v1/search", params=params, headers={"X-User": user})
    assert resp.status_code == 200, resp.text
    return resp.json()


def ids(data) -> list[int]:
    return [item["asset"]["id"] for item in data["items"]]


def parts(item) -> dict[str, float]:
    return {p["label"]: p["value"] for p in item["score"]["parts"]}


# ---------- 召回 ----------

def test_published_asset_is_searchable_immediately(client):
    """索引在发布的同一个事务里建 —— 刚沉淀的知识必须马上搜得到。"""
    asset = publish(client)
    data = search(client, q="TTFT 调度")

    assert ids(data) == [asset["id"]]
    assert data["total"] == 1
    assert data["search_event_id"] > 0
    assert data["recall"]["keyword"] == "portable"     # sqlite 上的降级路径
    assert data["recall"]["keyword_hits"] == 1


def test_query_terms_come_back_for_highlighting(client):
    publish(client, **MLA)
    data = search(client, q="MLA 图模式")

    assert "mla" in data["terms"]
    assert all(len(t) >= 2 for t in data["terms"])     # 单字不高亮，否则整段标黄


def test_unrelated_query_returns_nothing_but_still_records_the_need(client, db):
    """零结果也要落 SearchEvent：它是需求事件，看板复用率的分母要用（design.md §9）。"""
    publish(client)
    data = search(client, q="量子纠缠")

    assert data["items"] == [] and data["total"] == 0
    event = db.scalar(select(SearchEvent).order_by(SearchEvent.id.desc()))
    assert event.query == "量子纠缠" and event.result_ids == []


def test_search_event_records_user_filters_and_results(client, db):
    asset = publish(client, **MLA)
    data = search(client, q="MLA", direction="model")

    event = db.get(SearchEvent, data["search_event_id"])
    assert event.user_id == "wanglei"
    assert event.result_ids == [asset["id"]]
    assert event.filters["direction"] == "model"
    assert event.mode == "search"


def test_matching_the_title_beats_matching_only_the_body(client):
    """字段权重 title×4 > body×1（design.md §5）：标题命中的应当排前面。"""
    titled = publish(client, title="aclgraph 捕获失败的排查", tags=[], models=[])
    in_body = publish(client, title="TP4 下的吞吐调优", tags=[],
                      body_md="## 结论\n\n顺带提一句 aclgraph 捕获。\n", models=[])
    data = search(client, q="aclgraph")

    assert ids(data)[0] == titled["id"]
    assert in_body["id"] in ids(data)


# ---------- 排序 ----------

def test_verified_outranks_draft(client):
    """「找推理知识，先看可信度」：同样相关时 VERIFIED 必须压过 DRAFT。"""
    draft = publish(client, title="显存 OOM 的排查路径 A")
    verified = publish(client, title="显存 OOM 的排查路径 B")
    assert draft["status"] == "DRAFT"
    ok = client.post("/api/v1/feedback/useful", json={"asset_id": verified["id"]},
                     headers={"X-User": "chenyuwei"})       # 非作者复用 → 升 VERIFIED
    assert ok.json()["status"] == "VERIFIED"

    data = search(client, q="显存 OOM")
    assert ids(data)[0] == verified["id"]
    assert parts(data["items"][0])["状态 VERIFIED"] == 14.0


def test_review_due_is_downweighted_not_hidden(client, db):
    fresh = publish(client, title="调度预算调优 A")
    stale_ish = publish(client, title="调度预算调优 B")
    asset = db.get(KnowledgeAsset, stale_ish["id"])
    state_machine.transition(db, asset, Status.REVIEW_DUE, Trigger.user_feedback,
                             actor="chenyuwei", evidence_type="user_feedback", note="反馈过时")
    db.commit()

    data = search(client, q="调度预算")
    assert set(ids(data)) == {fresh["id"], stale_ish["id"]}      # 还在结果里
    assert ids(data)[0] == fresh["id"]                           # 但被压到后面
    assert parts(data["items"][1])["状态 REVIEW_DUE"] == -10.0


def test_reuse_evidence_lifts_ranking(client, db):
    quiet = publish(client, title="EI0002 报错处理 A")
    reused = publish(client, title="EI0002 报错处理 B")
    db.get(KnowledgeAsset, reused["id"]).reuse_count = 20
    db.commit()

    data = search(client, q="EI0002")
    assert ids(data) == [reused["id"], quiet["id"]]
    assert parts(data["items"][0])["复用×20"] == 8.0             # proof 封顶 8


def test_version_range_hit_adds_points(client):
    publish(client, **MLA)
    data = search(client, q="v0.9.5 MLA 图模式")

    assert parts(data["items"][0])["版本区间命中"] == 4.0


def test_inferred_framework_downweights_mismatch(client):
    """查询里带 sglang 是**软**信号：不匹配的降权但仍可见，不硬过滤。"""
    publish(client, **SGLANG)
    publish(client, **MLA)
    data = search(client, q="sglang MLA")      # 两条各命中一个词，都会被召回

    assert len(data["items"]) == 2
    assert "框架匹配" in parts(data["items"][0])
    assert parts(data["items"][-1])["框架不符"] == -8.0


# ---------- 过滤 ----------

def test_explicit_framework_filter_is_a_hard_filter(client):
    """筛选器里点的框架是显式筛选，直接在召回层挡掉（design.md §5「显式筛选除外」）。"""
    sglang = publish(client, **SGLANG)
    publish(client, **MLA)
    data = search(client, q="调度", framework="sglang")

    assert ids(data) == [sglang["id"]]


def test_model_filter_lets_generic_assets_through(client):
    """models=["通用"] 是保留值：任何模型筛选都应当放它过。"""
    generic = publish(client, **SGLANG)       # models=["通用"]
    specific = publish(client, **MLA)         # models=["DeepSeek-V3"]

    # 筛 DeepSeek-V3：精确匹配的和「通用」的都留下
    assert set(ids(search(client, q="调度 MLA", model="DeepSeek-V3"))) == {
        generic["id"], specific["id"]
    }
    # 筛一个谁都没声明的模型：只剩「通用」那条
    assert ids(search(client, q="调度 MLA", model="Qwen3-30B-A3B")) == [generic["id"]]


def test_direction_and_status_filters(client):
    mla = publish(client, **MLA)
    publish(client, **SGLANG)

    assert ids(search(client, q="调度 MLA", direction="model")) == [mla["id"]]
    assert ids(search(client, q="调度 MLA", status="VERIFIED")) == []


def test_stale_is_isolated_and_only_visible_in_hist_mode(client, db):
    """STALE/ARCHIVED 默认不进搜索结果，只有历史资产模式能捞出来（硬规则 4）。"""
    alive = publish(client, title="HCCL 初始化失败 A")
    dead = publish(client, title="HCCL 初始化失败 B")
    asset = db.get(KnowledgeAsset, dead["id"])
    state_machine.transition(db, asset, Status.REVIEW_DUE, Trigger.code_change, actor="system")
    state_machine.transition(db, asset, Status.STALE, Trigger.review_stale, actor="chenyuwei")
    db.commit()

    assert ids(search(client, q="HCCL 初始化")) == [alive["id"]]

    hist = search(client, q="HCCL 初始化", hist=True)
    assert ids(hist) == [dead["id"]] and hist["hist"] is True


def test_browse_mode_without_query_ranks_by_status_and_reuse(client, db):
    plain = publish(client, title="普通记录")
    promoted = publish(client, title="被复用过的记录")
    db.get(KnowledgeAsset, promoted["id"]).reuse_count = 10
    db.commit()
    client.post("/api/v1/feedback/useful", json={"asset_id": promoted["id"]},
                headers={"X-User": "chenyuwei"})

    data = search(client)
    assert ids(data)[0] == promoted["id"]
    assert plain["id"] in ids(data)
    assert data["terms"] == []
    assert data["recall"]["keyword_hits"] == 0          # 浏览模式不走召回


def test_pagination_reports_total_across_pages(client):
    for i in range(3):
        publish(client, title=f"aclgraph 捕获桶失效 {i}")

    first = search(client, q="aclgraph", limit=2)
    assert len(first["items"]) == 2 and first["total"] == 3

    second = search(client, q="aclgraph", limit=2, offset=2)
    assert len(second["items"]) == 1
    assert set(ids(first)) | set(ids(second)) == set(ids(search(client, q="aclgraph")))


@pytest.mark.parametrize(("param", "value"), [("direction", "不存在"), ("status", "GONE")])
def test_invalid_filter_returns_422_in_the_standard_error_shape(client, param, value):
    resp = client.get("/api/v1/search", params={"q": "x", param: value})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_empty_filter_values_mean_all(client):
    """前端下拉框的「全部」就是空串，不能变成 422。"""
    asset = publish(client)
    assert ids(search(client, q="TTFT", direction="", status="", framework="", model="")) == [
        asset["id"]
    ]


# ---------- 向量路与 AI 摘要（网关行为用假实现覆盖） ----------

def fake_embed(texts, **_):
    """词袋哈希向量：共享词越多越接近。够用来验证「向量路真的跑了并影响了融合」。"""
    vectors = []
    for text in texts:
        vector = [0.0] * 32
        for token in tokenize(text):
            vector[zlib.crc32(token.encode()) % 32] += 1.0
        vectors.append(vector)
    return vectors


def test_vector_path_runs_when_embeddings_are_available(client, db, monkeypatch):
    monkeypatch.setattr(settings, "vector_search", "auto")
    monkeypatch.setattr(ai, "embed", fake_embed)
    recall.reset_capabilities_cache()

    asset = publish(client, **MLA)
    data = search(client, q="MLA 图模式")

    assert data["recall"]["vector"] == "python"      # sqlite 上没有 pgvector，走内存余弦
    assert data["recall"]["vector_hits"] >= 1
    assert ids(data) == [asset["id"]]


def test_vector_path_is_skipped_when_the_gateway_is_down(client, monkeypatch):
    """网关挂了不该让搜索挂：整路跳过，关键词路照常出结果，并在响应里说明。"""
    monkeypatch.setattr(settings, "vector_search", "auto")
    monkeypatch.setattr(ai, "embed", lambda texts, **_: None)
    recall.reset_capabilities_cache()

    asset = publish(client, **MLA)
    data = search(client, q="MLA 图模式")

    assert data["recall"]["vector"] == "unavailable"
    assert ids(data) == [asset["id"]]


def test_ai_summary_is_used_and_labelled(client, monkeypatch):
    monkeypatch.setattr(settings, "ai_summary", "auto")
    monkeypatch.setattr(ai, "summarize", lambda title, body: "把 max_num_batched_tokens 调到 8192")

    detail = publish(client)
    assert detail["summary"] == "把 max_num_batched_tokens 调到 8192"
    assert detail["summary_source"] == "ai"          # 硬规则 1：AI 产出必须可识别


def test_summary_falls_back_to_rules_when_gateway_is_down(client, monkeypatch):
    monkeypatch.setattr(settings, "ai_summary", "auto")
    monkeypatch.setattr(ai, "summarize", lambda title, body: None)

    detail = publish(client)
    assert detail["summary_source"] == "rule"
    assert "max_num_batched_tokens" in detail["summary"]   # 规则式摘要取「结论」小节


def test_vector_path_ignores_embeddings_from_another_model(client, db, monkeypatch):
    """换了 embedding 模型而没重建索引：老向量维度可能还对得上，余弦照算不误、结果却是胡的。
    宁可这一路暂时不召回，也不能悄悄用错模型的向量。"""
    monkeypatch.setattr(settings, "vector_search", "auto")
    monkeypatch.setattr(ai, "embed", fake_embed)
    recall.reset_capabilities_cache()

    publish(client, **MLA)
    assert search(client, q="MLA 图模式")["recall"]["vector_hits"] == 1

    db.execute(update(AssetEmbedding).values(model="旧模型-v1"))
    db.commit()
    assert search(client, q="MLA 图模式")["recall"]["vector_hits"] == 0
