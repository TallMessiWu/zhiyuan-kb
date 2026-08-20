"""Pydantic 模型 — 与 docs/api-contract.md 对应。M1 起逐个补全。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, computed_field

from .models import Direction, RefKind, Status, Tier, Trigger, VersionOrigin


# 长度上限对齐 models.py 的列宽：sqlite 不校验 VARCHAR 长度，只有 PG 会在插入时报错，
# 所以必须在入口挡住，否则超长输入在测试里静默通过、上了 PG 才 500。

class CodeRefIn(BaseModel):
    # 用枚举而不是裸 str：否则非法 kind 要等到 RefKind(...) 抛 ValueError，变成 500 而不是 422
    kind: RefKind = RefKind.repo_path
    repo: str = Field(default="", max_length=200)
    path_or_key: str = Field(default="", max_length=400)
    ref_id: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=300)
    watch: bool = True


class AssetCreate(BaseModel):
    """沉淀页发布 DRAFT：三项确认（问题=title、环境、结论）+ AI 带出的元数据。"""

    title: str = Field(max_length=300)
    direction: Direction
    body_md: str                      # 问题/环境/结论 三节 markdown
    models: list[str] = []
    framework: str = Field(default="vllm-ascend", max_length=64)
    fw_version: str = Field(default="", max_length=40)
    env_note: str = Field(default="", max_length=200)
    tags: list[str] = []
    source: str = Field(default="ai_session", max_length=32)
    source_ref: str = Field(default="", max_length=300)
    code_refs: list[CodeRefIn] = []


class ScorePartOut(BaseModel):
    label: str
    value: float


class ScoreOut(BaseModel):
    total: float
    parts: list[ScorePartOut]


class AssetBrief(BaseModel):
    id: int
    title: str
    direction: Direction
    tier: Tier
    status: Status
    summary: str
    tags: list[str]
    author_id: str
    reuse_count: int
    updated_at: datetime

    model_config = {"from_attributes": True}

    @computed_field  # 展示用编号，与原型一致（id=16 → KA-016）；库内主键仍是 int
    @property
    def code(self) -> str:
        return f"KA-{self.id:03d}"


# ---------- 详情页（GET /assets/{id}） ----------

class VersionBrief(BaseModel):
    """版本历史条目（不含正文）。"""

    id: int
    seq: int
    change_note: str
    created_by: str
    created_from: VersionOrigin
    created_at: datetime

    model_config = {"from_attributes": True}


class VersionOut(VersionBrief):
    body_md: str


class FrameworkOut(BaseModel):
    name: str
    repo_url: str = ""
    version_min: str = ""
    version_max: str = ""
    verified_on: str = ""


class CodeRefOut(BaseModel):
    id: int
    kind: RefKind
    repo: str
    path_or_key: str
    ref_id: str
    note: str
    watch: bool

    model_config = {"from_attributes": True}


class ValidationOut(BaseModel):
    id: int
    version_id: int | None
    validator_id: str
    kind: str    # reuse_success / manual_review / review_confirm
    result: str  # pass / fail / stale_confirm
    note: str
    at: datetime

    model_config = {"from_attributes": True}


class ReuseOut(BaseModel):
    id: int
    version_id: int | None
    user_id: str
    task_note: str
    outcome: str
    fw_version_at_use: str
    at: datetime

    model_config = {"from_attributes": True}


class TransitionOut(BaseModel):
    """状态流转审计流水条目（GET /assets/{id}/transitions）。"""

    id: int
    asset_id: int
    from_status: Status | None
    to_status: Status
    trigger: Trigger
    evidence_type: str
    evidence_id: int | None
    actor: str
    note: str
    at: datetime

    model_config = {"from_attributes": True}


class AssetDetail(AssetBrief):
    """详情：资产 + 当前版本 + 验证/复用记录 + 代码引用 + 版本历史（docs/api-contract.md）。"""

    source: str
    source_ref: str
    env_note: str
    status_reason: str
    created_at: datetime
    models: list[str] = []
    frameworks: list[FrameworkOut] = []
    current_version: VersionOut | None = None
    versions: list[VersionBrief] = []
    code_refs: list[CodeRefOut] = []
    validations: list[ValidationOut] = []
    reuses: list[ReuseOut] = []


class SearchItem(BaseModel):
    asset: AssetBrief
    score: ScoreOut


class SearchResponse(BaseModel):
    items: list[SearchItem]
    search_event_id: int
    hist: bool = False


class UsefulIn(BaseModel):
    asset_id: int
    task_note: str = Field(default="", max_length=500)
    search_event_id: int | None = None


class UsefulOut(BaseModel):
    """三键反馈「有用」的结果。promoted=True 表示本次证据把 DRAFT 升成了 VERIFIED。"""

    reuse_event_id: int
    asset_id: int
    status: Status
    reuse_count: int
    promoted: bool = False
    note: str = ""


class StaleIn(BaseModel):
    asset_id: int
    note: str = Field(default="", max_length=500)


class NotFoundIn(BaseModel):
    query: str = Field(max_length=500)
    search_event_id: int | None = None


class ReviewResolveIn(BaseModel):
    action: str  # confirm / accept_draft / stale / archive
    note: str = ""
    replaced_by: int | None = None


class AskIn(BaseModel):
    question: str


class Citation(BaseModel):
    asset_id: int
    fragment: str
    status: Status
    fw_version: str
    updated_at: datetime


class AskResponse(BaseModel):
    answer_md: str
    citations: list[Citation] = []
    risks: list[str] = []
    conflict: dict | None = None
    not_found: bool = False
