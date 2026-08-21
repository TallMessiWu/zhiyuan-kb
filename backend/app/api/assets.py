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
    ReuseEvent,
    StatusTransition,
    Tier,
    ValidationRecord,
    VersionOrigin,
)
from ..schemas import (
    AssetBrief,
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
from ..services import ai, indexing, state_machine
from ..services.search import framework_label

router = APIRouter()

SUMMARY_MAX = 140
# X-User 会落到 author_id / actor / validator_id / user_id 这些 String(64) 列上。
# sqlite 不校验 VARCHAR 长度，超长值只有在 PG 上才炸，所以在请求头入口就挡住。
USER_ID_MAX = 64
# 版本区间只按连接号切（v0.9.1–v0.9.2 / v0.9.1~v0.9.2）；不切 ASCII 连字符，
# 否则 v0.4.2-patch 这类带后缀的版本会被错切成区间。
_RANGE_SEP = re.compile(r"\s*[–—~]\s*")


def make_summary(title: str, body_md: str) -> tuple[str, str]:
    """生成检索用摘要，返回 (summary, source)。

    首选 AI（把工程记录压成一句带结论和关键参数的话，比截取正文可检索得多）；
    网关不可用就回落到规则式截取 —— 摘要不该成为发布路径的单点故障。
    source 会落到 KnowledgeAsset.summary_source，硬规则 1 要求 AI 产出可识别。
    """
    text = ai.summarize(title, body_md)
    if text:
        return text[:SUMMARY_MAX], "ai"
    return _derive_summary(body_md), "rule"


def _derive_summary(body_md: str) -> str:
    """规则式兜底：取「结论」小节（沉淀页三项确认的第三项），没有该小节就退回全文开头。"""
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


def build_brief(asset: KnowledgeAsset, *, models: list[str], framework: str, fw_version: str) -> AssetBrief:
    """列表项（搜索结果 / 首页）。维度由调用方批量查好传进来，避免逐条查库。"""
    return AssetBrief(
        id=asset.id,
        title=asset.title,
        direction=asset.direction,
        tier=asset.tier,
        status=asset.status,
        summary=asset.summary,
        summary_source=asset.summary_source,
        tags=asset.tags or [],
        author_id=asset.author_id,
        reuse_count=asset.reuse_count,
        updated_at=asset.updated_at,
        models=models,
        framework=framework,
        fw_version=fw_version,
    )


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

    fw_name, fw_version = framework_label([(fw.name, af) for fw, af in frameworks])
    # AssetDetail 继承 AssetBrief，公共字段直接摊平复用，别抄第二遍。
    # code 要排除：它是 computed_field，由 id 算出来，不能当构造参数传。
    return AssetDetail(
        **build_brief(
            asset, models=list(model_names), framework=fw_name, fw_version=fw_version
        ).model_dump(exclude={"code"}),
        created_at=asset.created_at,
        source=asset.source,
        source_ref=asset.source_ref,
        env_note=asset.env_note,
        status_reason=asset.status_reason,
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
def create_asset(body: AssetCreate, db: Session = Depends(get_db), x_user: str = Header(default="anonymous", max_length=USER_ID_MAX)):
    """发布 DRAFT（沉淀页）：资产 + 首个版本 + 模型/框架/代码引用关联，状态经状态机置 DRAFT。

    摘要与向量都在同一个事务里同步产出：索引晚一拍就意味着「刚沉淀的知识搜不到」，
    而这正是本系统要解决的问题。两者都可降级（网关不可用不影响发布）。
    TODO：库量上来后把 embedding 挪到后台任务，别让发布等一次网关往返。
    """
    summary, summary_source = make_summary(body.title, body.body_md)
    asset = KnowledgeAsset(
        title=body.title,
        direction=body.direction,
        tier=Tier.note,                       # 发布一律是工作记录；升 shared/core 由复用频次驱动
        summary=summary,
        summary_source=summary_source,
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
            asset_id=asset.id, kind=ref.kind, repo=ref.repo,
            path_or_key=ref.path_or_key, ref_id=ref.ref_id, note=ref.note, watch=ref.watch,
        ))

    db.flush()   # 索引要读刚建的框架/模型关联，先落到会话里
    indexing.refresh_doc(db, asset, body_md=body.body_md)
    indexing.refresh_embedding(db, asset, body_md=body.body_md)

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
