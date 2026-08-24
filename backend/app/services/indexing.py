"""检索索引维护（M2）：把资产的可检索文本切好词写进 AssetSearchDoc，并可选回填向量。

调用点：
- POST /assets 发布后同事务刷新（索引不能比资产晚一拍，否则刚发布的知识搜不到）
- scripts/seed.py 导入完成后全量重建
- scripts/reindex.py 手工重建（改了分词规则或字段权重后必须跑）
"""
from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    AssetEmbedding,
    AssetFramework,
    AssetModel,
    AssetSearchDoc,
    AssetVersion,
    Direction,
    Framework,
    KnowledgeAsset,
    Model,
)
from . import ai, recall
from .text import index_text

# 方向的中文名也进索引：用户搜「执行链路」应当召回 direction=chain 的资产。
# 与 frontend/src/types.ts 的 DIRECTION_ZH 保持一致。
DIRECTION_ZH = {
    Direction.model: "模型结构",
    Direction.chain: "执行链路",
    Direction.feature: "推理特性",
}

# 送去做 embedding 的正文截断长度：bge-m3 能吃 8k token，但知识资产的结论都在前面，
# 截断既省网关时间也避免长尾正文稀释语义。
EMBED_BODY_CHARS = 1500


def body_md_of(db: Session, asset: KnowledgeAsset) -> str:
    """当前版本正文；没有 current_version_id 时退回最新一版。复核（M4）与索引共用。"""
    if asset.current_version_id:
        version = db.get(AssetVersion, asset.current_version_id)
        if version is not None:
            return version.body_md
    return db.scalar(
        select(AssetVersion.body_md).where(AssetVersion.asset_id == asset.id)
        .order_by(AssetVersion.seq.desc()).limit(1)
    ) or ""


def source_fields(db: Session, asset: KnowledgeAsset, *, body_md: str | None = None) -> dict[str, str]:
    """四个字段桶的原始文本。标签桶里塞进模型名/框架名/方向中文名 —— 它们短、区分度高，
    按标签权重（×3）参与打分正好，和排序里的「框架/模型匹配」加分是两件事，不冲突。"""
    model_names = db.scalars(
        select(Model.name).join(AssetModel, AssetModel.model_id == Model.id)
        .where(AssetModel.asset_id == asset.id)
    ).all()
    fw_names = db.scalars(
        select(Framework.name).join(AssetFramework, AssetFramework.framework_id == Framework.id)
        .where(AssetFramework.asset_id == asset.id)
    ).all()
    fw_versions = db.scalars(
        select(AssetFramework.verified_on).where(AssetFramework.asset_id == asset.id)
    ).all()
    tags = list(asset.tags or []) + list(model_names) + list(fw_names) + list(fw_versions)
    tags.append(DIRECTION_ZH.get(asset.direction, ""))
    if asset.env_note:
        tags.append(asset.env_note)
    return {
        "title": asset.title,
        "tags": " ".join(t for t in tags if t),
        "summary": asset.summary,
        "body": body_md if body_md is not None else body_md_of(db, asset),
    }


def _hash(fields: dict[str, str]) -> str:
    joined = "\x1f".join(fields[k] for k in ("title", "tags", "summary", "body"))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def refresh_doc(
    db: Session, asset: KnowledgeAsset, *, body_md: str | None = None, force: bool = False
) -> tuple[AssetSearchDoc, bool]:
    """重建单条资产的分词索引。返回 (doc, changed)；内容指纹没变且 force=False 时直接跳过。"""
    fields = source_fields(db, asset, body_md=body_md)
    digest = _hash(fields)
    doc = db.get(AssetSearchDoc, asset.id)
    if doc is not None and doc.content_hash == digest and not force:
        return doc, False

    values = {
        "tok_title": index_text(fields["title"]),
        "tok_tags": index_text(fields["tags"]),
        "tok_summary": index_text(fields["summary"]),
        "tok_body": index_text(fields["body"]),
        "raw_text": " ".join(fields.values()).lower(),
        "content_hash": digest,
    }
    if doc is None:
        doc = AssetSearchDoc(asset_id=asset.id, **values)
        db.add(doc)
    else:
        for key, value in values.items():
            setattr(doc, key, value)
    db.flush()
    return doc, True


def embed_source(fields: dict[str, str]) -> str:
    """向量化用的文本：标题 + 标签 + 摘要 + 截断正文（不分词，交给 bge-m3 自己处理）。"""
    return "\n".join([
        fields["title"], fields["tags"], fields["summary"], fields["body"][:EMBED_BODY_CHARS],
    ]).strip()


def refresh_embedding(db: Session, asset: KnowledgeAsset, *, body_md: str | None = None,
                      force: bool = False) -> bool:
    """回填单条资产的向量。网关不可用时返回 False（不报错 —— 向量路是可降级的增强）。"""
    fields = source_fields(db, asset, body_md=body_md)
    text = embed_source(fields)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    row = db.get(AssetEmbedding, asset.id)
    if row is not None and row.content_hash == digest and not force:
        return False

    vectors = ai.embed([text])
    if not vectors:
        return False
    vector = vectors[0]
    if row is None:
        row = AssetEmbedding(asset_id=asset.id)
        db.add(row)
    row.model, row.dim, row.vector, row.content_hash = settings.embedding_model, len(vector), vector, digest
    db.flush()

    # 有 pgvector 时把同一份向量再写进 vec 列（ORM 不认识它，只能走 SQL）：
    # JSONB 那份是权威数据，vec 只是给 HNSW 索引用的副本，漏写会让 ANN 召回空手而归。
    if recall.capabilities(db).vector == "pgvector":
        db.execute(
            sql_text("UPDATE asset_embedding SET vec = CAST(:vec AS vector) WHERE asset_id = :id"),
            {"vec": "[" + ",".join(repr(float(x)) for x in vector) + "]", "id": asset.id},
        )
    return True


def reindex_all(db: Session, *, with_embeddings: bool = False, force: bool = False) -> dict[str, int]:
    """全量重建。返回 {'assets': n, 'docs': n, 'embeddings': n}。"""
    assets = db.scalars(select(KnowledgeAsset).order_by(KnowledgeAsset.id)).all()
    stats = {"assets": len(assets), "docs": 0, "embeddings": 0}
    for asset in assets:
        _, changed = refresh_doc(db, asset, force=force)
        stats["docs"] += int(changed)
        if with_embeddings and refresh_embedding(db, asset, force=force):
            stats["embeddings"] += 1
    return stats
