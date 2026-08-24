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
    """列表项（搜索结果 / 首页）。带上 models·framework·fw_version 是因为原型的结果行
    要渲染「模型 / 框架版本 / 更新 / 复用 / 作者」这条 meta —— 否则前端得为每条结果再拉一次详情。"""

    id: int
    title: str
    direction: Direction
    tier: Tier
    status: Status
    summary: str
    summary_source: str = "rule"   # author / ai / rule，前端据此标注「AI 摘要」
    tags: list[str]
    author_id: str
    reuse_count: int
    updated_at: datetime
    models: list[str] = []
    framework: str = ""
    fw_version: str = ""

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
    frameworks: list[FrameworkOut] = []      # models / framework / fw_version 继承自 AssetBrief
    current_version: VersionOut | None = None
    versions: list[VersionBrief] = []
    code_refs: list[CodeRefOut] = []
    validations: list[ValidationOut] = []
    reuses: list[ReuseOut] = []


class SearchItem(BaseModel):
    asset: AssetBrief
    score: ScoreOut


class RecallOut(BaseModel):
    """这次搜索实际用了哪条召回路 —— 排序可解释也包括「怎么召回的」，
    而且能力探测降级（没有 pgvector / 网关不可达）必须让人看得见，不能静默。"""

    keyword: str          # pg_tsvector / portable
    vector: str           # pgvector / python / off / unavailable
    keyword_hits: int = 0
    vector_hits: int = 0


class SearchResponse(BaseModel):
    items: list[SearchItem]
    search_event_id: int
    hist: bool = False
    total: int = 0
    terms: list[str] = []      # 前端 <mark> 高亮用的查询词（已滤掉单字）
    recall: RecallOut


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


class StaleOut(BaseModel):
    """「内容可能过时」的结果。merged=True 表示并进了去抖窗口内已存在的复核任务。"""

    feedback_id: int
    asset_id: int
    status: Status
    review_task_id: int
    merged: bool = False
    note: str = ""


class NotFoundIn(BaseModel):
    query: str = Field(default="", max_length=500)
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


# ---------- 首页（GET /home）与缺口（GET /gaps） ----------

class GapOut(BaseModel):
    """知识缺口：来自「没有找到答案」反馈的累计需求（docs/design.md §9 分母的一部分）。"""

    id: int
    question: str
    hit_count: int
    first_at: datetime
    last_at: datetime
    reporters: list[str]
    status: str            # open / claimed / resolved
    claimed_by: str

    model_config = {"from_attributes": True}

    @computed_field  # 展示编号，与原型的 GAP-01 一致
    @property
    def code(self) -> str:
        return f"GAP-{self.id:02d}"


class NotFoundOut(BaseModel):
    """「没有找到答案」的结果。created=False 表示并入了同一需求的已有缺口。

    定义在这里而不是跟另外两键放一起：它要嵌 GapOut，得等 GapOut 先定义。
    """

    feedback_id: int
    gap: GapOut
    created: bool


class RecentValidation(BaseModel):
    """首页「最近验证」条目：资产 + 这次验证的证据（谁、何时、说明）。"""

    asset: AssetBrief
    validator_id: str
    note: str
    at: datetime


class HomeStats(BaseModel):
    """首页数字条。有效复用率不在这里 —— 它的口径（非作者成功复用 ÷ 需求事件）
    是看板指标，M5 由事件表实时聚合，M2 拿半成品数字冒充等于违反硬规则 5。"""

    total: int          # 在库资产（不含 ARCHIVED）
    verified: int
    review_due: int
    open_gaps: int


class HomeResponse(BaseModel):
    stats: HomeStats
    recent_validated: list[RecentValidation] = []
    hot: list[AssetBrief] = []
    gaps: list[GapOut] = []
