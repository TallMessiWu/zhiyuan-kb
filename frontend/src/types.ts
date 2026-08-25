// 与 backend/app/schemas.py 对齐 — 后端改 schema 必须同步此文件。

export type Direction = "model" | "chain" | "feature";
export type Tier = "note" | "shared" | "core";
export type Status = "DRAFT" | "VERIFIED" | "REVIEW_DUE" | "STALE" | "ARCHIVED";

/** CodeReference.kind */
export type CodeRefKind = "repo_path" | "config_key" | "issue" | "pr";
/** AssetVersion.created_from —— ai_draft 只能产生草稿，不产生可信状态（硬规则 1） */
export type VersionSource = "author" | "ai_draft" | "review";
/** ValidationRecord.result */
export type ValidationResult = "pass" | "fail" | "stale_confirm";
/** ReuseEvent.outcome */
export type ReuseOutcome = "success" | "partial" | "failed";

export const STATUS_ZH: Record<Status, string> = {
  VERIFIED: "已验证",
  DRAFT: "尚未验证",
  REVIEW_DUE: "可能过时",
  STALE: "已失效",
  ARCHIVED: "已归档",
};

export const TIER_ZH: Record<Tier, string> = {
  core: "核心资产",
  shared: "共享知识",
  note: "工作记录",
};

/** 三级知识方向（原型里叫 TYPE_ZH，后端字段名为 direction） */
export const DIRECTION_ZH: Record<Direction, string> = {
  model: "模型结构",
  chain: "执行链路",
  feature: "推理特性",
};

export const VALIDATION_RESULT_ZH: Record<ValidationResult, string> = {
  pass: "通过",
  fail: "未通过",
  stale_confirm: "确认失效",
};

export const REUSE_OUTCOME_ZH: Record<ReuseOutcome, string> = {
  success: "成功",
  partial: "部分成功",
  failed: "未成功",
};

/** 摘要来源：author 手写 / ai 网关生成 / rule 规则式兜底（硬规则 1：AI 产出必须可识别） */
export type SummarySource = "author" | "ai" | "rule";

export interface AssetBrief {
  id: number;
  code: string;
  title: string;
  direction: Direction;
  tier: Tier;
  status: Status;
  summary: string;
  summary_source: SummarySource;
  tags: string[];
  author_id: string;
  reuse_count: number;
  updated_at: string;
  /** 列表行 meta 用：后端已批量查好，前端不必为每条结果再拉详情 */
  models: string[];
  framework: string;
  fw_version: string;
}

export interface ScorePart {
  label: string;
  value: number;
}

export interface SearchItem {
  asset: AssetBrief;
  score: { total: number; parts: ScorePart[] };
}

/** 这次搜索实际走了哪条召回路。降级（没 pgvector / 网关不可达）必须能看见，不能静默 */
export interface RecallInfo {
  /** pg_tsvector = PG 全文索引；portable = Python 加权词频兜底 */
  keyword: "pg_tsvector" | "portable";
  /** pgvector = ANN 索引；python = 内存余弦；off = 关闭；unavailable = 网关拿不到查询向量 */
  vector: "pgvector" | "python" | "off" | "unavailable";
  keyword_hits: number;
  vector_hits: number;
}

export interface SearchResponse {
  items: SearchItem[];
  search_event_id: number;
  hist: boolean;
  total: number;
  /** 高亮词（后端已滤掉单字） */
  terms: string[];
  recall: RecallInfo;
}

/** 引用块（§6 规则 1：必须含 资产/命中段落/状态/适用版本/更新时间） */
export interface Citation {
  asset_id: number;
  code: string;
  title: string;
  fragment: string;
  status: Status;
  framework: string;
  fw_version: string;
  models: string[];
  updated_at: string;
}

/** 风险提示（§6 规则 5：引用 REVIEW_DUE 附「可能过时」并链其 M4 AI 影响摘要） */
export interface AskRisk {
  type: "warn" | "bad";
  text: string;
  asset_id: number | null;
  review_task_id: number | null;
  ai_impact_summary: string;
}

export interface AskConflictSide {
  asset_id: number;
  code: string;
  stand: string;
}

/** §6 规则 4：结论互斥时并列展示，系统不选边 */
export interface AskConflict {
  a: AskConflictSide;
  b: AskConflictSide;
}

export interface AskResponse {
  answer_md: string;
  citations: Citation[];
  risks: AskRisk[];
  conflict: AskConflict | null;
  not_found: boolean;
  /** 问答会话的需求事件 id；「记录为知识缺口」要带它调 /feedback/not-found */
  search_event_id: number;
}

/* ---------- 资产详情（GET /assets/{id}） ---------- */

/** 版本列表项：不含正文 */
export interface VersionBrief {
  id: number;
  seq: number;
  change_note: string;
  created_by: string;
  created_from: VersionSource;
  created_at: string;
}

/** 当前版本：带正文 markdown */
export interface VersionOut extends VersionBrief {
  body_md: string;
}

export interface FrameworkOut {
  name: string;
  repo_url: string;
  version_min: string;
  version_max: string;
  verified_on: string;
}

export interface CodeRefOut {
  id: number;
  kind: CodeRefKind;
  repo: string;
  /** repo_path → 文件路径；config_key → 配置项；issue/pr → 兜底标识 */
  path_or_key: string;
  /** issue/pr 的编号，如 vllm-ascend#1523 */
  ref_id: string;
  note: string;
  /** true 表示该引用变更会触发 REVIEW-DUE（M4） */
  watch: boolean;
}

export interface ValidationOut {
  id: number;
  version_id: number;
  validator_id: string;
  /** 证据来源，如 reuse_success / manual_check；后端未冻结取值 */
  kind: string;
  result: ValidationResult;
  note: string;
  at: string;
}

export interface ReuseOut {
  id: number;
  version_id: number;
  user_id: string;
  task_note: string;
  outcome: ReuseOutcome;
  fw_version_at_use: string;
  at: string;
}

/** 状态流水（硬规则 2：每次状态变更都必须有一条带证据的记录） */
export interface TransitionOut {
  id: number;
  asset_id: number;
  from_status: Status | null;
  to_status: Status;
  trigger: string;
  evidence_type: string;
  evidence_id: number | null;
  actor: string;
  note: string;
  at: string;
}

export interface AssetDetail {
  id: number;
  code: string;
  title: string;
  direction: Direction;
  tier: Tier;
  status: Status;
  summary: string;
  tags: string[];
  author_id: string;
  reuse_count: number;
  created_at: string;
  updated_at: string;
  source: string;
  source_ref: string;
  env_note: string;
  status_reason: string;
  models: string[];
  frameworks: FrameworkOut[];
  current_version: VersionOut;
  /** 全部版本，后端按 seq 返回；前端展示时倒序 */
  versions: VersionBrief[];
  code_refs: CodeRefOut[];
  validations: ValidationOut[];
  reuses: ReuseOut[];
}

/* ---------- 写入 ---------- */

/** POST /assets 请求体（沉淀页只让用户确认 问题/环境/结论 三项，硬规则 6） */
export interface AssetCreate {
  title: string;
  direction: Direction;
  body_md: string;
  models: string[];
  framework: string;
  fw_version: string;
  env_note: string;
  tags: string[];
  source: string;
  source_ref: string;
  code_refs: Array<Omit<CodeRefOut, "id">>;
  /** 缺口认领而来的沉淀：发布成功把该缺口置 resolved 并回链（M5 闭环） */
  gap_id?: number;
}

/** POST /feedback/useful 响应；promoted=true 表示凭非作者复用证据升级为 VERIFIED */
export interface UsefulOut {
  reuse_event_id: number;
  asset_id: number;
  status: Status;
  reuse_count: number;
  promoted: boolean;
  /** promoted=false 时的说明，如「作者本人复用不作为升级证据」 */
  note: string;
}

/** 三键之二「内容可能过时」（POST /feedback/stale）。merged=true 表示并入了已有复核任务。 */
export interface StaleOut {
  feedback_id: number;
  asset_id: number;
  status: Status;
  review_task_id: number;
  merged: boolean;
  note: string;
}

/** 三键之三「没有找到答案」（POST /feedback/not-found）。created=false 表示累计到了已有缺口。 */
export interface NotFoundOut {
  feedback_id: number;
  gap: GapOut;
  created: boolean;
}

/* ---------- 首页（GET /home）与缺口（GET /gaps） ---------- */

export interface GapOut {
  id: number;
  code: string;
  question: string;
  hit_count: number;
  first_at: string;
  last_at: string;
  reporters: string[];
  status: "open" | "claimed" | "resolved";
  claimed_by: string;
}

/* ---------- 复核队列（GET /review · POST /review/{id}/resolve，M4） ---------- */

/** ReviewTask.trigger 里会出现的三种（全集是 Trigger 枚举，进队列的只有这三类流转） */
export type ReviewTrigger = "code_change" | "version_change" | "user_feedback";
export type ReviewAction = "confirm" | "accept_draft" | "stale" | "archive";

export const REVIEW_TRIGGER_ZH: Record<ReviewTrigger, string> = {
  code_change: "代码变更",
  version_change: "版本变更",
  user_feedback: "人工反馈",
};

export interface ReviewTaskOut {
  id: number;
  asset: AssetBrief;
  trigger: ReviewTrigger;
  trigger_detail: string;
  /** seed/原型数据是 "add:/del:" 前缀的 diff 行；webhook 建的是 compare/PR 链接 */
  diff_ref: string;
  /** 空串 = 网关降级没生成（不渲染该块） */
  ai_impact_summary: string;
  ai_draft_version_id: number | null;
  /** 草稿正文；空串 = 没有草稿（「接受 AI 更新草稿」按钮 disabled，后端也会 409） */
  ai_draft: string;
  priority: number;
  priority_label: "高" | "中" | "低";
  usage_30d: number;
  created_at: string;
}

export interface ReviewListOut {
  items: ReviewTaskOut[];
  total: number;
}

/** 四选一处理结果；note 是给 toast 的结果说明，后端已按原型文案组装 */
export interface ReviewResolveOut {
  task_id: number;
  action: ReviewAction;
  asset_id: number;
  status: Status;
  current_version_id: number | null;
  note: string;
}

/** 首页「最近验证」：资产 + 这次验证的证据 */
export interface RecentValidation {
  asset: AssetBrief;
  validator_id: string;
  note: string;
  at: string;
}

/** 有效复用率（近 30 天）。分子分母随数字一起给（硬规则 5）；den=0 时 pct=null，显示「—」 */
export interface ReuseRateBrief {
  num: number;
  den: number;
  pct: number | null;
}

/** 首页数字条。复用率与看板同一口径（services/metrics.py），不许前端另算 */
export interface HomeStats {
  total: number;
  verified: number;
  review_due: number;
  open_gaps: number;
  reuse_rate: ReuseRateBrief;
}

export interface HomeResponse {
  stats: HomeStats;
  recent_validated: RecentValidation[];
  hot: AssetBrief[];
  gaps: GapOut[];
}

/* ---------- 看板（GET /dashboard，口径见 design.md §9） ---------- */

export interface TrendPoint {
  label: string;
  value: number;
}

export interface DashboardResponse {
  window_days: number;
  generated_at: string;
  reuse_rate: ReuseRateBrief & { trend: TrendPoint[] };
  search_ok: {
    pct: number | null;
    ok_sessions: number;
    total_sessions: number;
    trend: TrendPoint[];
  };
  not_found_30d: number;
  review_backlog: number;
  verified_count: number;
  draft_count: number;
  open_gaps: number;
  claimed_gaps: number;
  gaps_total: number;
  /** 估算指标：rework_hours_estimated 明示，不冒充实测 */
  rework_hours_trend: TrendPoint[];
  rework_hours_estimated: boolean;
  rework_hours_per_miss: number;
  coverage: Record<Direction, Record<Status, number>>;
  reuse_by_direction: Record<Direction, number>;
}

/* ---------- 缺口 AI 底稿（POST /gaps/{id}/draft，M5） ---------- */

/** 沉淀页预填底稿：全部是建议，作者确认三项后走 POST /assets 发布 */
export interface GapDraft {
  title: string;
  problem: string;
  env: string;
  conclusion: string;
  tags: string[];
  direction: Direction;
  models: string[];
  framework: string;
  fw_version: string;
  code_refs: Array<Omit<CodeRefOut, "id" | "ref_id"> & { ref_id?: string }>;
}

export interface GapDraftOut {
  gap_id: number;
  draft: GapDraft;
  /** 生成时参考的资产 id（沉淀页可展示「基于 KA-xxx」） */
  sources: number[];
}
