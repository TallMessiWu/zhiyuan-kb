"""看板指标聚合（M5）— 口径见 docs/design.md §9，全部由事件表实时聚合。

硬规则 5：有效复用率 = 非作者成功复用事件 ÷ 需求事件，禁止用点击量/PV/问答次数冒充。
所以本模块只读四张事件/状态表（ReuseEvent / SearchEvent / UserFeedback / KnowledgeGap +
资产状态计数），不碰 AI，也没有任何「点击」参与计算。

两个 M5 冻结的口径（design.md §9 落地注记同步维护）：
- 需求会话去重：SearchEvent（search+qa 一起）按（user_id, jieba 词集合归一化主题）在
  30 分钟窗口内合并为一次需求。「导航式查询」MVP 无法识别、不扣减。
- 分母 = 去重会话数 + **不带 search_event_id 的** not_found 反馈数 —— 带 event_id 的
  缺口反馈，其需求已经被那次搜索会话计入分母，再加一次就是双算。

事件量在 MVP 规模（单团队）下很小，聚合直接在 Python 里做：口径逻辑（30min 滑动合并、
词集合归一）用 SQL 表达会又长又难测，而且 sqlite/PG 行为必须一致（测试不依赖 PG）。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Direction,
    KnowledgeAsset,
    KnowledgeGap,
    ReuseEvent,
    SearchEvent,
    Status,
    UserFeedback,
    utcnow,
)
from .text import query_terms

WINDOW_DAYS = 30       # 主窗口：近 30 天
TREND_MONTHS = 5       # 趋势条：近 5 个自然月（含当月）

# 空查询（浏览模式）的主题占位。浏览不构成「主题」，重复工时的重复判定要排除它。
BROWSE_TOPIC = "(browse)"


def topic_key(query: str) -> str:
    """同一需求的归一化主题：jieba 词集合排序拼接（与缺口合并同一思路，见 services/gaps.py）。"""
    terms = sorted(set(query_terms(query)))
    return " ".join(terms) if terms else BROWSE_TOPIC


def _aware(dt: datetime) -> datetime:
    """sqlite 读回来的是裸 datetime，PG 是 timestamptz；统一成 UTC 再比较。"""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class DemandSession:
    """一次去重后的知识需求：同人同主题 30 分钟内的搜索/问答合并。"""

    user_id: str
    topic: str
    start: datetime
    last: datetime
    event_ids: list[int] = field(default_factory=list)
    modes: set = field(default_factory=set)        # {"search", "qa"}
    has_result: bool = False


def demand_sessions(events: list[SearchEvent]) -> list[DemandSession]:
    """把 SearchEvent 流合并成需求会话。events 无需预排序。"""
    window = timedelta(minutes=settings.dashboard_session_minutes)
    by_key: dict[tuple[str, str], list[SearchEvent]] = defaultdict(list)
    for e in events:
        by_key[(e.user_id, topic_key(e.query))].append(e)

    sessions: list[DemandSession] = []
    for (user_id, topic), rows in by_key.items():
        rows.sort(key=lambda e: (_aware(e.at), e.id))
        current: DemandSession | None = None
        for e in rows:
            at = _aware(e.at)
            if current is None or at - current.last > window:
                current = DemandSession(user_id=user_id, topic=topic, start=at, last=at)
                sessions.append(current)
            current.last = at
            current.event_ids.append(e.id)
            current.modes.add(e.mode)
            current.has_result = current.has_result or bool(e.result_ids)
    return sessions


# ---------- 复用率（/home 与 /dashboard 共用，口径只有这一处） ----------

def _reuse_num(db: Session, since: datetime, until: datetime) -> int:
    """分子：非作者成功复用事件（join 资产核对作者，服务端口径，不信任何前端计数）。"""
    return db.scalar(
        select(func.count()).select_from(ReuseEvent)
        .join(KnowledgeAsset, KnowledgeAsset.id == ReuseEvent.asset_id)
        .where(
            ReuseEvent.outcome == "success",
            ReuseEvent.user_id != KnowledgeAsset.author_id,
            ReuseEvent.at >= since, ReuseEvent.at < until,
        )
    ) or 0


def _events_between(db: Session, since: datetime, until: datetime) -> list[SearchEvent]:
    return list(db.scalars(
        select(SearchEvent).where(SearchEvent.at >= since, SearchEvent.at < until)
    ).all())


def _orphan_not_found(db: Session, since: datetime, until: datetime) -> int:
    """不带 search_event_id 的「没有找到答案」：需求没经过任何搜索会话（详情页入口），
    单独进分母；带 event_id 的已随会话计入，不重复加。"""
    return db.scalar(
        select(func.count()).select_from(UserFeedback)
        .where(
            UserFeedback.kind == "not_found",
            UserFeedback.search_event_id.is_(None),
            UserFeedback.at >= since, UserFeedback.at < until,
        )
    ) or 0


def reuse_rate(db: Session, *, now: datetime | None = None) -> tuple[int, int, float | None]:
    """近 30 天有效复用率：(分子, 分母, 百分比)。den=0 时 pct=None（前端显示「—」）。"""
    now = now or utcnow()
    since = now - timedelta(days=WINDOW_DAYS)
    num = _reuse_num(db, since, now)
    den = len(demand_sessions(_events_between(db, since, now))) + _orphan_not_found(db, since, now)
    return num, den, round(num / den * 100, 1) if den else None


# ---------- 月度趋势 ----------

def _month_starts(now: datetime, n: int) -> list[datetime]:
    """近 n 个自然月的月初（UTC），旧→新，含当月。"""
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    months = [first]
    for _ in range(n - 1):
        first = (first - timedelta(days=1)).replace(day=1)
        months.append(first)
    return list(reversed(months))


def _next_month(month: datetime) -> datetime:
    return (month + timedelta(days=32)).replace(day=1)


def _label(month: datetime) -> str:
    return f"{month.month}月"


def _not_found_feedback_ids(db: Session) -> set[int]:
    """挂了「没有找到答案」反馈的 search_event_id 集合（搜索成功率的失败面）。"""
    rows = db.scalars(
        select(UserFeedback.search_event_id)
        .where(UserFeedback.kind == "not_found", UserFeedback.search_event_id.is_not(None))
    ).all()
    return set(rows)


def _session_ok(s: DemandSession, nf_ids: set[int]) -> bool:
    return s.has_result and not (set(s.event_ids) & nf_ids)


# ---------- 看板主聚合 ----------

def dashboard_data(db: Session, *, now: datetime | None = None) -> dict:
    """GET /dashboard 的全部字段（schemas.DashboardResponse 的构造参数）。"""
    now = now or utcnow()
    since_30d = now - timedelta(days=WINDOW_DAYS)
    months = _month_starts(now, TREND_MONTHS)
    horizon = months[0]

    # 事件一次取齐（近 5 个月覆盖了 30 天窗口），按月与按窗口分别聚合
    events = _events_between(db, horizon, now)
    nf_ids = _not_found_feedback_ids(db)

    sessions_30d = demand_sessions([e for e in events if _aware(e.at) >= since_30d])
    num_30d = _reuse_num(db, since_30d, now)
    den_30d = len(sessions_30d) + _orphan_not_found(db, since_30d, now)

    search_sessions_30d = [s for s in sessions_30d if "search" in s.modes]
    ok_30d = sum(1 for s in search_sessions_30d if _session_ok(s, nf_ids))

    reuse_trend, search_trend, rework_trend = [], [], []
    for month in months:
        until = min(_next_month(month), now)
        label = _label(month)
        month_events = [e for e in events if month <= _aware(e.at) < until]
        sessions = demand_sessions(month_events)

        num = _reuse_num(db, month, until)
        den = len(sessions) + _orphan_not_found(db, month, until)
        reuse_trend.append({"label": label, "value": round(num / den * 100, 1) if den else 0.0})

        search_sessions = [s for s in sessions if "search" in s.modes]
        ok = sum(1 for s in search_sessions if _session_ok(s, nf_ids))
        search_trend.append({
            "label": label,
            "value": round(ok / len(search_sessions) * 100, 1) if search_sessions else 0.0,
        })

        # 重复探索工时（估算）：同月内同主题的第 2+ 次需求会话（跨用户 —— 别人已探索过的
        # 主题再次被探索，就是本该省下的排查时间）× 平均排查工时。浏览会话不算主题。
        per_topic: dict[str, int] = defaultdict(int)
        for s in sessions:
            if s.topic != BROWSE_TOPIC:
                per_topic[s.topic] += 1
        repeats = sum(n - 1 for n in per_topic.values())
        rework_trend.append({
            "label": label,
            "value": round(repeats * settings.rework_hours_per_miss, 1),
        })

    status_counts = dict(db.execute(
        select(KnowledgeAsset.status, func.count()).group_by(KnowledgeAsset.status)
    ).all())

    coverage: dict[str, dict[str, int]] = {
        d.value: {s.value: 0 for s in Status} for d in Direction
    }
    for direction, status, count in db.execute(
        select(KnowledgeAsset.direction, KnowledgeAsset.status, func.count())
        .group_by(KnowledgeAsset.direction, KnowledgeAsset.status)
    ):
        coverage[direction.value][status.value] = count

    reuse_by_direction = {d.value: 0 for d in Direction}
    for direction, count in db.execute(
        select(KnowledgeAsset.direction, func.count())
        .select_from(ReuseEvent)
        .join(KnowledgeAsset, KnowledgeAsset.id == ReuseEvent.asset_id)
        .where(ReuseEvent.outcome == "success", ReuseEvent.user_id != KnowledgeAsset.author_id)
        .group_by(KnowledgeAsset.direction)
    ):
        reuse_by_direction[direction.value] = count

    gap_counts = dict(db.execute(
        select(KnowledgeGap.status, func.count()).group_by(KnowledgeGap.status)
    ).all())
    open_gaps = gap_counts.get("open", 0)
    claimed_gaps = gap_counts.get("claimed", 0)

    not_found_30d = db.scalar(
        select(func.count()).select_from(UserFeedback)
        .where(UserFeedback.kind == "not_found", UserFeedback.at >= since_30d)
    ) or 0

    return {
        "window_days": WINDOW_DAYS,
        "generated_at": now,
        "reuse_rate": {
            "num": num_30d, "den": den_30d,
            "pct": round(num_30d / den_30d * 100, 1) if den_30d else None,
            "trend": reuse_trend,
        },
        "search_ok": {
            "pct": round(ok_30d / len(search_sessions_30d) * 100, 1) if search_sessions_30d else None,
            "ok_sessions": ok_30d,
            "total_sessions": len(search_sessions_30d),
            "trend": search_trend,
        },
        "not_found_30d": not_found_30d,
        "review_backlog": status_counts.get(Status.REVIEW_DUE, 0),
        "verified_count": status_counts.get(Status.VERIFIED, 0),
        "draft_count": status_counts.get(Status.DRAFT, 0),
        "open_gaps": open_gaps,
        "claimed_gaps": claimed_gaps,
        "gaps_total": open_gaps + claimed_gaps,
        "rework_hours_trend": rework_trend,
        "rework_hours_estimated": True,
        "rework_hours_per_miss": settings.rework_hours_per_miss,
        "coverage": coverage,
        "reuse_by_direction": reuse_by_direction,
    }
