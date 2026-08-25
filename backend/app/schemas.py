"""Pydantic 模型 — 与 docs/api-contract.md 对应。M1 起逐个补全。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

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
    # 从缺口认领而来的沉淀：发布成功把该缺口置 resolved 并回链资产（M5 闭环）
    gap_id: int | None = None


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


# ---------- 复核队列（GET /review · POST /review/{id}/resolve，M4） ----------

class ReviewTaskOut(BaseModel):
    """队列条目：任务本体 + 资产列表项 + AI 产物。ai_draft 直接带正文 ——
    队列页的草稿折叠框要就地展示，不该为每条任务再拉一次版本接口。"""

    id: int
    asset: AssetBrief
    trigger: Trigger              # code_change / version_change / user_feedback
    trigger_detail: str
    diff_ref: str
    ai_impact_summary: str        # 空串 = 网关降级没生成（前端不渲染该块）
    ai_draft_version_id: int | None
    ai_draft: str = ""            # 草稿正文；空串 = 没有草稿（accept_draft 会 409）
    priority: int
    priority_label: str           # 高 / 中 / 低（阈值见 services/review_queue.py）
    usage_30d: int                # 近 30 天复用次数（队列行展示）
    created_at: datetime          # 检出时间


class ReviewListOut(BaseModel):
    items: list[ReviewTaskOut]
    total: int


class ReviewResolveIn(BaseModel):
    # Literal 而不是裸 str：非法动作应当是 422，而不是服务层的 ValueError → 500
    action: Literal["confirm", "accept_draft", "stale", "archive"]
    note: str = Field(default="", max_length=500)
    replaced_by: int | None = None   # archive 时的替代资产回链


class ReviewResolveOut(BaseModel):
    task_id: int
    action: str
    asset_id: int
    status: Status                # 处理后的资产状态
    current_version_id: int | None = None
    note: str = ""                # 结果说明（前端 toast 直接用）


# ---------- Webhook（POST /hooks/git，M4） ----------

class HookTaskOut(BaseModel):
    """一次事件对一个资产的处理回执。created=False 表示并进了去抖窗口内的已有任务。"""

    review_task_id: int
    asset_id: int
    created: bool


class HookAck(BaseModel):
    """webhook 应答。不相关事件也返回 200（handled=False + reason），
    否则 Git 平台会按失败重试，把同一个事件反复砸过来。"""

    handled: bool
    reason: str = ""
    event: str = ""               # push / tag / pr
    repo: str = ""
    matched_refs: int = 0         # 命中的 CodeReference 条数
    tasks: list[HookTaskOut] = []


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)   # 落 SearchEvent.query，对齐列宽


class Citation(BaseModel):
    """引用块。§6 规则 1：必须含 资产链接/命中段落/状态/适用版本/更新时间。
    title/framework/models 一并带上 —— 否则前端要为每条引用再拉一次详情。"""

    asset_id: int
    title: str
    fragment: str
    status: Status
    framework: str = ""
    fw_version: str = ""
    models: list[str] = []
    updated_at: datetime

    @computed_field
    @property
    def code(self) -> str:
        return f"KA-{self.asset_id:03d}"


class AskRisk(BaseModel):
    """风险提示。§6 规则 5：引用 REVIEW_DUE 必须附「可能过时」并链其 AI 变化摘要（M4 产物）。"""

    type: Literal["warn", "bad"] = "warn"
    text: str
    asset_id: int | None = None
    review_task_id: int | None = None
    ai_impact_summary: str = ""


class AskConflictSide(BaseModel):
    asset_id: int
    stand: str

    @computed_field
    @property
    def code(self) -> str:
        return f"KA-{self.asset_id:03d}"


class AskConflict(BaseModel):
    """§6 规则 4：多资产结论互斥时并列展示「说法 A / 说法 B」，系统不选边。"""

    a: AskConflictSide
    b: AskConflictSide


class AskResponse(BaseModel):
    answer_md: str
    citations: list[Citation] = []
    risks: list[AskRisk] = []
    conflict: AskConflict | None = None
    not_found: bool = False
    # 问答会话是需求事件（§9 分母）；前端「记录为知识缺口」要带它调 /feedback/not-found
    search_event_id: int


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


class ReuseRateBrief(BaseModel):
    """有效复用率（近 30 天）。分子分母必须随数字一起给 —— 硬规则 5 的展示面：
    让任何人都能看出这不是点击量。den=0 时 pct=None，前端显示「—」。"""

    num: int
    den: int
    pct: float | None


class HomeStats(BaseModel):
    """首页数字条。有效复用率与看板同一口径（services/metrics.py），不许另算一份。"""

    total: int          # 在库资产（不含 ARCHIVED）
    verified: int
    review_due: int
    open_gaps: int
    reuse_rate: ReuseRateBrief


class HomeResponse(BaseModel):
    stats: HomeStats
    recent_validated: list[RecentValidation] = []
    hot: list[AssetBrief] = []
    gaps: list[GapOut] = []


# ---------- 看板（GET /dashboard，口径见 docs/design.md §9） ----------

class TrendPoint(BaseModel):
    label: str      # "4月"
    value: float


class ReuseRateOut(ReuseRateBrief):
    trend: list[TrendPoint] = []


class SearchOkOut(BaseModel):
    """搜索成功率：去重后的搜索会话里「有结果且未反馈没找到答案」的占比。
    MVP 没有点击上报，以「有结果」代替原型口径的「有结果点击」（§9 落地注记）。"""

    pct: float | None
    ok_sessions: int
    total_sessions: int
    trend: list[TrendPoint] = []


class DashboardResponse(BaseModel):
    window_days: int
    generated_at: datetime
    reuse_rate: ReuseRateOut
    search_ok: SearchOkOut
    not_found_30d: int
    review_backlog: int        # REVIEW_DUE 资产数（目标 ≤5）
    verified_count: int
    draft_count: int
    open_gaps: int
    claimed_gaps: int
    gaps_total: int            # 未 resolved 的缺口总数（open + claimed）
    # 重复探索工时是估算（重复需求会话 × 平均排查工时），estimated 字段明示，不冒充实测
    rework_hours_trend: list[TrendPoint] = []
    rework_hours_estimated: bool = True
    rework_hours_per_miss: float
    coverage: dict[str, dict[str, int]]      # direction -> status -> 资产数
    reuse_by_direction: dict[str, int]       # direction -> 非作者成功复用事件数（全时段）


# ---------- 缺口 AI 底稿（POST /gaps/{id}/draft，M5） ----------

class GapDraft(BaseModel):
    """沉淀页预填底稿。全部是「建议」：作者确认三项后才走 POST /assets 发布为 DRAFT。"""

    title: str = ""
    problem: str = ""
    env: str = ""
    conclusion: str = ""
    tags: list[str] = []
    direction: Direction = Direction.feature
    models: list[str] = []
    framework: str = ""
    fw_version: str = ""
    code_refs: list[CodeRefIn] = []


class GapDraftOut(BaseModel):
    gap_id: int
    draft: GapDraft
    sources: list[int] = []    # 生成时参考的资产 id（检索上下文），前端可展示「基于 KA-xxx」
