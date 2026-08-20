from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db

router = APIRouter()


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    """7 指标，全部由事件表实时聚合（口径见 docs/design.md §9，禁止用点击数替代复用）。TODO(M5)：
    reuse_rate{num,den,trend[]} · search_ok · not_found_30d · review_backlog ·
    verified_count · rework_hours_trend[] · coverage[direction][status]
    """
    raise NotImplementedError("M5")
