from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import DashboardResponse
from ..services import metrics

router = APIRouter()


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(db: Session = Depends(get_db)):
    """7 指标看板，全部由事件表实时聚合（口径见 docs/design.md §9，实现在 services/metrics.py）。

    硬规则 5：复用率分子只认「非作者成功复用事件」，分母是去重后的需求会话 + 纯缺口反馈；
    没有任何点击量/PV 参与。重复探索工时是估算值，响应里 rework_hours_estimated 明示。
    """
    return DashboardResponse(**metrics.dashboard_data(db))
