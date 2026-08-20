// 展示辅助：状态 pill / 分级 chip / 方向 chip / 时间格式化。
// 规则照搬 prototype/kms-prototype.html 的 pill() / tierChip() / typeChip() / fmtAgo()。
// M2 的搜索结果页会复用这里，别在页面里重复实现。

import {
  DIRECTION_ZH,
  STATUS_ZH,
  TIER_ZH,
  type Direction,
  type Status,
  type Tier,
} from "../types";

/** ISO 时间串 → YYYY-MM-DD（后端统一发 UTC，直接截日期部分，避免时区漂移） */
export function fmtDate(iso: string): string {
  return (iso ?? "").slice(0, 10);
}

/** 相对时间：今天 / N 天前 / N 个月前 / N 年前 */
export function fmtAgo(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const n = Math.round((Date.now() - t) / 86400000);
  if (n <= 0) return "今天";
  if (n < 30) return `${n} 天前`;
  if (n < 365) return `${Math.round(n / 30)} 个月前`;
  return `${Math.round(n / 365)} 年前`;
}

/** 状态 pill：VERIFIED 只显示码值，其余显示「码值 · 中文」 */
export function StatusPill({ status }: { status: Status }) {
  const code = status.replace("_", "-");
  return (
    <span className={`pill ${status}`}>
      {status === "VERIFIED" ? code : `${code} · ${STATUS_ZH[status]}`}
    </span>
  );
}

export function TierChip({ tier }: { tier: Tier }) {
  return <span className={`tier ${tier}`}>{TIER_ZH[tier]}</span>;
}

export function DirectionChip({ direction }: { direction: Direction }) {
  return <span className="tier">{DIRECTION_ZH[direction]}</span>;
}
