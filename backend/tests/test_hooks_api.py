"""POST /hooks/git：签名校验、事件解析、CodeReference 匹配、24h 去抖、AI 回填与降级。

conftest 的 SAMPLE 资产自带一条 watch 引用：repo=vllm-project/vllm，
path=vllm/v1/core/sched/scheduler.py —— 大部分用例围绕它做命中/不命中。
"""
from __future__ import annotations

import hashlib
import hmac
import json

from sqlalchemy import select

from app.config import settings
from app.models import AssetVersion, CodeReference, ReviewTask, StatusTransition
from app.services import ai

from .conftest import publish

HOOK = "/api/v1/hooks/git"
WATCHED = "vllm/v1/core/sched/scheduler.py"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(settings.webhook_secret.encode(), body, hashlib.sha256).hexdigest()


def post_github(client, payload: dict, *, event: str = "push", bad_sig: bool = False):
    body = json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": "sha256=" + "0" * 64 if bad_sig else _sign(body),
        "Content-Type": "application/json",
    }
    return client.post(HOOK, content=body, headers=headers)


def post_gitlab(client, payload: dict, *, token: str | None = None):
    body = json.dumps(payload).encode()
    headers = {"X-Gitlab-Token": token or settings.webhook_secret, "Content-Type": "application/json"}
    return client.post(HOOK, content=body, headers=headers)


def push_payload(files=(WATCHED,), repo="vllm-project/vllm",
                 message="refactor scheduler", ref="refs/heads/main", sha="a" * 40):
    return {
        "ref": ref,
        "after": sha,
        "compare": "https://github.com/vllm-project/vllm/compare/111...222",
        "repository": {"full_name": repo},
        "commits": [{
            "id": sha, "message": message,
            "added": [], "modified": list(files), "removed": [],
        }],
    }


def pr_payload(*, merged=True, body_text=f"touches {WATCHED}", repo="vllm-project/vllm"):
    return {
        "action": "closed",
        "number": 17332,
        "repository": {"full_name": repo},
        "pull_request": {
            "merged": merged,
            "title": "V1: move EngineCore into per-request threads",
            "body": body_text,
            "merge_commit_sha": "b" * 40,
            "html_url": "https://github.com/vllm-project/vllm/pull/17332",
            "base": {"repo": {"full_name": repo}},
        },
    }


# ---------- 签名 ----------

def test_missing_signature_401(client):
    resp = client.post(HOOK, content=b"{}", headers={"Content-Type": "application/json"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "MISSING_SIGNATURE"


def test_bad_github_signature_401(client):
    resp = post_github(client, push_payload(), bad_sig=True)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_SIGNATURE"


def test_bad_gitlab_token_401(client):
    resp = post_gitlab(client, {"object_kind": "push"}, token="wrong")
    assert resp.status_code == 401


# ---------- 命中与去抖 ----------

def test_push_matches_watch_path(client, db):
    asset = publish(client)
    resp = post_github(client, push_payload())
    assert resp.status_code == 200
    data = resp.json()
    assert data["handled"] and data["event"] == "push" and data["matched_refs"] == 1
    assert data["tasks"] == [{
        "review_task_id": data["tasks"][0]["review_task_id"],
        "asset_id": asset["id"], "created": True,
    }]

    detail = client.get(f"/api/v1/assets/{asset['id']}").json()
    assert detail["status"] == "REVIEW_DUE"
    task = db.get(ReviewTask, data["tasks"][0]["review_task_id"])
    assert task.trigger.value == "code_change"
    assert "vllm-project/vllm" in task.trigger_detail
    assert task.diff_ref.startswith("https://github.com/")
    assert task.priority >= 1


def test_push_debounce_merges_within_window(client, db):
    asset = publish(client)
    first = post_github(client, push_payload(message="commit 1")).json()
    second = post_github(client, push_payload(message="commit 2")).json()
    assert first["tasks"][0]["created"] is True
    assert second["tasks"][0]["created"] is False
    assert second["tasks"][0]["review_task_id"] == first["tasks"][0]["review_task_id"]

    open_tasks = db.scalars(select(ReviewTask).where(ReviewTask.state == "open")).all()
    assert len(open_tasks) == 1
    assert "[合并]" in open_tasks[0].trigger_detail
    # 状态只流转一次
    moves = db.scalars(select(StatusTransition).where(
        StatusTransition.asset_id == asset["id"],
        StatusTransition.to_status == "REVIEW_DUE",
    )).all()
    assert len(moves) == 1


def test_push_unrelated_file_no_task(client, db):
    publish(client)
    resp = post_github(client, push_payload(files=("docs/README.md",)))
    data = resp.json()
    assert data["handled"] and data["matched_refs"] == 0 and data["tasks"] == []
    assert db.scalars(select(ReviewTask)).all() == []


def test_push_wrong_repo_no_task(client, db):
    publish(client)
    data = post_github(client, push_payload(repo="someone/else")).json()
    assert data["matched_refs"] == 0
    assert db.scalars(select(ReviewTask)).all() == []


def test_watch_false_not_matched(client, db):
    publish(client, code_refs=[{
        "kind": "repo_path", "repo": "vllm-project/vllm",
        "path_or_key": WATCHED, "note": "", "watch": False,
    }])
    data = post_github(client, push_payload()).json()
    assert data["matched_refs"] == 0


def test_config_key_matched_via_commit_message(client, db):
    publish(client, code_refs=[{
        "kind": "config_key", "repo": "", "path_or_key": "max_num_batched_tokens",
        "note": "", "watch": True,
    }])
    data = post_github(client, push_payload(
        files=("vllm/config.py",),
        message="bump default max_num_batched_tokens to 8192",
    )).json()
    assert data["matched_refs"] == 1 and len(data["tasks"]) == 1


def test_pr_merged_matches_body_text(client, db):
    asset = publish(client)
    data = post_github(client, pr_payload(), event="pull_request").json()
    assert data["handled"] and data["event"] == "pr"
    assert data["tasks"][0]["asset_id"] == asset["id"]
    task = db.get(ReviewTask, data["tasks"][0]["review_task_id"])
    assert "PR #17332" in task.trigger_detail


def test_pr_not_merged_ignored(client, db):
    publish(client)
    data = post_github(client, pr_payload(merged=False), event="pull_request").json()
    assert data["handled"] is False
    assert db.scalars(select(ReviewTask)).all() == []


def test_tag_push_triggers_version_change_for_repo(client, db):
    asset = publish(client)
    data = post_github(client, push_payload(
        files=(), ref="refs/tags/v0.11.0", message="release v0.11.0",
    )).json()
    assert data["event"] == "tag"
    assert data["tasks"][0]["asset_id"] == asset["id"]
    task = db.get(ReviewTask, data["tasks"][0]["review_task_id"])
    assert task.trigger.value == "version_change"


def test_unknown_event_ignored(client):
    data = post_github(client, {"zen": "Design for failure."}, event="issues").json()
    assert data["handled"] is False and "issues" in data["reason"]


def test_gitlab_push_matches(client, db):
    asset = publish(client)
    data = post_gitlab(client, {
        "object_kind": "push",
        "ref": "refs/heads/main",
        "before": "0" * 40,
        "after": "c" * 40,
        "checkout_sha": "c" * 40,
        "project": {"path_with_namespace": "vllm-project/vllm",
                    "web_url": "https://gitlab.example.com/vllm-project/vllm"},
        "commits": [{"id": "c" * 40, "message": "tune scheduler",
                     "added": [], "modified": [WATCHED], "removed": []}],
    }).json()
    assert data["handled"] and data["tasks"][0]["asset_id"] == asset["id"]


def test_stale_asset_skipped(client, db):
    """死状态不进复核队列：先把资产走到 STALE，再推事件 —— 命中但不建任务。"""
    asset = publish(client)
    task_id = client.post("/api/v1/feedback/stale", json={"asset_id": asset["id"]},
                          headers={"X-User": "lihao"}).json()["review_task_id"]
    resp = client.post(f"/api/v1/review/{task_id}/resolve", json={"action": "stale"},
                       headers={"X-User": "lihao"})
    assert resp.status_code == 200 and resp.json()["status"] == "STALE"

    data = post_github(client, push_payload()).json()
    assert data["matched_refs"] == 1 and data["tasks"] == []
    assert db.scalars(select(ReviewTask).where(ReviewTask.state == "open")).all() == []


def test_last_seen_sha_updated(client, db):
    asset = publish(client)
    post_github(client, push_payload(sha="d" * 40))
    ref = db.scalar(select(CodeReference).where(CodeReference.asset_id == asset["id"]))
    assert ref.last_seen_sha == "d" * 40


# ---------- AI 回填与降级 ----------

def test_ai_downgrade_task_still_created(client, db):
    """网关不可用（conftest 默认 off）：任务照建，只是没有摘要和草稿。"""
    asset = publish(client)
    # 有一次非作者复用：既是治理过滤的「近 90 天有使用」，也让该任务能进 GET /review
    client.post("/api/v1/feedback/useful", json={"asset_id": asset["id"]},
                headers={"X-User": "lihao"})
    data = post_github(client, push_payload()).json()
    task = db.get(ReviewTask, data["tasks"][0]["review_task_id"])
    assert task.ai_impact_summary == "" and task.ai_draft_version_id is None
    # 没草稿时 GET /review 的 ai_draft 是空串
    items = client.get("/api/v1/review").json()["items"]
    mine = next(i for i in items if i["asset"]["id"] == asset["id"])
    assert mine["ai_draft"] == "" and mine["ai_draft_version_id"] is None


def test_ai_attach_fills_summary_and_draft(client, db, monkeypatch):
    monkeypatch.setattr(ai, "impact_summary", lambda body, chg: "第 1 节的调度描述可能失效")
    monkeypatch.setattr(ai, "update_draft", lambda body, chg: "## 结论\n\n预算调度已改为零拷贝直通（待验证）。")
    asset = publish(client)
    before_version = asset["current_version"]["id"]

    data = post_github(client, push_payload()).json()
    task = db.get(ReviewTask, data["tasks"][0]["review_task_id"])
    assert task.ai_impact_summary == "第 1 节的调度描述可能失效"
    draft = db.get(AssetVersion, task.ai_draft_version_id)
    assert draft.created_from.value == "ai_draft" and draft.created_by == "ai"

    # 硬规则 1：草稿归草稿，current_version 纹丝不动
    detail = client.get(f"/api/v1/assets/{asset['id']}").json()
    assert detail["current_version"]["id"] == before_version
    assert any(v["created_from"] == "ai_draft" for v in detail["versions"])


def test_merge_backfills_missing_ai(client, db, monkeypatch):
    """第一次网关瞬断没生成，去抖合并的第二次事件补上 —— 不重复生成已有的。"""
    publish(client)
    first = post_github(client, push_payload(message="commit 1")).json()
    task = db.get(ReviewTask, first["tasks"][0]["review_task_id"])
    assert task.ai_impact_summary == ""

    monkeypatch.setattr(ai, "impact_summary", lambda body, chg: "补上的摘要")
    monkeypatch.setattr(ai, "update_draft", lambda body, chg: "补上的草稿")
    second = post_github(client, push_payload(message="commit 2")).json()
    assert second["tasks"][0]["review_task_id"] == task.id
    db.expire_all()
    task = db.get(ReviewTask, task.id)
    assert task.ai_impact_summary == "补上的摘要"
    assert db.get(AssetVersion, task.ai_draft_version_id).body_md == "补上的草稿"
