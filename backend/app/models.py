"""核心数据模型 — 与 docs/design.md §3 一一对应。

约定：
- 状态历史在 StatusTransition（append-only），KnowledgeAsset.status 只是当前态冗余；
  两者必须经 services.state_machine.transition() 同事务写入。
- 事件表（ReuseEvent / SearchEvent / UserFeedback / StatusTransition）只 INSERT。
- 检索用的派生数据放在独立表（AssetSearchDoc / AssetEmbedding），主表不加列；
  两张表都只用跨库通用的列类型，本文件才能继续被 sqlite 测试加载（PG 专属的
  tsvector 生成列与 pgvector 列由迁移单独追加，ORM 不声明，见 services/recall.py）。
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------- 枚举 ----------

class Direction(str, enum.Enum):
    model = "model"      # 模型结构
    chain = "chain"      # 推理执行链路
    feature = "feature"  # 推理特性


class Tier(str, enum.Enum):
    note = "note"      # 工作记录
    shared = "shared"  # 共享知识
    core = "core"      # 核心资产


class Status(str, enum.Enum):
    DRAFT = "DRAFT"
    VERIFIED = "VERIFIED"
    REVIEW_DUE = "REVIEW_DUE"
    STALE = "STALE"
    ARCHIVED = "ARCHIVED"


# 注意 name="transition_trigger"：PostgreSQL 的 pg_catalog 里有内置伪类型 trigger，
# 且 pg_catalog 隐式排在 search_path 最前，枚举若叫 trigger 会被伪类型遮蔽，
# 建表时报 column "trigger" has pseudo-type trigger。
class Trigger(str, enum.Enum):
    auto_create = "auto_create"
    nonauthor_reuse = "nonauthor_reuse"
    manual_validation = "manual_validation"
    code_change = "code_change"
    version_change = "version_change"
    user_feedback = "user_feedback"
    review_confirm = "review_confirm"
    review_accept_draft = "review_accept_draft"
    review_stale = "review_stale"
    review_replace = "review_replace"


class VersionOrigin(str, enum.Enum):
    author = "author"
    ai_draft = "ai_draft"
    review = "review"


class RefKind(str, enum.Enum):
    repo_path = "repo_path"
    config_key = "config_key"
    issue = "issue"
    pr = "pr"


# ---------- 主体 ----------

class KnowledgeAsset(Base):
    __tablename__ = "knowledge_asset"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    direction: Mapped[Direction] = mapped_column(Enum(Direction))
    tier: Mapped[Tier] = mapped_column(Enum(Tier), default=Tier.note)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.DRAFT, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    # 摘要来源：author 手写 / ai 网关生成 / rule 规则式兜底。硬规则 1 要求 AI 产出可识别，
    # 详情页与沉淀页据此标注「AI 摘要」。
    summary_source: Mapped[str] = mapped_column(String(16), default="rule")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    author_id: Mapped[str] = mapped_column(String(64), index=True)
    # 与 AssetVersion.asset_id 构成环形外键：use_alter 让建表期先建两张表、再 ALTER 加约束，
    # 否则 PostgreSQL 会在 CREATE TABLE 阶段因前向引用失败（SQLite 不较真，会掩盖这个问题）。
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("asset_version.id", use_alter=True, name="fk_knowledge_asset_current_version"),
        nullable=True,
    )
    # 来源：ai_session / issue / pr / wiki / manual + 引用标识
    source: Mapped[str] = mapped_column(String(32), default="manual")
    source_ref: Mapped[str] = mapped_column(String(300), default="")
    # 适用环境的依赖描述（如 "CANN 8.2.RC1 · torch_npu 2.5.1.post1"），详情页右栏「依赖」行。
    # 框架/版本区间在 AssetFramework，模型在 AssetModel；此列只承载不便结构化的依赖串。
    env_note: Mapped[str] = mapped_column(String(200), default="")
    status_reason: Mapped[str] = mapped_column(Text, default="")
    reuse_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    versions: Mapped[list[AssetVersion]] = relationship(
        back_populates="asset", foreign_keys="AssetVersion.asset_id", order_by="AssetVersion.seq.desc()"
    )
    transitions: Mapped[list[StatusTransition]] = relationship(back_populates="asset")
    code_refs: Mapped[list[CodeReference]] = relationship(back_populates="asset")
    validations: Mapped[list[ValidationRecord]] = relationship(back_populates="asset")
    reuses: Mapped[list[ReuseEvent]] = relationship(back_populates="asset")


class AssetVersion(Base):
    __tablename__ = "asset_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("knowledge_asset.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # v1, v2, ...
    body_md: Mapped[str] = mapped_column(Text)
    change_note: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[str] = mapped_column(String(64))
    created_from: Mapped[VersionOrigin] = mapped_column(Enum(VersionOrigin), default=VersionOrigin.author)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    asset: Mapped[KnowledgeAsset] = relationship(back_populates="versions", foreign_keys=[asset_id])


class StatusTransition(Base):
    """状态流转审计流水（append-only）。谁、何时、从哪到哪、凭什么证据。"""

    __tablename__ = "status_transition"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("knowledge_asset.id"), index=True)
    from_status: Mapped[Status | None] = mapped_column(Enum(Status), nullable=True)
    to_status: Mapped[Status] = mapped_column(Enum(Status))
    trigger: Mapped[Trigger] = mapped_column(Enum(Trigger, name="transition_trigger"))
    evidence_type: Mapped[str] = mapped_column(String(40), default="")   # reuse_event / user_feedback / review_task / validation
    evidence_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")     # system 或 user_id
    note: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    asset: Mapped[KnowledgeAsset] = relationship(back_populates="transitions")


# ---------- 维度 ----------

class Framework(Base):
    __tablename__ = "framework"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)  # vllm-ascend / sglang / 通用
    repo_url: Mapped[str] = mapped_column(String(300), default="")


class AssetFramework(Base):
    """资产 × 框架，携带版本区间与实测版本。"""

    __tablename__ = "asset_framework"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("knowledge_asset.id"), index=True)
    framework_id: Mapped[int] = mapped_column(ForeignKey("framework.id"))
    version_min: Mapped[str] = mapped_column(String(40), default="")
    version_max: Mapped[str] = mapped_column(String(40), default="")
    verified_on: Mapped[str] = mapped_column(String(40), default="")  # 实测版本


class Model(Base):
    __tablename__ = "model"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)  # 「通用」为保留值
    family: Mapped[str] = mapped_column(String(64), default="")
    arch_notes: Mapped[str] = mapped_column(String(200), default="")  # MLA / GQA / MoE ...
    hf_ref: Mapped[str] = mapped_column(String(200), default="")


class AssetModel(Base):
    __tablename__ = "asset_model"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("knowledge_asset.id"), index=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("model.id"))


class CodeReference(Base):
    """自动更新的锚点：watch=true 的 repo_path / config_key 参与 webhook 匹配。"""

    __tablename__ = "code_reference"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("knowledge_asset.id"), index=True)
    kind: Mapped[RefKind] = mapped_column(Enum(RefKind))
    repo: Mapped[str] = mapped_column(String(200), default="")
    path_or_key: Mapped[str] = mapped_column(String(400), default="")
    ref_id: Mapped[str] = mapped_column(String(100), default="")  # issue/pr 编号
    note: Mapped[str] = mapped_column(String(300), default="")
    watch: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_sha: Mapped[str] = mapped_column(String(64), default="")

    asset: Mapped[KnowledgeAsset] = relationship(back_populates="code_refs")


# ---------- 证据与事件（只 INSERT） ----------

class ValidationRecord(Base):
    __tablename__ = "validation_record"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("knowledge_asset.id"), index=True)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("asset_version.id"), nullable=True)
    validator_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))    # reuse_success / manual_review / review_confirm
    result: Mapped[str] = mapped_column(String(32))  # pass / fail / stale_confirm
    env_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    asset: Mapped[KnowledgeAsset] = relationship(back_populates="validations")


class ReuseEvent(Base):
    __tablename__ = "reuse_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("knowledge_asset.id"), index=True)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("asset_version.id"), nullable=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    task_note: Mapped[str] = mapped_column(String(500), default="")
    outcome: Mapped[str] = mapped_column(String(16), default="success")  # success / partial / failed
    search_event_id: Mapped[int | None] = mapped_column(ForeignKey("search_event.id"), nullable=True)
    fw_version_at_use: Mapped[str] = mapped_column(String(64), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    asset: Mapped[KnowledgeAsset] = relationship(back_populates="reuses")


class SearchEvent(Base):
    __tablename__ = "search_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    query: Mapped[str] = mapped_column(String(500))
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    mode: Mapped[str] = mapped_column(String(16), default="search")  # search / qa
    result_ids: Mapped[list] = mapped_column(JSON, default=list)
    clicked_ids: Mapped[list] = mapped_column(JSON, default=list)
    session_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserFeedback(Base):
    __tablename__ = "user_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64))
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_asset.id"), nullable=True)
    search_event_id: Mapped[int | None] = mapped_column(ForeignKey("search_event.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(24))  # useful / maybe_stale / not_found
    note: Mapped[str] = mapped_column(String(500), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeGap(Base):
    __tablename__ = "knowledge_gap"

    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(String(500))
    hit_count: Mapped[int] = mapped_column(Integer, default=1)
    first_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reporters: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open / claimed / resolved
    claimed_by: Mapped[str] = mapped_column(String(64), default="")
    resolved_asset_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_asset.id"), nullable=True)


class ReviewTask(Base):
    """复核队列条目。priority = 近 30 天使用 × 风险，排序用。"""

    __tablename__ = "review_task"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("knowledge_asset.id"), index=True)
    trigger: Mapped[Trigger] = mapped_column(Enum(Trigger, name="transition_trigger"))
    trigger_detail: Mapped[str] = mapped_column(Text, default="")
    diff_ref: Mapped[str] = mapped_column(String(400), default="")
    ai_impact_summary: Mapped[str] = mapped_column(Text, default="")
    ai_draft_version_id: Mapped[int | None] = mapped_column(ForeignKey("asset_version.id"), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open / done
    handled_by: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(24), default="")  # confirm / accept_draft / stale / archive
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------- 检索派生数据（M2；可由 scripts/reindex.py 全量重建） ----------

class AssetSearchDoc(Base):
    """关键词检索的索引文档：jieba 预分词后的四个字段桶。

    字段权重 title×4 / tags×3 / summary×2 / body×1（docs/design.md §5）在 PG 上由
    setweight(A/B/C/D) 表达 —— 迁移会加一个 tsv 生成列（GENERATED ALWAYS AS ... STORED）
    让数据库自己从这四列算 tsvector，写入方只管写分词串。ORM 不声明 tsv：TSVECTOR 是
    PG 专属类型，声明了 models.py 就没法在 sqlite 上 create_all，测试全得依赖 PG。
    sqlite 没有 tsv，关键词召回自动走 recall.py 里的可移植 Python 打分路径。
    """

    __tablename__ = "asset_search_doc"

    asset_id: Mapped[int] = mapped_column(ForeignKey("knowledge_asset.id"), primary_key=True)
    tok_title: Mapped[str] = mapped_column(Text, default="")
    tok_tags: Mapped[str] = mapped_column(Text, default="")
    tok_summary: Mapped[str] = mapped_column(Text, default="")
    tok_body: Mapped[str] = mapped_column(Text, default="")
    # 未分词的原文拼接：可移植路径做子串匹配用（中文不分词也应命中，与原型的 includes 行为一致）
    raw_text: Mapped[str] = mapped_column(Text, default="")
    # 源文本指纹，重建索引时跳过没变的资产
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AssetEmbedding(Base):
    """资产向量（bge-m3，来自 services/ai.py::embed）。

    向量本体存 JSON（PG 上是 JSONB）：跨库一致，没有 pgvector 扩展也能用 —— 召回时
    拉回 Python 算余弦，团队级库量（千条内）完全够用。PG 装了 pgvector 时，迁移会额外
    建一个 vec vector(dim) 列 + HNSW 索引，recall.py 探测到就改走 ANN 加速路径。
    """

    __tablename__ = "asset_embedding"

    asset_id: Mapped[int] = mapped_column(ForeignKey("knowledge_asset.id"), primary_key=True)
    model: Mapped[str] = mapped_column(String(64), default="")
    dim: Mapped[int] = mapped_column(Integer, default=0)
    vector: Mapped[list] = mapped_column(JSON, default=list)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
