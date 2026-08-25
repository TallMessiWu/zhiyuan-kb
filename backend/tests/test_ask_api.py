"""POST /ask 端到端 —— §6 五条硬性规则 + 降级语义 + SearchEvent(mode=qa) 落库。

conftest 的 autouse 夹具已把 AI 开关关掉（chat 会返回 None）；需要「生成成功」的用例
直接 monkeypatch ai.chat 整个函数（backend/CLAUDE.md：调用方一律 ai.chat(...) 模块属性，
就是为了这里打得上桩）。降级用例什么都不打 —— off 开关下 chat 返回 None 就是网关不可用。
"""
import json

from sqlalchemy import select, update

from app.config import settings
from app.models import KnowledgeAsset, ReviewTask, SearchEvent, Status, Trigger
from app.services import ai, state_machine

from .conftest import publish

MLA = {
    "title": "DeepSeek-V3 MLA 在图模式下的算子限制",
    "direction": "model",
    "body_md": (
        "## 问题\n\nMLA 走 aclgraph 捕获时部分算子回退到 eager。\n\n"
        "## 环境\n\nvllm-ascend v0.9.1 · CANN 8.2.RC1\n\n"
        "## 结论\n\nMLA 不需要手动开启，识别到结构后自动选择 AscendMLABackend。\n"
    ),
    "models": ["DeepSeek-V3"],
    "framework": "vllm-ascend",
    "fw_version": "v0.9.1",
    "tags": ["mla", "图模式"],
}


def ask(client, question, user="zhangsan", expect=200):
    resp = client.post("/api/v1/ask", json={"question": question}, headers={"X-User": user})
    assert resp.status_code == expect, resp.text
    return resp.json()


def stub_chat(monkeypatch, payload, calls=None):
    """把 ai.chat 换成固定 JSON 输出；calls 传 list 可以数被调了几次。"""
    def fake(prompt, system="", **kw):
        if calls is not None:
            calls.append(prompt)
        return json.dumps(payload, ensure_ascii=False)
    monkeypatch.setattr(ai, "chat", fake)


def answer_payload(index=1, fragment="", **extra):
    return {
        "answer_md": "MLA 不需要手动开启（KA-001），自动选择 AscendMLABackend。",
        "citations": [{"index": index, "fragment": fragment}],
        "conflict": None,
        "insufficient": False,
        **extra,
    }


# ---------- 正常回答与引用（规则 1） ----------

def test_ask_answers_with_full_citation_metadata(client, db, monkeypatch):
    asset = publish(client, user="wanglei", **MLA)
    frag = "MLA 不需要手动开启，识别到结构后自动选择 AscendMLABackend。"
    stub_chat(monkeypatch, answer_payload(fragment=frag))

    data = ask(client, "DeepSeek MLA 怎么开启？")

    assert data["not_found"] is False
    assert "AscendMLABackend" in data["answer_md"]
    (cite,) = data["citations"]
    # §6 规则 1：引用块必须含 资产/命中段落/状态/适用版本/更新时间
    assert cite["asset_id"] == asset["id"] and cite["code"] == f"KA-{asset['id']:03d}"
    assert cite["fragment"] == frag
    assert cite["status"] == "DRAFT"
    assert cite["framework"] == "vllm-ascend" and cite["fw_version"] == "v0.9.1"
    assert cite["title"] == MLA["title"] and cite["updated_at"]

    # 问答会话是需求事件（§9 分母）：mode=qa 落库，id 随响应返回供「记缺口」使用
    event = db.get(SearchEvent, data["search_event_id"])
    assert event.mode == "qa" and event.user_id == "zhangsan"
    assert event.result_ids == [asset["id"]]


def test_ask_fabricated_fragment_falls_back_to_real_text(client, monkeypatch):
    """「逐字摘录」校验：LLM 编的引用片段要被服务端换成正文里真实存在的段落。"""
    publish(client, **MLA)
    stub_chat(monkeypatch, answer_payload(fragment="这句话在正文里根本不存在，是模型现编的。"))

    (cite,) = ask(client, "MLA 图模式 算子限制")["citations"]
    squash = lambda s: "".join(s.split())
    assert squash(cite["fragment"]) in squash(MLA["body_md"])


def test_ask_invalid_citation_index_means_no_evidence(client, monkeypatch):
    """引用索引全部无效 = 没有依据 —— 规则 2：没有依据就不许有答案。"""
    publish(client, **MLA)
    stub_chat(monkeypatch, answer_payload(index=99))

    data = ask(client, "MLA 图模式 算子限制")
    assert data["not_found"] is True
    assert data["answer_md"].startswith("没有找到经过验证的知识")


# ---------- 无据与阈值（规则 2） ----------

def test_ask_not_found_on_empty_library_without_calling_llm(client, db, monkeypatch):
    calls = []
    stub_chat(monkeypatch, answer_payload(), calls)

    data = ask(client, "PD 分离在 vllm-ascend 上怎么部署？")

    assert data["not_found"] is True
    assert data["answer_md"].startswith("没有找到经过验证的知识")
    assert data["citations"] == [] and data["conflict"] is None
    assert calls == []                       # 禁止通用知识补位：没命中就根本不调 LLM
    event = db.get(SearchEvent, data["search_event_id"])
    assert event.mode == "qa" and event.result_ids == []   # 零结果也是需求事件


def test_ask_low_rel_hits_do_not_count(client, monkeypatch):
    """阈值打在 rel 分项上：资产在库、也被召回，但相关性不够就不算命中。"""
    publish(client, **MLA)
    calls = []
    stub_chat(monkeypatch, answer_payload(), calls)
    monkeypatch.setattr(settings, "ask_min_rel", 999.0)

    data = ask(client, "MLA 图模式 算子限制")
    assert data["not_found"] is True and calls == []


def test_ask_llm_admitting_insufficient_becomes_not_found(client, monkeypatch):
    publish(client, **MLA)
    stub_chat(monkeypatch, {"answer_md": "", "citations": [], "insufficient": True})

    assert ask(client, "MLA 图模式 算子限制")["not_found"] is True


# ---------- STALE 隔离（规则 3） ----------

def test_ask_stale_assets_never_enter_context(client, db, monkeypatch):
    dead = publish(client, **MLA)
    asset = db.get(KnowledgeAsset, dead["id"])
    state_machine.transition(db, asset, Status.REVIEW_DUE, Trigger.user_feedback, actor="lisi")
    state_machine.transition(db, asset, Status.STALE, Trigger.review_stale, actor="lisi")
    db.commit()

    calls = []
    stub_chat(monkeypatch, answer_payload(), calls)
    data = ask(client, "MLA 图模式 算子限制")

    assert data["not_found"] is True and calls == []     # 召回层就隔离，连上下文都进不了


# ---------- 冲突并列（规则 4） ----------

def test_ask_conflict_sides_map_to_assets(client, monkeypatch):
    a = publish(client, **MLA)
    b = publish(client, **{**MLA, "title": "MLA 图模式限制的另一种说法：必须手动开启"})
    stub_chat(monkeypatch, {
        "answer_md": "两份资产结论互斥，差异如下，请自行核对版本。",
        "citations": [{"index": 1, "fragment": ""}, {"index": 2, "fragment": ""}],
        "conflict": {"a": {"index": 1, "stand": "自动启用"}, "b": {"index": 2, "stand": "必须手动开启"}},
        "insufficient": False,
    })

    data = ask(client, "MLA 图模式 算子限制 手动开启")
    assert data["conflict"] is not None
    got = {data["conflict"]["a"]["asset_id"], data["conflict"]["b"]["asset_id"]}
    assert got == {a["id"], b["id"]}
    assert data["conflict"]["a"]["stand"] == "自动启用"


def test_ask_conflict_with_invalid_side_is_dropped(client, monkeypatch):
    publish(client, **MLA)
    stub_chat(monkeypatch, answer_payload(
        fragment="", conflict={"a": {"index": 1, "stand": "x"}, "b": {"index": 42, "stand": "y"}},
    ))

    assert ask(client, "MLA 图模式 算子限制")["conflict"] is None


# ---------- REVIEW_DUE 风险提示（规则 5） ----------

def test_ask_review_due_citation_carries_risk_and_impact_summary(client, db, monkeypatch):
    detail = publish(client, **MLA)
    resp = client.post("/api/v1/feedback/stale",
                       json={"asset_id": detail["id"], "note": "CANN 已升级"},
                       headers={"X-User": "lisi"})
    assert resp.status_code == 200
    task_id = resp.json()["review_task_id"]
    # 网关关着，任务没有 AI 摘要；补一条模拟 M4 已生成的影响摘要
    db.execute(update(ReviewTask).where(ReviewTask.id == task_id)
               .values(ai_impact_summary="图缓存目录结构已变化，第 2 节可能失效"))
    db.commit()

    stub_chat(monkeypatch, answer_payload(
        fragment="MLA 不需要手动开启，识别到结构后自动选择 AscendMLABackend。"))
    data = ask(client, "MLA 图模式 算子限制")

    assert data["citations"][0]["status"] == "REVIEW_DUE"
    (risk,) = data["risks"]
    assert risk["type"] == "warn" and "可能过时" in risk["text"]
    assert risk["asset_id"] == detail["id"]
    assert risk["review_task_id"] == task_id
    assert risk["ai_impact_summary"] == "图缓存目录结构已变化，第 2 节可能失效"


# ---------- 降级语义（问答没有规则式兜底） ----------

def test_ask_gateway_down_returns_503_not_500(client, db):
    publish(client, **MLA)      # 有命中才会走到生成，降级点才暴露

    resp = client.post("/api/v1/ask", json={"question": "MLA 图模式 算子限制"},
                       headers={"X-User": "zhangsan"})
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "AI_UNAVAILABLE"
    assert "问答暂不可用" in body["error"]["message"]

    # 需求在提问那一刻已发生：503 的会话也要进分母
    event = db.scalar(select(SearchEvent).where(SearchEvent.mode == "qa"))
    assert event is not None and event.query == "MLA 图模式 算子限制"


def test_ask_retries_once_on_malformed_json(client, monkeypatch):
    publish(client, **MLA)
    outputs = ["这不是 JSON", json.dumps(answer_payload(
        fragment="MLA 不需要手动开启，识别到结构后自动选择 AscendMLABackend。"), ensure_ascii=False)]
    monkeypatch.setattr(ai, "chat", lambda prompt, system="", **kw: outputs.pop(0))

    data = ask(client, "MLA 图模式 算子限制")
    assert data["not_found"] is False and outputs == []


def test_ask_gives_up_after_two_malformed_outputs(client, monkeypatch):
    publish(client, **MLA)
    monkeypatch.setattr(ai, "chat", lambda prompt, system="", **kw: "始终不是 JSON")

    resp = client.post("/api/v1/ask", json={"question": "MLA 图模式 算子限制"},
                       headers={"X-User": "zhangsan"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "AI_UNAVAILABLE"


def test_ask_question_length_is_capped(client):
    resp = client.post("/api/v1/ask", json={"question": "长" * 501})
    assert resp.status_code == 422
