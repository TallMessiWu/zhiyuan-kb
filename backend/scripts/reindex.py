"""重建检索索引（分词文档 + 可选向量）。

什么时候必须跑：
- 改了 services/text.py 的分词规则或 services/indexing.py 的字段组装（老索引还是旧词）
- 迁移刚加上 tsv/vec 列（生成列会自动补算，但向量得回填）
- embedding 网关刚接通（之前发布的资产都没有向量）
- 换了 ZY_EMBEDDING_MODEL（老向量不会被用到 —— 召回只认当前模型的向量，
  但在重建之前向量这一路等于停摆，必须 `--embeddings --force` 补上）

用法：
    python scripts/reindex.py                 # 只重建分词文档（内容没变的跳过）
    python scripts/reindex.py --embeddings    # 同时回填向量（要网关可达）
    python scripts/reindex.py --force         # 忽略内容指纹，全部重算
"""
from __future__ import annotations

import argparse
import sys

from app.db import get_sessionmaker
from app.services import indexing, recall


def main() -> int:
    parser = argparse.ArgumentParser(description="重建检索索引")
    parser.add_argument("--embeddings", action="store_true", help="同时回填向量（调 embedding 网关）")
    parser.add_argument("--force", action="store_true", help="忽略内容指纹，全部重算")
    args = parser.parse_args()

    db = get_sessionmaker()()
    try:
        caps = recall.capabilities(db)
        print(f"库能力：dialect={caps.dialect} 关键词={caps.keyword} 向量={caps.vector}")

        stats = indexing.reindex_all(db, with_embeddings=args.embeddings, force=args.force)
        db.commit()

        print(f"资产 {stats['assets']} 条：更新分词文档 {stats['docs']} 条，向量 {stats['embeddings']} 条")
        if args.embeddings and not stats["embeddings"] and stats["assets"]:
            print("没有回填到任何向量 —— embedding 网关不可达或全部内容未变（加 --force 强制重算）",
                  file=sys.stderr)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
