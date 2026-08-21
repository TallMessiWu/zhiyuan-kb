"""检索的纯函数部分：分词、RRF 融合、版本区间命中、框架推断、重排公式。

这些不碰数据库，是排序行为的「单元级」保障；跨库行为在 test_search_api.py。
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import AssetFramework, KnowledgeAsset, Status
from app.services.recall import rrf_fuse
from app.services.search import (
    WEIGHTS,
    framework_label,
    infer_framework,
    rerank,
    version_hit,
    version_key,
)
from app.services.text import highlight_terms, index_text, query_terms, tokenize

# ---------- 分词 ----------

def test_tokenize_splits_chinese_and_lowercases():
    tokens = tokenize("显存泄漏 与 MLA 图模式")
    assert "显存" in tokens and "泄漏" in tokens
    assert "mla" in tokens                      # 统一小写，查询侧才对得上
    assert "与" not in tokens                    # 停用词


def test_tokenize_keeps_versions_and_identifiers_whole():
    """jieba 会把版本号和下划线标识符切碎，而它们恰恰要整体命中。"""
    assert "v0.10.0rc1" in tokenize("升级到 v0.10.0rc1 后")
    assert "max_num_batched_tokens" in tokenize("把 max_num_batched_tokens 调到 8192")
    assert "vllm-ascend" in tokenize("vllm-ascend 的调度器")
    # 碎片也在，模糊召回不丢
    assert {"vllm", "ascend"} <= set(tokenize("vllm-ascend 的调度器"))


def test_tokenize_drops_single_latin_but_keeps_single_cjk():
    tokens = tokenize("a 核 b")
    assert tokens == ["核"]


def test_query_terms_dedupe_preserves_order():
    assert query_terms("显存 OOM 显存") == ["显存", "oom"]


def test_highlight_terms_drops_single_chars():
    assert highlight_terms(["图", "模式", "mla"]) == ["模式", "mla"]


def test_index_text_is_space_joined_tokens():
    assert index_text("显存泄漏") == "显存 泄漏"


# ---------- RRF ----------

def test_rrf_rewards_agreement_between_paths():
    """两路都排第一的，要压过只有一路排第一的。"""
    fused = rrf_fuse([[1, 2], [1, 3]])
    assert fused[1] > fused[2] and fused[1] > fused[3]


def test_rrf_normalises_by_active_paths_only():
    """只有一路可用时，第一名照样拿满 rel_cap —— 否则一降级 rel 就被系统性压低，
    trust/fresh 的相对权重被放大，排序会悄悄变形。"""
    assert rrf_fuse([[7, 8, 9], []])[7] == pytest.approx(30.0)
    assert rrf_fuse([[7], [7]])[7] == pytest.approx(30.0)


def test_rrf_gradient_is_meaningful_across_the_list():
    """k 选得太大会把 rel 压成平线；这里保证名次差异确实体现在分数上。"""
    fused = rrf_fuse([list(range(1, 51)), []])
    assert fused[1] - fused[10] > 10
    assert fused[50] < fused[1] / 2


def test_rrf_empty_input():
    assert rrf_fuse([[], []]) == {}


# ---------- 版本区间 ----------

def test_version_key_ignores_prerelease_suffix():
    assert version_key("v0.10.0rc1") == (0, 10, 0)
    assert version_key("2.5.1.post1") == (2, 5, 1)
    assert version_key("不适用") is None


def _fw(**kwargs) -> AssetFramework:
    return AssetFramework(asset_id=1, framework_id=1, **{
        "version_min": "", "version_max": "", "verified_on": "", **kwargs
    })


def test_version_hit_inside_declared_range():
    rows = [_fw(version_min="v0.9.1", version_max="v0.10.0")]
    assert version_hit(["v0.9.5"], rows) is True
    assert version_hit(["v0.11.0"], rows) is False


def test_version_hit_matches_verified_version_exactly():
    rows = [_fw(verified_on="v0.10.0rc1")]
    assert version_hit(["v0.10.0"], rows) is True     # rc 后缀不参与比较
    assert version_hit(["v0.9.0"], rows) is False


def test_version_hit_needs_a_version_in_the_query():
    assert version_hit(["显存"], [_fw(verified_on="v0.10.0")]) is False


# ---------- 框架推断与展示 ----------

@pytest.mark.parametrize(("query", "expected"), [
    ("sglang overlap 调度", "sglang"),
    ("vllm-ascend 显存", "vllm-ascend"),
    ("Ascend 图模式", "vllm-ascend"),
    ("显存泄漏", None),
])
def test_infer_framework(query, expected):
    assert infer_framework(query) == expected


def test_framework_label_prefers_verified_version_and_named_framework():
    rows = [("通用", _fw()), ("vllm-ascend", _fw(verified_on="v0.10.0rc1"))]
    assert framework_label(rows) == ("vllm-ascend", "v0.10.0rc1")


def test_framework_label_falls_back_to_range():
    assert framework_label([("sglang", _fw(version_min="v0.4.0", version_max="v0.4.6"))]) == (
        "sglang", "v0.4.0–v0.4.6"
    )


def test_framework_label_without_frameworks():
    assert framework_label([]) == ("", "")


# ---------- 重排公式 ----------

NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)


def _asset(status=Status.DRAFT, reuse_count=0, days_old=1) -> KnowledgeAsset:
    return KnowledgeAsset(
        id=1, title="t", direction="feature", status=status, author_id="a",
        reuse_count=reuse_count, updated_at=NOW - timedelta(days=days_old),
    )


def test_rerank_reports_every_part_so_ranking_is_explainable():
    score = rerank(_asset(Status.VERIFIED, reuse_count=5), 20.0, now=NOW)
    labels = [p.label for p in score.parts]
    assert labels[0] == "关键词+语义"
    assert any("VERIFIED" in label for label in labels)
    assert any("复用×5" == label for label in labels)
    assert score.total == pytest.approx(20 + 14 + 5 + 2.0)   # rel + trust + fresh + proof


def test_rerank_trust_ordering_matches_design():
    def total(status):
        return rerank(_asset(status), 10.0, now=NOW).total

    assert total(Status.VERIFIED) > total(Status.DRAFT) > total(Status.REVIEW_DUE)


def test_rerank_caps_relevance_and_proof():
    score = rerank(_asset(reuse_count=100), 999.0, now=NOW)
    parts = {p.label: p.value for p in score.parts}
    assert parts["关键词+语义"] == WEIGHTS["rel_cap"]
    assert parts["复用×100"] == WEIGHTS["proof_cap"]


def test_rerank_framework_mismatch_is_a_penalty_not_a_filter():
    hit = rerank(_asset(), 10.0, fw_filter="sglang", asset_fw_names={"sglang"}, now=NOW)
    miss = rerank(_asset(), 10.0, fw_filter="sglang", asset_fw_names={"vllm-ascend"}, now=NOW)
    assert hit.total - miss.total == pytest.approx(WEIGHTS["fw_match"] - WEIGHTS["fw_mismatch"])
    assert any("框架不符" in p.label for p in miss.parts)


def test_rerank_treats_generic_framework_as_match():
    score = rerank(_asset(), 10.0, fw_filter="sglang", asset_fw_names={"通用"}, now=NOW)
    assert any(p.label == "框架匹配" for p in score.parts)


def test_rerank_freshness_buckets():
    def fresh_part(days):
        parts = rerank(_asset(days_old=days), 0.0, now=NOW).parts
        return next((p.value for p in parts if p.label.startswith("更新")), 0.0)

    assert fresh_part(10) == 5.0
    assert fresh_part(60) == 3.0
    assert fresh_part(120) == 1.0
    assert fresh_part(400) == 0.0


# ---------- pgvector 语句（本机没有 PG 也要能挡住语法/类型错误） ----------

def test_pgvector_statement_compiles_for_postgres():
    """向量 ANN 查询只在 PG 上跑得动，但编译不该等到线上才炸。

    历史教训：把查询向量写成 CAST(:qv AS vector) 的文本片段，SQLAlchemy 在类型强制阶段
    就会抛 AttributeError —— 单测里编译一次就能挡住这类错误。
    """
    from sqlalchemy.dialects import postgresql

    from app.services.recall import candidate_filters, pgvector_stmt

    sql = str(pgvector_stmt([0.1, 0.2, 0.3], candidate_filters(), limit=5).compile(
        dialect=postgresql.dialect()
    ))
    assert "<=>" in sql
    assert "asset_embedding.vec IS NOT NULL" in sql
    assert "LIMIT" in sql
