from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import AssetCreate

router = APIRouter()


@router.post("/assets", status_code=201)
def create_asset(body: AssetCreate, db: Session = Depends(get_db), x_user: str = Header(default="anonymous")):
    """发布 DRAFT（沉淀页）。TODO(M1)：
    1. 建 KnowledgeAsset(tier=note) + AssetVersion(seq=1, created_from=author)
    2. services.state_machine.create_as_draft()
    3. 建 CodeReference / AssetModel / AssetFramework 关联
    4. TODO(M2) 异步生成 summary/tags/embedding
    """
    raise NotImplementedError("M1")


@router.get("/assets/{asset_id}")
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    """详情：资产 + 当前版本正文 + 验证/复用记录 + 代码引用 + 版本历史。TODO(M1)"""
    raise NotImplementedError("M1")


@router.get("/assets/{asset_id}/transitions")
def get_transitions(asset_id: int, db: Session = Depends(get_db)):
    """状态流转审计流水。TODO(M1)"""
    raise NotImplementedError("M1")
