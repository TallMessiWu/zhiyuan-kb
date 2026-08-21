"""混合检索：召回（recall.py）+ 业务重排（本文件）+ 编排 run_search()。

公式来源 docs/design.md §5：
    final = rel + trust + fit + fresh + proof
调权重只改 WEIGHTS 并同步设计文档。每个分项都要返回给前端（排序可解释）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    AssetFramework,
    AssetModel,
    Direction,
    Framework,
    KnowledgeAsset,
    Model,
    Status,
)
from . import recall
from .text import highlight_terms, query_terms

WEIGHTS = {
    "rel_cap": 30.0,           # RRF 归一化上限
    "trust": {Status.VERIFIED: 14.0, Status.DRAFT: 0.0, Status.REVIEW_DUE: -10.0},
    "fw_match": 6.0, "fw_mismatch": -8.0,
    "model_match": 6.0, "model_mismatch": -8.0,
    "version_hit": 4.0,
    "fresh": [(30, 5.0), (90, 3.0), (180, 1.0)],   # (天数上限, 加分)
    "proof_per_reuse": 0.4, "proof_cap": 8.0,
}

# 浏览模式（无查询词）一次最多铺多少条候选进重排 —— 库大了不至于把整库拉进内存。
BROWSE_CANDIDATES = 200


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


# ---- 框架推断与版本区间命中 ----

# 查询词里出现这些片段就推断出框架（原型 scoreAsset 的 fwQ 逻辑）。推断出来的框架是**软**
# 信号，只加减分；用户在筛选器里显式选的框架才硬过滤（docs/design.md §5「显式筛选除外」）。
FRAMEWORK_HINTS = (("sglang", "sglang"), ("ascend", "vllm-ascend"), ("vllm", "vllm-ascend"))

_VERSION = re.compile(r"^v?(\d+(?:\.\d+)+)")


def infer_framework(q: str) -> str | None:
    low = q.lower()
    return next((name for needle, name in FRAMEWORK_HINTS if needle in low), None)


def version_key(value: str) -> tuple[int, ...] | None:
    """把 v0.10.0rc1 归一成 (0, 10, 0) 供区间比较；取不到数字段就返回 None。

    只比数字前缀，不解析 rc/post 后缀 —— 预发布版排序规则在各框架里并不统一，
    与其猜错不如让它落在同一个数字点上（区间判断仍然成立）。
    """
    m = _VERSION.match(value.strip())
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def _pad(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


def version_hit(terms: list[str], rows: list[AssetFramework]) -> bool:
    """查询里带的版本号是否落在资产声明的适用区间内（或正好是实测版本）。"""
    query_versions = [key for key in (version_key(t) for t in terms) if key]
    if not query_versions:
        return False
    for row in rows:
        lo, hi, on = version_key(row.version_min), version_key(row.version_max), version_key(row.verified_on)
        for qv in query_versions:
            if on and _pad(qv, on)[0] == _pad(qv, on)[1]:
                return True
            if lo and hi:
                a, b = _pad(qv, lo)
                c, d = _pad(qv, hi)
                if a >= b and c <= d:
                    return True
    return False


# ---- 批量取维度（避免逐条查库） ----

def load_framework_rows(db: Session, asset_ids: list[int]) -> dict[int, list[tuple[str, AssetFramework]]]:
    """asset_id -> [(框架名, 关联行)]。名字用于匹配加分，关联行里的版本用于区间命中。"""
    stmt = (
        select(Framework.name, AssetFramework)
        .join(AssetFramework, AssetFramework.framework_id == Framework.id)
        .where(AssetFramework.asset_id.in_(asset_ids))
        .order_by(AssetFramework.id)
    )
    out: dict[int, list[tuple[str, AssetFramework]]] = {}
    for name, row in db.execute(stmt):
        out.setdefault(row.asset_id, []).append((name, row))
    return out


def load_model_names(db: Session, asset_ids: list[int]) -> dict[int, list[str]]:
    stmt = (
        select(AssetModel.asset_id, Model.name)
        .join(Model, Model.id == AssetModel.model_id)
        .where(AssetModel.asset_id.in_(asset_ids))
        .order_by(AssetModel.id)
    )
    out: dict[int, list[str]] = {}
    for asset_id, name in db.execute(stmt):
        out.setdefault(asset_id, []).append(name)
    return out


def framework_label(rows: list[tuple[str, AssetFramework]]) -> tuple[str, str]:
    """列表行要展示的「框架 + 版本」：优先取实测版本，否则退回 min–max 区间。
    多个框架时取第一个非「通用」的 —— 与详情页右栏的展示口径一致。"""
    if not rows:
        return "", ""
    name, row = next((r for r in rows if r[0] != "通用"), rows[0])
    version = row.verified_on or "–".join(v for v in (row.version_min, row.version_max) if v)
    return name, version


# ---- 编排 ----

@dataclass
class ScoredAsset:
    """一条结果：资产 + 分项得分 + 列表行要用的维度（免得前端为每条再拉详情）。"""

    asset: KnowledgeAsset
    score: Score
    framework: str
    fw_version: str
    models: list[str]


@dataclass
class SearchOutcome:
    items: list[ScoredAsset]
    total: int
    terms: list[str]                 # 给前端做 <mark> 高亮
    recall_backends: dict[str, str]  # {"keyword": ..., "vector": ...}
    recall_hits: dict[str, int]


def run_search(
    db: Session,
    *,
    q: str = "",
    direction: Direction | None = None,
    model: str | None = None,
    framework: str | None = None,
    status: Status | None = None,
    hist: bool = False,
    limit: int = 20,
    offset: int = 0,
    now: datetime | None = None,
) -> SearchOutcome:
    """双路召回 → RRF → 业务重排 → 分页。不落 SearchEvent（那是 api 层的事）。"""
    terms = query_terms(q)
    conds = recall.candidate_filters(
        hist=hist, direction=direction, status=status, framework=framework, model=model
    )

    if terms:
        keyword_ids = recall.keyword_recall(db, terms, conds, limit=settings.recall_limit)
        vector_ids, vector_backend = recall.vector_recall(db, q, conds, limit=settings.recall_limit)
        rel = recall.rrf_fuse([keyword_ids, vector_ids], cap=WEIGHTS["rel_cap"])
        candidate_ids = list(rel)
        hits = {"keyword": len(keyword_ids), "vector": len(vector_ids)}
    else:
        # 浏览模式：没有查询词就不召回，直接按状态+新鲜度+复用把候选铺开重排（对齐原型）
        candidate_ids = list(db.scalars(
            select(KnowledgeAsset.id).where(*conds)
            .order_by(KnowledgeAsset.updated_at.desc()).limit(BROWSE_CANDIDATES)
        ).all())
        rel, vector_backend = {}, "off"
        hits = {"keyword": 0, "vector": 0}

    backends = {"keyword": recall.capabilities(db).keyword, "vector": vector_backend}
    if not candidate_ids:
        return SearchOutcome([], 0, highlight_terms(terms), backends, hits)

    assets = {a.id: a for a in db.scalars(
        select(KnowledgeAsset).where(KnowledgeAsset.id.in_(candidate_ids))
    ).all()}
    fw_rows = load_framework_rows(db, candidate_ids)
    model_names = load_model_names(db, candidate_ids)

    # 显式筛选已在召回层硬过滤掉不匹配的资产，这里只对「从查询词里猜出来的框架」做加减分
    inferred_fw = None if framework else infer_framework(q)

    scored: list[ScoredAsset] = []
    for asset_id in candidate_ids:
        asset = assets.get(asset_id)
        if asset is None:                     # 召回与取数之间被删了；跳过而不是报错
            continue
        rows = fw_rows.get(asset_id, [])
        models = model_names.get(asset_id, [])
        score = rerank(
            asset, rel.get(asset_id, 0.0),
            fw_filter=inferred_fw,
            asset_fw_names={name for name, _ in rows},
            asset_model_names=set(models),
            version_hit=version_hit(terms, [row for _, row in rows]),
            now=now,
        )
        name, version = framework_label(rows)
        scored.append(ScoredAsset(asset, score, name, version, models))

    scored.sort(key=lambda item: (-item.score.total, item.asset.id))
    return SearchOutcome(
        items=scored[offset:offset + limit],
        total=len(scored),
        terms=highlight_terms(terms),
        recall_backends=backends,
        recall_hits=hits,
    )
