"""API 测试夹具 — sqlite 内存库 + get_db 依赖覆盖，不依赖 PG。

同一个连接复用给整个用例（StaticPool），这样 TestClient 里多次请求看到同一份内存库。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture()
def client(session_factory):
    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def db(session_factory):
    """需要直接查库断言时用（与 client 共享同一个 sqlite 内存库）。"""
    s = session_factory()
    yield s
    s.close()


SAMPLE = {
    "title": "Qwen3-30B-A3B 在 800I A2 单机 TP4 上 TTFT 异常（>8s）",
    "direction": "feature",
    "body_md": (
        "## 问题\n\n默认预算下长 prompt 被切成 4 块串行 prefill。\n\n"
        "## 环境\n\nvllm-ascend v0.10.0rc1 · CANN 8.2.RC1 · TP4\n\n"
        "## 结论\n\n`max_num_batched_tokens` 调至 8192 后 TTFT 由 8s+ 降至 1.9s，"
        "注意预算变化会使 aclgraph 捕获桶失效需重新 warmup。\n"
    ),
    "models": ["Qwen3-30B-A3B"],
    "framework": "vllm-ascend",
    "fw_version": "v0.10.0rc1",
    "env_note": "CANN 8.2.RC1 · Atlas 800I A2",
    "tags": ["ttft", "调度"],
    "source": "ai_session",
    "source_ref": "claude-code#8f3a",
    "code_refs": [{
        "kind": "repo_path",
        "repo": "vllm-project/vllm",
        "path_or_key": "vllm/v1/core/sched/scheduler.py",
        "note": "token 预算调度",
        "watch": True,
    }],
}


def publish(client, user="wanglei", **overrides):
    """发布一条 DRAFT，返回详情 dict。"""
    body = {**SAMPLE, **overrides}
    resp = client.post("/api/v1/assets", json=body, headers={"X-User": user})
    assert resp.status_code == 201, resp.text
    return resp.json()
