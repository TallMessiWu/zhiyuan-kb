"""M2 检索：分词索引表 + 向量表 + summary_source

Revision ID: a1c7f2e93b40
Revises: 562da9d71450
Create Date: 2026-08-21 15:40:00.000000

两处 PG 专属结构由本迁移单独追加，ORM 里刻意不声明（TSVECTOR / vector 都是 PG 专属类型，
声明了 models.py 就没法在 sqlite 上 create_all，测试全得改成依赖 PG）：

1. asset_search_doc.tsv —— 生成列（GENERATED ALWAYS AS ... STORED）。
   用 to_tsvector('simple', ...) 这种带**字面量配置名**的写法才是 immutable，
   生成列才允许；写成 to_tsvector(col)（走默认配置）是 stable，PG 会直接拒绝建列。
   字段权重 A/B/C/D 对应 title×4 / tags×3 / summary×2 / body×1（docs/design.md §5）。
2. asset_embedding.vec —— 仅当这台 PG 装得上 vector 扩展时才建，并配 HNSW 余弦索引。
   装不上（比如 scripts/devdb.ps1 起的 Windows pgserver）就跳过：向量本体照样存在
   JSONB 的 vector 列里，召回退化为 Python 余弦，功能不缺，只是没有 ANN 加速。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1c7f2e93b40'
down_revision: Union[str, Sequence[str], None] = '562da9d71450'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TSV_DDL = """
ALTER TABLE asset_search_doc ADD COLUMN tsv tsvector
GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', coalesce(tok_title, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(tok_tags, '')), 'B') ||
    setweight(to_tsvector('simple', coalesce(tok_summary, '')), 'C') ||
    setweight(to_tsvector('simple', coalesce(tok_body, '')), 'D')
) STORED
"""


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _has_vector_extension() -> bool:
    return bool(op.get_bind().scalar(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
    ))


def upgrade() -> None:
    op.add_column(
        "knowledge_asset",
        sa.Column("summary_source", sa.String(length=16), nullable=False, server_default="rule"),
    )

    op.create_table(
        "asset_search_doc",
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("tok_title", sa.Text(), nullable=False),
        sa.Column("tok_tags", sa.Text(), nullable=False),
        sa.Column("tok_summary", sa.Text(), nullable=False),
        sa.Column("tok_body", sa.Text(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["knowledge_asset.id"]),
        sa.PrimaryKeyConstraint("asset_id"),
    )

    op.create_table(
        "asset_embedding",
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["knowledge_asset.id"]),
        sa.PrimaryKeyConstraint("asset_id"),
    )

    if not _is_postgres():
        return

    op.execute(TSV_DDL)
    op.execute("CREATE INDEX ix_asset_search_doc_tsv ON asset_search_doc USING gin (tsv)")

    if _has_vector_extension():
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        # 维度写死 1024 = bge-m3；换 embedding 模型要新写一个迁移改列宽并重建索引
        op.execute("ALTER TABLE asset_embedding ADD COLUMN vec vector(1024)")
        op.execute(
            "CREATE INDEX ix_asset_embedding_vec ON asset_embedding "
            "USING hnsw (vec vector_cosine_ops)"
        )
    else:
        print("[M2] 这台 PostgreSQL 没有 vector 扩展，跳过 pgvector 列与 HNSW 索引；"
              "向量召回将走 Python 余弦（功能不缺，无 ANN 加速）")


def downgrade() -> None:
    op.drop_table("asset_embedding")     # tsv/vec/索引随表一起消失
    op.drop_table("asset_search_doc")
    op.drop_column("knowledge_asset", "summary_source")
