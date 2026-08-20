"""资产接口测试 — POST /assets、GET /assets/{id}、GET /assets/{id}/transitions。"""
from app.api.assets import _derive_summary, split_version_range
from app.models import AssetVersion, KnowledgeAsset, Status, StatusTransition, VersionOrigin

from .conftest import SAMPLE, publish


def test_publish_creates_draft_with_version_and_audit(client, db):
    detail = publish(client)

    assert detail["status"] == "DRAFT"
    assert detail["tier"] == "note"            # 发布一律是工作记录
    assert detail["author_id"] == "wanglei"
    assert detail["code"] == f"KA-{detail['id']:03d}"

    # 首个版本：seq=1、created_from=author、正文原样落库
    assert detail["current_version"]["seq"] == 1
    assert detail["current_version"]["created_from"] == "author"
    assert detail["current_version"]["body_md"] == SAMPLE["body_md"]
    assert len(detail["versions"]) == 1

    asset = db.get(KnowledgeAsset, detail["id"])
    version = db.query(AssetVersion).filter_by(asset_id=asset.id).one()
    assert asset.current_version_id == version.id
    assert version.created_from is VersionOrigin.author

    # 唯一一条流水：→DRAFT，证据指向首个版本
    rows = db.query(StatusTransition).filter_by(asset_id=asset.id).all()
    assert len(rows) == 1
    assert rows[0].from_status is None and rows[0].to_status is Status.DRAFT
    assert rows[0].evidence_type == "asset_version" and rows[0].evidence_id == version.id
    assert rows[0].actor == "wanglei"


def test_publish_links_models_framework_and_code_refs(client):
    detail = publish(client)

    assert detail["models"] == ["Qwen3-30B-A3B"]
    assert detail["frameworks"] == [{
        "name": "vllm-ascend", "repo_url": "",
        "version_min": "", "version_max": "", "verified_on": "v0.10.0rc1",
    }]
    assert detail["env_note"] == "CANN 8.2.RC1 · Atlas 800I A2"
    assert len(detail["code_refs"]) == 1
    assert detail["code_refs"][0]["path_or_key"] == "vllm/v1/core/sched/scheduler.py"
    assert detail["code_refs"][0]["kind"] == "repo_path"
    assert detail["code_refs"][0]["watch"] is True


def test_publish_derives_summary_from_conclusion(client):
    detail = publish(client)
    assert detail["summary"].startswith("max_num_batched_tokens 调至 8192")
    assert "`" not in detail["summary"]              # 反引号已剥掉
    assert len(detail["summary"]) <= 140


def _naive_times(obj):
    """去掉 ISO 时间串尾部的 Z 再比较。

    sqlite 不存时区：POST 响应序列化的是内存里带 tzinfo 的值（带 Z），
    GET 是从库里读回来的裸值（不带 Z）。PostgreSQL 上 timestamptz 会原样往返，不存在这个差异。
    """
    if isinstance(obj, dict):
        return {k: _naive_times(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_naive_times(v) for v in obj]
    return obj[:-1] if isinstance(obj, str) and obj.endswith("Z") and "T" in obj else obj


def test_get_asset_returns_full_detail(client):
    created = publish(client)
    detail = client.get(f"/api/v1/assets/{created['id']}").json()
    assert _naive_times(detail) == _naive_times(created)


def test_get_asset_404_uses_error_envelope(client):
    resp = client.get("/api/v1/assets/9999")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_transitions_endpoint_returns_audit_trail(client):
    created = publish(client)
    rows = client.get(f"/api/v1/assets/{created['id']}/transitions").json()
    assert len(rows) == 1
    assert rows[0]["from_status"] is None
    assert rows[0]["to_status"] == "DRAFT"
    assert rows[0]["trigger"] == "auto_create"
    assert rows[0]["evidence_type"] == "asset_version"
    assert rows[0]["evidence_id"] == created["current_version"]["id"]


def test_transitions_404_for_unknown_asset(client):
    assert client.get("/api/v1/assets/9999/transitions").status_code == 404


def test_publish_reuses_existing_framework_and_model_rows(client, db):
    from app.models import Framework, Model

    publish(client)
    publish(client, title="第二条记录")
    assert db.query(Framework).count() == 1
    assert db.query(Model).count() == 1


def test_split_version_range():
    assert split_version_range("v0.9.1–v0.9.2") == ("v0.9.1", "v0.9.2")
    assert split_version_range("v0.9.2") == ("", "")
    # 带后缀的版本不能被 ASCII 连字符错切成区间
    assert split_version_range("v0.4.2-patch") == ("", "")


def test_derive_summary_falls_back_without_conclusion_section():
    assert _derive_summary("## 问题\n\n只有问题一节\n") == "只有问题一节"
    assert _derive_summary("没有任何小节标题的裸正文") == "没有任何小节标题的裸正文"


def test_oversized_fields_rejected_with_422(client):
    """列宽在入口就挡住：sqlite 不校验 VARCHAR 长度，只有 PG 会在插入时炸。"""
    resp = client.post("/api/v1/assets", json={**SAMPLE, "title": "长" * 301},
                       headers={"X-User": "wanglei"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    resp = client.post("/api/v1/assets", json={**SAMPLE, "env_note": "x" * 201},
                       headers={"X-User": "wanglei"})
    assert resp.status_code == 422
