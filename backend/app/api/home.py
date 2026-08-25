"""首页聚合（GET /home）。

单独一个接口而不是让前端拼 3~4 个请求：首页数字条 + 最近验证 + 热门 + 缺口是一屏内容，
分开拉会有 4 次往返和 4 个 loading 态。和 M5 的 /dashboard 是两码事 —— 那边是 7 个
带口径的指标，这边只是首屏的展示数据。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import KnowledgeAsset, KnowledgeGap, Status, ValidationRecord
from ..schemas import AssetBrief, GapOut, HomeResponse, HomeStats, RecentValidation, ReuseRateBrief
from ..services import metrics
from ..services.search import framework_label, load_framework_rows, load_model_names
from .assets import build_brief
from .gaps import load_gaps

router = APIRouter()

RECENT_LIMIT = 5
HOT_LIMIT = 5
GAPS_LIMIT = 6


def briefs_for(db: Session, assets: list[KnowledgeAsset]) -> dict[int, AssetBrief]:
    """批量补齐列表项需要的框架/模型，避免逐条查库。"""
    ids = [a.id for a in assets]
    if not ids:
        return {}
    fw_rows, models = load_framework_rows(db, ids), load_model_names(db, ids)
    out: dict[int, AssetBrief] = {}
    for asset in assets:
        name, version = framework_label(fw_rows.get(asset.id, []))
        out[asset.id] = build_brief(
            asset, models=models.get(asset.id, []), framework=name, fw_version=version
        )
    return out


@router.get("/home", response_model=HomeResponse)
def home(db: Session = Depends(get_db)):
    """首页数据：数字条 + 最近验证 + 热门知识（按非作者复用次数）+ 待认领缺口。"""
    counts = dict(db.execute(
        select(KnowledgeAsset.status, func.count()).group_by(KnowledgeAsset.status)
    ).all())
    # 第五格「有效复用率」与看板同一口径（services/metrics.py），不许在这里另算一份近似值
    num, den, pct = metrics.reuse_rate(db)
    stats = HomeStats(
        total=sum(n for st, n in counts.items() if st != Status.ARCHIVED),
        verified=counts.get(Status.VERIFIED, 0),
        review_due=counts.get(Status.REVIEW_DUE, 0),
        open_gaps=db.scalar(
            select(func.count()).select_from(KnowledgeGap).where(KnowledgeGap.status == "open")
        ) or 0,
        reuse_rate=ReuseRateBrief(num=num, den=den, pct=pct),
    )

    # 最近验证：取最新的验证记录，按资产去重（同一条资产只展示最近一次）
    rows = db.execute(
        select(ValidationRecord, KnowledgeAsset)
        .join(KnowledgeAsset, KnowledgeAsset.id == ValidationRecord.asset_id)
        .where(KnowledgeAsset.status == Status.VERIFIED, ValidationRecord.result == "pass")
        .order_by(ValidationRecord.at.desc(), ValidationRecord.id.desc())
        .limit(RECENT_LIMIT * 4)
    ).all()
    recent: list[tuple[ValidationRecord, KnowledgeAsset]] = []
    seen: set[int] = set()
    for validation, asset in rows:
        if asset.id in seen:
            continue
        seen.add(asset.id)
        recent.append((validation, asset))
        if len(recent) == RECENT_LIMIT:
            break

    hot = list(db.scalars(
        select(KnowledgeAsset)
        .where(KnowledgeAsset.status.notin_((Status.STALE, Status.ARCHIVED)))
        .order_by(KnowledgeAsset.reuse_count.desc(), KnowledgeAsset.updated_at.desc())
        .limit(HOT_LIMIT)
    ).all())

    briefs = briefs_for(db, [a for _, a in recent] + hot)
    return HomeResponse(
        stats=stats,
        recent_validated=[
            RecentValidation(
                asset=briefs[asset.id], validator_id=v.validator_id, note=v.note, at=v.at
            )
            for v, asset in recent
        ],
        hot=[briefs[a.id] for a in hot],
        gaps=[GapOut.model_validate(g) for g in load_gaps(db, limit=GAPS_LIMIT)],
    )
