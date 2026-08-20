"""混合检索：召回（M2 接 PG 全文 + pgvector）+ 业务重排（本文件已实现公式）。

公式来源 docs/design.md §5：
    final = rel + trust + fit + fresh + proof
调权重只改 WEIGHTS 并同步设计文档。每个分项都要返回给前端（排序可解释）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..models import KnowledgeAsset, Status

WEIGHTS = {
    "rel_cap": 30.0,           # RRF 归一化上限
    "trust": {Status.VERIFIED: 14.0, Status.DRAFT: 0.0, Status.REVIEW_DUE: -10.0},
    "fw_match": 6.0, "fw_mismatch": -8.0,
    "model_match": 6.0, "model_mismatch": -8.0,
    "version_hit": 4.0,
    "fresh": [(30, 5.0), (90, 3.0), (180, 1.0)],   # (天数上限, 加分)
    "proof_per_reuse": 0.4, "proof_cap": 8.0,
}


@dataclass
class ScorePart:
    label: str
    value: float


@dataclass
class Score:
    total: float = 0.0
    parts: list[ScorePart] = field(default_factory=list)

    def add(self, label: str, value: float) -> None:
        if value:
            self.total += value
            self.parts.append(ScorePart(label, round(value, 1)))


def rerank(
    asset: KnowledgeAsset,
    rel: float,
    *,
    fw_filter: str | None = None,
    model_filter: str | None = None,
    asset_fw_names: set[str] = frozenset(),
    asset_model_names: set[str] = frozenset(),
    version_hit: bool = False,
    now: datetime | None = None,
) -> Score:
    """对单条召回结果计算业务重排分。rel 为 RRF 融合后的相关性（0–rel_cap）。

    STALE/ARCHIVED 不应进入本函数（在召回层就被隔离，历史模式除外）。
    """
    s = Score()
    s.add("关键词+语义", min(rel, WEIGHTS["rel_cap"]))

    trust = WEIGHTS["trust"].get(asset.status)
    if trust is not None:
        s.add(f"状态 {asset.status.value}", trust)

    if fw_filter:
        hit = fw_filter in asset_fw_names or "通用" in asset_fw_names
        s.add("框架匹配" if hit else "框架不符", WEIGHTS["fw_match"] if hit else WEIGHTS["fw_mismatch"])
    if model_filter:
        hit = model_filter in asset_model_names or "通用" in asset_model_names
        s.add("模型匹配" if hit else "模型不符", WEIGHTS["model_match"] if hit else WEIGHTS["model_mismatch"])
    if version_hit:
        s.add("版本区间命中", WEIGHTS["version_hit"])

    now = now or datetime.now(timezone.utc)
    updated = asset.updated_at if asset.updated_at.tzinfo else asset.updated_at.replace(tzinfo=timezone.utc)
    days = (now - updated).days
    for limit, bonus in WEIGHTS["fresh"]:
        if days < limit:
            s.add(f"更新 {days}d", bonus)
            break

    proof = min(asset.reuse_count * WEIGHTS["proof_per_reuse"], WEIGHTS["proof_cap"])
    s.add(f"复用×{asset.reuse_count}", proof)

    s.total = round(s.total, 1)
    return s


# ---- 召回（M2 实现） ----
# TODO(M2): bm25_recall(db, query, hist) -> list[(asset_id, rank)]   PG 全文（zhparser/jieba）
# TODO(M2): vector_recall(db, query, hist) -> list[(asset_id, rank)] pgvector <=> bge-m3
# TODO(M2): rrf_fuse(bm25, vec, k=60) -> dict[asset_id, rel]
# 召回层负责隔离 STALE/ARCHIVED（hist=False 时 WHERE status NOT IN (...)）。
