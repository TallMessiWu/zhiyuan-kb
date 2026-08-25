"""POST /gaps/{id}/draft 与 POST /assets 的缺口回链（M5 认领闭环）。

约定回顾：claim 只登记（M3），draft 只返回预填建议（不落库），发布带 gap_id 才把
缺口置 resolved。AI 底稿走 ai.chat 打桩；降级用例靠 conftest 关掉的开关。
"""
import json

from sqlalchemy import func, select

from app.models import KnowledgeGap, SearchEvent
from app.services import ai

from .conftest import SAMPLE, publish

DRAFT_JSON = {
    "title": "PD 分离在 vllm-ascend 上的部署步骤",
    "problem": "多机 PD 分离部署缺少已验证做法。",
    "env": "vllm-ascend v0.10.x · CANN 8.2.RC1",
    "conclusion": "按 disagg_prefill 示例配置 proxy（待验证）。",
    "tags": ["pd分离", "部署"],
    "direction": "chain",
    "models": ["DeepSeek-V3"],
    "framework": "vllm-ascend",
    "fw_version": "v0.10.0",
    "code_refs": [{"repo": "vllm-project/vllm-ascend",
                   "path_or_key": "examples/disagg_prefill", "note": "官方示例"}],
}


def make_gap(client, question="PD 分离在 vllm-ascend 上怎么部署", user="wanglei"):
    resp = client.post("/api/v1/feedback/not-found", json={"query": question},
                       headers={"X-User": user})
    assert resp.status_code == 200
    return resp.json()["gap"]["id"]


def claim(client, gap_id, user="wanglei"):
    resp = client.post(f"/api/v1/gaps/{gap_id}/claim", headers={"X-User": user})
    assert resp.status_code == 200
    return resp.json()


def draft(client, gap_id, user="wanglei", expect=200):
    resp = client.post(f"/api/v1/gaps/{gap_id}/draft", headers={"X-User": user})
    assert resp.status_code == expect, resp.text
    return resp.json()


# ---------- 前置条件 ----------

def test_draft_requires_claim_first(client):
    gap_id = make_gap(client)
    body = draft(client, gap_id, expect=409)
    assert body["error"]["code"] == "GAP_NOT_CLAIMED"


def test_draft_is_for_the_claimant_only(client):
    gap_id = make_gap(client)
    claim(client, gap_id, user="wanglei")
    body = draft(client, gap_id, user="zhangsan", expect=409)
    assert body["error"]["code"] == "GAP_ALREADY_CLAIMED"


def test_draft_on_missing_gap_is_404(client):
    draft(client, 9999, expect=404)


# ---------- 生成与清洗 ----------

def test_draft_happy_path_returns_prefill_and_sources(client, db, monkeypatch):
    context_asset = publish(client, user="zhangsan")     # 库里有相关资产 → 进 sources
    gap_id = make_gap(client, question="TTFT 调度预算 怎么调")
    claim(client, gap_id)
    monkeypatch.setattr(ai, "chat", lambda p, s="", **kw: json.dumps(DRAFT_JSON, ensure_ascii=False))

    events_before = db.scalar(select(func.count()).select_from(SearchEvent))
    data = draft(client, gap_id)

    assert data["gap_id"] == gap_id
    d = data["draft"]
    assert d["title"] == DRAFT_JSON["title"]
    assert d["direction"] == "chain"
    assert d["code_refs"][0]["path_or_key"] == "examples/disagg_prefill"
    assert d["code_refs"][0]["watch"] is True
    assert data["sources"] == [context_asset["id"]]

    # 底稿是预填建议：不产出资产、不改缺口状态（发布才闭环）
    gap = db.get(KnowledgeGap, gap_id)
    assert gap.status == "claimed"
    # 底稿检索是系统辅助，不是需求事件 —— 不许污染复用率分母（硬规则 5）
    assert db.scalar(select(func.count()).select_from(SearchEvent)) == events_before


def test_draft_cleans_malformed_llm_output(client, monkeypatch):
    gap_id = make_gap(client)
    claim(client, gap_id)
    monkeypatch.setattr(ai, "chat", lambda p, s="", **kw: json.dumps({
        "title": "x" * 999,
        "direction": "银河系漫游",                     # 非法枚举 → 回落 feature
        "tags": ["ok", "", 123],
        "code_refs": [{"repo": "a/b"}, "不是对象", {"path_or_key": "real/path.py"}],
    }, ensure_ascii=False))

    d = draft(client, gap_id)["draft"]
    assert len(d["title"]) == 300                      # 对齐 AssetCreate.title 列宽
    assert d["direction"] == "feature"
    assert d["tags"] == ["ok", "123"]
    assert [r["path_or_key"] for r in d["code_refs"]] == ["real/path.py"]   # 没路径的丢弃


def test_draft_gateway_down_is_503_and_claim_survives(client, db):
    gap_id = make_gap(client)
    claim(client, gap_id)

    body = draft(client, gap_id, expect=503)
    assert body["error"]["code"] == "AI_UNAVAILABLE"
    assert db.get(KnowledgeGap, gap_id).status == "claimed"   # 认领不受降级影响


# ---------- 发布回链（缺口闭环） ----------

def test_publish_with_gap_id_resolves_the_gap(client, db):
    gap_id = make_gap(client)
    claim(client, gap_id)

    detail = publish(client, user="wanglei", gap_id=gap_id)

    gap = db.get(KnowledgeGap, gap_id)
    assert gap.status == "resolved"
    assert gap.resolved_asset_id == detail["id"]
    # resolved 的缺口退出列表（M3 语义不变）
    assert gap_id not in [g["id"] for g in client.get("/api/v1/gaps").json()]


def test_publish_against_resolved_gap_is_409(client):
    gap_id = make_gap(client)
    claim(client, gap_id)
    publish(client, gap_id=gap_id)

    resp = client.post("/api/v1/assets", json={**SAMPLE, "gap_id": gap_id},
                       headers={"X-User": "wanglei"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "GAP_RESOLVED"


def test_publish_with_unknown_gap_id_is_422(client):
    resp = client.post("/api/v1/assets", json={**SAMPLE, "gap_id": 9999},
                       headers={"X-User": "wanglei"})
    assert resp.status_code == 422
