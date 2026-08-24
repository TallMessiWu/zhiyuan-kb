"""知识缺口：「没有找到答案」反馈的建新与累计（M3）。

缺口既是待补充清单，也是看板有效复用率的分母之一（design.md §9），所以两件事都不能错：
漏记会让分母偏小、复用率虚高；同一个需求被记成好几条，首页列表会被同义缺口刷满，
hit_count 也就不再回答「有多少人在等这份知识」。

合并判据用 jieba 词集合，不用 embedding：
1. 缺口累计在写路径上同步发生，不能挂在会超时/会熔断的网关上（services/ai.py 的降级约定）；
2. 词集合在 PG 与 sqlite 上行为一致，测试不必依赖 PG —— 与 M2 的分词路线同源。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..models import KnowledgeGap, utcnow
from . import text

QUESTION_MAX = 500          # 对齐 KnowledgeGap.question 的列宽
BROWSE_QUERY = "（无关键词浏览）"   # 空查询下记缺口时的占位问句，与 prototype 的 reportGap() 一致


def _tokens(question: str) -> set[str]:
    return set(text.tokenize(question))


def similarity(a: str, b: str) -> float:
    """两个问句的词集合 Jaccard 相似度，0–1。任一侧切不出词就是 0。"""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def is_same_need(a: str, b: str) -> bool:
    """够不够算「同一个需求」。两条判据满足其一即合并：

    1. Jaccard ≥ `ZY_GAP_MERGE_SIMILARITY` —— 说法不同但用词大面积重合。
    2. 短的一侧整体落在长的一侧里，且至少 2 个词 —— 搜索词往往只是缺口问句的一个子集
       （「PD 分离 部署」vs「PD 分离在 vllm-ascend 的部署方式与 KV 传输配置」），
       这种情况 Jaccard 会被长度差压到 0.2 以下，只靠判据 1 必漏合。
       限 2 个词是为了挡住「vllm」这种单词查询 —— 它能落进几乎所有缺口里。
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(short) >= 2 and short <= long_:
        return True
    return len(ta & tb) / len(ta | tb) >= settings.gap_merge_similarity


def find_same_need(db: Session, question: str) -> KnowledgeGap | None:
    """在未解决的缺口里找同一个需求。

    只在 open/claimed 里找：resolved 意味着知识已经沉淀，此时还有人报「没找到答案」，
    问题就不是缺知识而是搜不到 —— 那是一个新的需求信号，不该被旧缺口吞掉。

    逐条比对而不是走索引：MVP 的缺口量是几十条量级，一次全表足够快。
    TODO：缺口过千再考虑先用关键词粗筛再算相似度。
    """
    candidates = db.scalars(
        select(KnowledgeGap)
        .where(KnowledgeGap.status != "resolved")
        .order_by(KnowledgeGap.hit_count.desc(), KnowledgeGap.id.desc())
    ).all()
    for gap in candidates:
        if is_same_need(question, gap.question):
            return gap
    return None


def record(db: Session, *, question: str, user_id: str, at: datetime | None = None) -> tuple[KnowledgeGap, bool]:
    """记一次「没有找到答案」：命中同一需求就累计，否则新建。返回 (缺口, 是否新建)。

    调用方负责 commit。
    """
    question = (question.strip() or BROWSE_QUERY)[:QUESTION_MAX]
    now = at or utcnow()

    gap = find_same_need(db, question)
    if gap is None:
        gap = KnowledgeGap(question=question, hit_count=1, first_at=now, last_at=now,
                           reporters=[user_id], status="open")
        db.add(gap)
        db.flush()
        return gap, True

    # hit_count 走原子自增：它是需求强度的计数，两个并发的「没找到」不能互相覆盖。
    # reporters 是 JSON 列，只能读改写 —— 且必须整体赋新值：JSON 列默认不带变更追踪，
    # gap.reporters.append(...) 改的是同一个 list 对象，SQLAlchemy 看不见、不会 UPDATE。
    reporters = gap.reporters if user_id in gap.reporters else [*gap.reporters, user_id]
    db.execute(
        update(KnowledgeGap)
        .where(KnowledgeGap.id == gap.id)
        .values(hit_count=KnowledgeGap.hit_count + 1, last_at=now, reporters=reporters)
    )
    db.refresh(gap)
    return gap, False
