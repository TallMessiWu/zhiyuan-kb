"""双路召回（关键词 + 向量）与 RRF 融合 —— docs/design.md §5 的「召回」半边。
业务重排（final = rel + trust + fit + fresh + proof）在 search.py。

两路都按能力探测降级，任何一路不可用都不影响搜索可用性：

| 路 | 首选 | 降级 | 触发条件 |
|---|---|---|---|
| 关键词 | PG tsvector 生成列 + GIN | Python 加权词频 | 非 PG（测试用 sqlite）/ 迁移没跑 |
| 向量 | pgvector `<=>` + HNSW | Python 余弦（拉回内存算） | PG 没装 vector 扩展 |
| 向量 | —— | 整路跳过 | 网关不可达 / 没回填过向量 / 开关 off |

降级结果随响应返回（SearchResponse.recall）：排序可解释也包括「这次是怎么召回的」。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from functools import reduce

from pgvector.sqlalchemy import Vector
from sqlalchemy import Float, Select, bindparam, func, literal_column, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    AssetEmbedding,
    AssetFramework,
    AssetModel,
    AssetSearchDoc,
    Direction,
    Framework,
    KnowledgeAsset,
    Model,
    Status,
)
from . import ai

log = logging.getLogger(__name__)

# 默认不进搜索结果、不进 RAG 上下文（根 CLAUDE.md 规则 4）；hist=True 时反过来只看它俩。
HIDDEN_STATUSES = (Status.STALE, Status.ARCHIVED)

# 字段权重 title×4 / tags×3 / summary×2 / body×1（docs/design.md §5）。
# PG 侧由迁移里的 tsv 生成列用 setweight(A/B/C/D) 表达，这里是可移植路径的等价物。
FIELD_WEIGHTS = (
    (AssetSearchDoc.tok_title, 4.0),
    (AssetSearchDoc.tok_tags, 3.0),
    (AssetSearchDoc.tok_summary, 2.0),
    (AssetSearchDoc.tok_body, 1.0),
)


@dataclass(frozen=True)
class Capabilities:
    """当前库能提供什么。进程内缓存 —— 跑完迁移要重启服务才会重新探测。"""

    dialect: str
    keyword: str   # pg_tsvector | portable
    vector: str    # pgvector | python | off


_capabilities_cache: dict[str, Capabilities] = {}

_PROBE_SQL = sql_text("""
    SELECT
      EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'asset_search_doc' AND column_name = 'tsv'
                AND table_schema = ANY (current_schemas(false))) AS has_tsv,
      EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'asset_embedding' AND column_name = 'vec'
                AND table_schema = ANY (current_schemas(false))) AS has_vec
""")


def capabilities(db: Session) -> Capabilities:
    bind = db.get_bind()
    key = str(bind.url)                      # SQLAlchemy 2.0 的 str(URL) 已隐去密码
    cached = _capabilities_cache.get(key)
    if cached is not None:
        return cached

    dialect = bind.dialect.name
    vector_off = settings.vector_search.lower() == "off"
    if dialect == "postgresql":
        row = db.execute(_PROBE_SQL).one()
        caps = Capabilities(
            dialect=dialect,
            keyword="pg_tsvector" if row.has_tsv else "portable",
            vector="off" if vector_off else ("pgvector" if row.has_vec else "python"),
        )
        if not row.has_tsv:
            log.warning("asset_search_doc.tsv 不存在（迁移没跑完？），关键词召回降级为 Python 打分")
    else:
        caps = Capabilities(dialect=dialect, keyword="portable", vector="off" if vector_off else "python")

    _capabilities_cache[key] = caps
    return caps


def reset_capabilities_cache() -> None:
    """测试用：同一进程里换库（sqlite 内存库每个用例一个）时清缓存。"""
    _capabilities_cache.clear()


# ---- 候选集过滤 ----

def candidate_filters(
    *,
    hist: bool = False,
    direction: Direction | None = None,
    status: Status | None = None,
    framework: str | None = None,
    model: str | None = None,
) -> list:
    """硬过滤条件：历史隔离 + 方向 + 状态 + 显式框架/模型筛选。

    docs/design.md §5「筛选不匹配默认降权而非硬过滤（**显式筛选除外**）」：
    用户在筛选器里点的框架/模型是显式筛选，直接在召回层挡掉；
    从查询词里猜出来的框架是软信号，只在 search.rerank 里加减分。
    「通用」是保留值，任何框架/模型筛选都应当放它过。
    """
    conds = [
        KnowledgeAsset.status.in_(HIDDEN_STATUSES) if hist
        else KnowledgeAsset.status.notin_(HIDDEN_STATUSES)
    ]
    if direction is not None:
        conds.append(KnowledgeAsset.direction == direction)
    if status is not None:
        conds.append(KnowledgeAsset.status == status)
    if framework:
        conds.append(
            select(1).select_from(AssetFramework)
            .join(Framework, Framework.id == AssetFramework.framework_id)
            .where(AssetFramework.asset_id == KnowledgeAsset.id, Framework.name.in_((framework, "通用")))
            .exists()
        )
    if model:
        conds.append(
            select(1).select_from(AssetModel)
            .join(Model, Model.id == AssetModel.model_id)
            .where(AssetModel.asset_id == KnowledgeAsset.id, Model.name.in_((model, "通用")))
            .exists()
        )
    return conds


def _join_assets(stmt: Select, conds: list) -> Select:
    return stmt.join(KnowledgeAsset, KnowledgeAsset.id == AssetSearchDoc.asset_id).where(*conds)


# ---- 关键词召回 ----

def keyword_recall(db: Session, terms: list[str], conds: list, *, limit: int) -> list[int]:
    """返回按相关度降序的 asset_id。空词表返回空（浏览模式不走召回）。"""
    if not terms:
        return []
    if capabilities(db).keyword == "pg_tsvector":
        return _keyword_pg(db, terms, conds, limit=limit)
    return _keyword_portable(db, terms, conds, limit=limit)


def _tsquery(terms: list[str]):
    """把词表拼成 tsquery：每个词单独走 plainto_tsquery（参数化），再用 || 做 OR。

    不手拼 to_tsquery 字符串 —— 版本号里的点、标识符里的连字符都会让手拼的 tsquery
    语法报错，而 plainto_tsquery 把任何输入都当纯文本处理，顺带堵死注入。
    """
    parts = [func.plainto_tsquery("simple", term) for term in terms]
    return reduce(lambda a, b: a.op("||")(b), parts)


def _keyword_pg(db: Session, terms: list[str], conds: list, *, limit: int) -> list[int]:
    # tsv 是迁移加的生成列，ORM 没声明（TSVECTOR 是 PG 专属类型，声明了 sqlite 就 create_all 不了）
    tsv = literal_column("asset_search_doc.tsv")
    tsq = _tsquery(terms)
    # ts_rank_cd 的 32 = rank/(rank+1) 归一化，把分数压进 0–1，便于和向量路对齐观察
    rank = func.ts_rank_cd(tsv, tsq, 32).label("rank")
    stmt = _join_assets(select(AssetSearchDoc.asset_id, rank), conds).where(tsv.op("@@")(tsq))
    stmt = stmt.order_by(rank.desc(), AssetSearchDoc.asset_id).limit(limit)
    return [row.asset_id for row in db.execute(stmt)]


def _keyword_portable(db: Session, terms: list[str], conds: list, *, limit: int) -> list[int]:
    """可移植加权词频：命中权重最高的字段桶算一次分，四个桶都没命中就退回原文子串匹配。

    子串兜底是为了对齐原型 scoreAsset() 的 includes 行为 —— 中文分词粒度和用户输入
    经常对不齐（「图模式」vs「图 模式」），子串命中至少能把资产捞进候选。
    """
    columns = [col for col, _ in FIELD_WEIGHTS]
    stmt = _join_assets(select(AssetSearchDoc.asset_id, *columns, AssetSearchDoc.raw_text), conds)
    scored: list[tuple[float, int]] = []
    for row in db.execute(stmt):
        buckets = [(set(getattr(row, col.key).split()), weight) for col, weight in FIELD_WEIGHTS]
        score = 0.0
        for term in terms:
            hit = next((weight for tokens, weight in buckets if term in tokens), 0.0)
            if not hit and term in row.raw_text:
                hit = FIELD_WEIGHTS[-1][1]
            score += hit
        if score:
            scored.append((score, row.asset_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [asset_id for _, asset_id in scored[:limit]]


# ---- 向量召回 ----

def vector_recall(db: Session, query: str, conds: list, *, limit: int) -> tuple[list[int], str]:
    """返回 (asset_ids, 实际用的后端)。后端取值：pgvector / python / off / unavailable。

    unavailable = 开关是 auto 但这次拿不到查询向量（网关不可达或没配），整路跳过。
    """
    caps = capabilities(db)
    if caps.vector == "off" or not query.strip():
        return [], "off"

    vectors = ai.embed([query])
    if not vectors:
        return [], "unavailable"
    qv = vectors[0]

    # 只认当前 embedding 模型产出的向量：换了模型而没重建索引的话，老向量维度可能还对得上，
    # 于是余弦照算不误、结果却是胡的 —— 这种静默失败比直接不召回难查得多。
    conds = [*conds, AssetEmbedding.model == settings.embedding_model]
    if caps.vector == "pgvector":
        return _vector_pgvector(db, qv, conds, limit=limit), "pgvector"
    return _vector_python(db, qv, conds, limit=limit), "python"


def pgvector_stmt(qv: list[float], conds: list, *, limit: int) -> Select:
    """pgvector ANN 查询：<=> 是余弦距离（越小越近），迁移建的 HNSW 索引直接吃这个算子。

    vec 列是迁移加的、ORM 未声明，所以用 literal_column 引用；查询向量必须带上 pgvector
    的 Vector 类型来绑定 —— 写成 CAST(:qv AS vector) 那种文本片段过不了 SQLAlchemy 的
    类型强制。单独拆成建语句的函数，是为了让测试能在没有 PG 的机器上编译它（见 test_search.py）。
    """
    vec = literal_column("asset_embedding.vec")
    distance = vec.op("<=>", return_type=Float)(bindparam("qv", value=qv, type_=Vector(len(qv))))
    return (
        select(AssetEmbedding.asset_id)
        .join(KnowledgeAsset, KnowledgeAsset.id == AssetEmbedding.asset_id)
        .where(*conds, vec.isnot(None))   # 迁移后回填前 vec 是 NULL，排序会把它们排在最前
        .order_by(distance)
        .limit(limit)
    )


def _vector_pgvector(db: Session, qv: list[float], conds: list, *, limit: int) -> list[int]:
    return [row.asset_id for row in db.execute(pgvector_stmt(qv, conds, limit=limit))]


def _vector_python(db: Session, qv: list[float], conds: list, *, limit: int) -> list[int]:
    """没有 pgvector 时的等价实现：把候选向量拉回来算余弦。

    团队级库量（千条 × 1024 维 ≈ 百万次乘加）在毫秒级，MVP 阶段够用；
    真到万条以上就该上 pgvector 索引了。
    """
    stmt = (
        select(AssetEmbedding.asset_id, AssetEmbedding.vector)
        .join(KnowledgeAsset, KnowledgeAsset.id == AssetEmbedding.asset_id)
        .where(*conds)
    )
    qnorm = math.sqrt(sum(x * x for x in qv)) or 1.0
    scored: list[tuple[float, int]] = []
    for asset_id, vector in db.execute(stmt):
        if not vector or len(vector) != len(qv):
            continue
        norm = math.sqrt(sum(x * x for x in vector))
        if not norm:
            continue
        scored.append((sum(a * b for a, b in zip(qv, vector)) / (qnorm * norm), asset_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [asset_id for _, asset_id in scored[:limit]]


# ---- RRF 融合 ----

def rrf_fuse(ranked_lists: list[list[int]], *, k: int | None = None, cap: float = 30.0) -> dict[int, float]:
    """Reciprocal Rank Fusion：score = Σ 1/(k + rank)，rank 从 1 起。

    只用名次不用原始分，两路量纲不同也能融（ts_rank_cd 和余弦没有可比性）。
    归一化按**非空**路数算：只有一路可用时，该路第一名照样拿满 rel_cap，
    否则一旦降级 rel 会被系统性压低、trust/fresh 的相对权重被放大，排序会悄悄变形。
    """
    k = settings.rrf_k if k is None else k
    active = [lst for lst in ranked_lists if lst]
    if not active:
        return {}
    best = len(active) / (k + 1)
    fused: dict[int, float] = {}
    for lst in active:
        for rank, asset_id in enumerate(lst, start=1):
            fused[asset_id] = fused.get(asset_id, 0.0) + 1.0 / (k + rank)
    return {asset_id: score / best * cap for asset_id, score in fused.items()}
