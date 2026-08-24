from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import KnowledgeGap
from ..schemas import GapOut
from .assets import USER_ID_MAX

router = APIRouter()


def load_gaps(db: Session, *, limit: int = 20) -> list[KnowledgeGap]:
    """未解决的缺口：open 排在 claimed 前，再按被问次数、最近时间排序。
    首页与缺口列表共用，排序口径只有这一处。"""
    order_by_state = case((KnowledgeGap.status == "open", 0), else_=1)
    return list(db.scalars(
        select(KnowledgeGap)
        .where(KnowledgeGap.status != "resolved")
        .order_by(order_by_state, KnowledgeGap.hit_count.desc(), KnowledgeGap.last_at.desc())
        .limit(limit)
    ).all())


@router.get("/gaps", response_model=list[GapOut])
def list_gaps(limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)):
    """知识缺口列表。写入路径是 POST /feedback/not-found（M3）。"""
    return [GapOut.model_validate(g) for g in load_gaps(db, limit=limit)]


@router.post("/gaps/{gap_id}/claim", response_model=GapOut)
def claim(gap_id: int, db: Session = Depends(get_db),
          x_user: str = Header(default="anonymous", max_length=USER_ID_MAX)):
    """认领缺口：status=claimed + 记认领人，让别人不再重复补同一份知识。

    认领是「我来写」的登记，不是「已经写了」—— 所以它只改缺口状态，不建任何资产。
    缺口关到 resolved 由 M5 的沉淀回链完成（resolved_asset_id）。

    TODO(M5)：认领后调 ai.draft_from_session 生成 DRAFT 底稿（现在那个函数还是骨架，
    原型的文案也只承诺「AI 将…生成草稿底稿」，没有当场产出资产）。
    """
    gap = db.get(KnowledgeGap, gap_id)
    if gap is None:
        raise HTTPException(404, detail=("NOT_FOUND", f"缺口 {gap_id} 不存在"))
    if gap.status == "resolved":
        raise HTTPException(409, detail=("GAP_RESOLVED", "该缺口已解决，无需认领"))
    if gap.status == "claimed" and gap.claimed_by != x_user:
        raise HTTPException(409, detail=("GAP_ALREADY_CLAIMED", f"该缺口已由 {gap.claimed_by} 认领"))

    gap.status = "claimed"
    gap.claimed_by = x_user
    db.commit()
    db.refresh(gap)
    return GapOut.model_validate(gap)
