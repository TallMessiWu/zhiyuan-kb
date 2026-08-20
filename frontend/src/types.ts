// 与 backend/app/schemas.py 对齐 — 后端改 schema 必须同步此文件。

export type Direction = "model" | "chain" | "feature";
export type Tier = "note" | "shared" | "core";
export type Status = "DRAFT" | "VERIFIED" | "REVIEW_DUE" | "STALE" | "ARCHIVED";

export const STATUS_ZH: Record<Status, string> = {
  VERIFIED: "已验证",
  DRAFT: "尚未验证",
  REVIEW_DUE: "可能过时",
  STALE: "已失效",
  ARCHIVED: "已归档",
};

export interface AssetBrief {
  id: number;
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
