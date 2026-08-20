"""资产接口：发布 DRAFT / 详情 / 状态流转审计（docs/api-contract.md 「资产」节）。

硬规则提醒：状态只能经 services.state_machine 改；本模块不出现 asset.status = ...
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    AssetFramework,
    AssetModel,
    AssetVersion,
    CodeReference,
    Framework,
    KnowledgeAsset,
    Model,
    RefKind,
    ReuseEvent,
    StatusTransition,
    Tier,
    ValidationRecord,
    VersionOrigin,
)
from ..schemas import (
    AssetCreate,
    AssetDetail,
    CodeRefOut,
    FrameworkOut,
    ReuseOut,
    TransitionOut,
    ValidationOut,
    VersionBrief,
    VersionOut,
)
from ..services import state_machine

router = APIRouter()

SUMMARY_MAX = 140
# 版本区间只按连接号切（v0.9.1–v0.9.2 / v0.9.1~v0.9.2）；不切 ASCII 连字符，
# 否则 v0.4.2-patch 这类带后缀的版本会被错切成区间。
_RANGE_SEP = re.compile(r"\s*[–—~]\s*")


def _derive_summary(body_md: str) -> str:
    """摘要取「结论」小节（沉淀页三项确认的第三项）；没有该小节就退回全文开头。

    M2 会用 AI 重写摘要，这里只做一个不出错的兜底。
    """
    sections = re.split(r"^##[ \t]*", body_md, flags=re.MULTILINE)
    if len(sections) > 1:
        # 有小节标题：优先「结论」，否则取最后一节；两种情况都要丢掉标题行本身
        section = next((s for s in sections if s.startswith("结论")), sections[-1])
        text = section.split("\n", 1)[1] if "\n" in section else section
    else:
        text = body_md
    text = text.replace("`", "")                  # 去掉行内代码反引号
    return " ".join(text.split())[:SUMMARY_MAX]


def _get_or_create_framework(db: Session, name: str) -> Framework:
    fw = db.scalar(select(Framework).where(Framework.name == name))
    if fw is None:
        fw = Framework(name=name)
        db.add(fw)
        db.flush()
    return fw


def _get_or_create_model(db: Session, name: str) -> Model:
    m = db.scalar(select(Model).where(Model.name == name))
    if m is None:
        m = Model(name=name)
        db.add(m)
        db.flush()
    return m


def split_version_range(fw_version: str) -> tuple[str, str]:
    """把 v0.9.1–v0.9.2 拆成上下界；单值或说明性文字返回两个空串。"""
    parts = _RANGE_SEP.split(fw_version.strip())
    if len(parts) == 2 and all(parts):
        return parts[0][:40], parts[1][:40]
    return "", ""


def build_detail(db: Session, asset: KnowledgeAsset) -> AssetDetail:
    """聚合详情响应：资产 + 当前版本 + 验证/复用记录 + 代码引用 + 版本历史。"""
    versions = db.scalars(
        select(AssetVersion).where(AssetVersion.asset_id == asset.id).order_by(AssetVersion.seq.desc())
    ).all()
    current = next((v for v in versions if v.id == asset.current_version_id), None)
    if current is None and versions:
        current = versions[0]   # 兜底：历史数据没写 current_version_id 时取最新版本

    frameworks = db.execute(
        select(Framework, AssetFramework)
        .join(AssetFramework, AssetFramework.framework_id == Framework.id)
        .where(AssetFramework.asset_id == asset.id)
    ).all()
    model_names = db.scalars(
        select(Model.name).join(AssetModel, AssetModel.model_id == Model.id)
        .where(AssetModel.asset_id == asset.id).order_by(AssetModel.id)
    ).all()
    code_refs = db.scalars(
        select(CodeReference).where(CodeReference.asset_id == asset.id).order_by(CodeReference.id)
    ).all()
    validations = db.scalars(
        select(ValidationRecord).where(ValidationRecord.asset_id == asset.id)
        .order_by(ValidationRecord.at.desc(), ValidationRecord.id.desc())
    ).all()
    reuses = db.scalars(
        select(ReuseEvent).where(ReuseEvent.asset_id == asset.id)
        .order_by(ReuseEvent.at.desc(), ReuseEvent.id.desc())
    ).all()

    return AssetDetail(
        id=asset.id,
        title=asset.title,
        direction=asset.direction,
        tier=asset.tier,
        status=asset.status,
        summary=asset.summary,
        tags=asset.tags or [],
        author_id=asset.author_id,
        reuse_count=asset.reuse_count,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        source=asset.source,
        source_ref=asset.source_ref,
        env_note=asset.env_note,
        status_reason=asset.status_reason,
        models=list(model_names),
        frameworks=[
            FrameworkOut(
                name=fw.name, repo_url=fw.repo_url, version_min=af.version_min,
                version_max=af.version_max, verified_on=af.verified_on,
            )
            for fw, af in frameworks
        ],
        current_version=VersionOut.model_validate(current) if current else None,
        versions=[VersionBrief.model_validate(v) for v in versions],
        code_refs=[CodeRefOut.model_validate(c) for c in code_refs],
        validations=[ValidationOut.model_validate(v) for v in validations],
        reuses=[ReuseOut.model_validate(r) for r in reuses],
    )


def load_asset(db: Session, asset_id: int) -> KnowledgeAsset:
    asset = db.get(KnowledgeAsset, asset_id)
    if asset is None:
        raise HTTPException(404, detail=("NOT_FOUND", f"资产 {asset_id} 不存在"))
    return asset


@router.post("/assets", status_code=201, response_model=AssetDetail)
def create_asset(body: AssetCreate, db: Session = Depends(get_db), x_user: str = Header(default="anonymous")):
    """发布 DRAFT（沉淀页）：资产 + 首个版本 + 模型/框架/代码引用关联，状态经状态机置 DRAFT。

    TODO(M2)：异步生成 summary/tags/embedding，替换这里的规则式摘要。
    """
    asset = KnowledgeAsset(
        title=body.title,
        direction=body.direction,
        tier=Tier.note,                       # 发布一律是工作记录；升 shared/core 由复用频次驱动
        summary=_derive_summary(body.body_md),
        tags=body.tags,
        author_id=x_user,
        source=body.source,
        source_ref=body.source_ref,
        env_note=body.env_note,
    )
    db.add(asset)
    db.flush()

    version = AssetVersion(
        asset_id=asset.id, seq=1, body_md=body.body_md, change_note="首次发布",
        created_by=x_user, created_from=VersionOrigin.author,
    )
    db.add(version)
    db.flush()
    asset.current_version_id = version.id

    state_machine.create_as_draft(
        db, asset, actor=x_user,
        evidence_type="asset_version", evidence_id=version.id,
        note="作者确认问题/环境/结论三项后发布，尚无非作者复用。",
    )

    if body.framework:
        fw = _get_or_create_framework(db, body.framework)
        vmin, vmax = split_version_range(body.fw_version)
        db.add(AssetFramework(
            asset_id=asset.id, framework_id=fw.id,
            version_min=vmin, version_max=vmax, verified_on=body.fw_version[:40],
        ))
    for name in dict.fromkeys(body.models):        # 去重且保序
        db.add(AssetModel(asset_id=asset.id, model_id=_get_or_create_model(db, name).id))
    for ref in body.code_refs:
        db.add(CodeReference(
            asset_id=asset.id, kind=RefKind(ref.kind), repo=ref.repo,
            path_or_key=ref.path_or_key, ref_id=ref.ref_id, note=ref.note, watch=ref.watch,
        ))

    db.commit()
    db.refresh(asset)
    return build_detail(db, asset)


@router.get("/assets/{asset_id}", response_model=AssetDetail)
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    """详情：资产 + 当前版本正文 + 验证/复用记录 + 代码引用 + 版本历史。"""
    return build_detail(db, load_asset(db, asset_id))


@router.get("/assets/{asset_id}/transitions", response_model=list[TransitionOut])
def get_transitions(asset_id: int, db: Session = Depends(get_db)):
    """状态流转审计流水，按发生时间正序（append-only，每条带证据）。"""
    load_asset(db, asset_id)
    rows = db.scalars(
        select(StatusTransition).where(StatusTransition.asset_id == asset_id)
        .order_by(StatusTransition.at, StatusTransition.id)
    ).all()
    return [TransitionOut.model_validate(r) for r in rows]
