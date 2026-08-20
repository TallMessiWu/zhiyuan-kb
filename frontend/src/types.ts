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

export interface AssetBrief {
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
  updated_at: string;
}

export interface ScorePart {
  label: string;
  value: number;
}

export interface SearchItem {
  asset: AssetBrief;
  score: { total: number; parts: ScorePart[] };
}

export interface SearchResponse {
  items: SearchItem[];
  search_event_id: number;
  hist: boolean;
}

export interface Citation {
  asset_id: number;
  fragment: string;
  status: Status;
  fw_version: string;
  updated_at: string;
}

export interface AskResponse {
  answer_md: string;
  citations: Citation[];
  risks: string[];
  conflict: Record<string, unknown> | null;
  not_found: boolean;
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
