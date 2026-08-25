"""ai.py 的网关接入层（M5-0）：Bearer 头、chat/embedding 端点分离、熔断隔离。

不碰真网络：把 httpx.Client 换成假的，记录请求参数、按脚本返回。
"""
from typing import ClassVar

import httpx
import pytest

from app.config import settings
from app.services import ai


class FakeClient:
    """记录构造参数与请求；行为由类属性脚本控制。"""

    created: ClassVar[list["FakeClient"]] = []
    fail_paths: ClassVar[set[str]] = set()

    def __init__(self, *, base_url="", headers=None, timeout=None):
        self.base_url = str(base_url)
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.posts: list[str] = []
        FakeClient.created.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, path, json=None):
        self.posts.append(path)
        if path in FakeClient.fail_paths:
            raise httpx.ConnectError("connection refused")
        if path == "/chat/completions":
            payload = {"choices": [{"message": {"content": "OK"}}]}
        else:
            payload = {"data": [{"embedding": [0.0] * 4} for _ in json["input"]]}
        return httpx.Response(200, json=payload, request=httpx.Request("POST", "http://x"))


@pytest.fixture(autouse=True)
def gateway(monkeypatch):
    """开开关、装假 Client、清熔断 —— 熔断是模块级状态，不清会串染别的用例。"""
    monkeypatch.setattr(settings, "ai_summary", "auto")
    monkeypatch.setattr(settings, "vector_search", "auto")
    monkeypatch.setattr(ai.httpx, "Client", FakeClient)
    FakeClient.created = []
    FakeClient.fail_paths = set()
    for circuit in ai._circuits.values():
        circuit.reset()
    yield
    for circuit in ai._circuits.values():
        circuit.reset()


def test_bearer_header_sent_when_key_is_set(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    assert ai.chat("hi") == "OK"
    assert FakeClient.created[0].headers["Authorization"] == "Bearer sk-test"


def test_no_auth_header_for_keyless_internal_gateway(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    ai.chat("hi")
    assert "Authorization" not in FakeClient.created[0].headers


def test_embedding_endpoint_and_key_can_differ_from_chat(monkeypatch):
    monkeypatch.setattr(settings, "llm_gateway_url", "https://chat.example/v1")
    monkeypatch.setattr(settings, "llm_api_key", "sk-chat")
    monkeypatch.setattr(settings, "embedding_gateway_url", "https://embed.example/v1")
    monkeypatch.setattr(settings, "embedding_api_key", "sk-embed")

    ai.chat("hi")
    ai.embed(["x"])

    chat_client, embed_client = FakeClient.created
    assert chat_client.base_url == "https://chat.example/v1"
    assert chat_client.headers["Authorization"] == "Bearer sk-chat"
    assert embed_client.base_url == "https://embed.example/v1"
    assert embed_client.headers["Authorization"] == "Bearer sk-embed"


def test_embedding_defaults_follow_the_main_gateway(monkeypatch):
    monkeypatch.setattr(settings, "llm_gateway_url", "https://main.example/v1")
    monkeypatch.setattr(settings, "llm_api_key", "sk-main")
    monkeypatch.setattr(settings, "embedding_gateway_url", "")
    monkeypatch.setattr(settings, "embedding_api_key", "")

    ai.embed(["x"])
    assert FakeClient.created[0].base_url == "https://main.example/v1"
    assert FakeClient.created[0].headers["Authorization"] == "Bearer sk-main"


def test_embedding_failure_does_not_trip_the_chat_circuit():
    """chat 与 embedding 可能一家可用一家不可用（DeepSeek 就没有 embedding），
    熔断必须按端点隔离 —— embedding 404 一次不许把问答也静默掉 60 秒。"""
    FakeClient.fail_paths = {"/embeddings"}

    assert ai.embed(["x"]) is None                    # embedding 熔断
    assert ai.chat("hi") == "OK"                      # chat 不受牵连
    assert not ai._circuits["embedding"].closed()
    assert ai._circuits["chat"].closed()


def test_generation_uses_long_timeout_and_retrieval_stays_short(monkeypatch):
    """chat（摘要/草稿/问答）默认走 generation_timeout；embed 在搜索同步路径上保持短超时。
    M5 实测：拿 6s 检索超时卡摘要会频繁超时→熔断，连带问答一起被降级 60s。"""
    monkeypatch.setattr(settings, "llm_timeout", 6.0)
    monkeypatch.setattr(settings, "generation_timeout", 30.0)
    ai.chat("hi")
    ai.embed(["x"])
    assert FakeClient.created[0].timeout == 30.0
    assert FakeClient.created[1].timeout == 6.0
