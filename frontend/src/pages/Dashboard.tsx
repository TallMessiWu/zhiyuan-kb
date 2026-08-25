import { useEffect, useState } from "react";
import { get } from "../api/client";
import { DIRECTION_ZH } from "../types";
import type { DashboardResponse, Direction, Status, TrendPoint } from "../types";

// 数据看板：7 指标 + 方向×状态覆盖矩阵。UI 对照 prototype 的 renderDash()，
// 数据来自 GET /api/v1/dashboard（全部由事件表实时聚合，口径见 design.md §9）。
//
// 与原型的两点差异：原型的趋势数字与解读文案是写死的演示数据，这里全部来自接口；
// 原型末尾「本会话新增复用事件（审计示例）」是内存演示态，真实现不提供。

const DIRECTIONS: Direction[] = ["model", "chain", "feature"];
const STATUSES: Status[] = ["VERIFIED", "DRAFT", "REVIEW_DUE", "STALE", "ARCHIVED"];

function TrendBars({ points }: { points: TrendPoint[] }) {
  const max = Math.max(...points.map((p) => p.value), 1); // 全 0 时避免除 0
  return (
    <div className="trend">
      {points.map((p) => (
        <div className="bar" key={p.label} title={String(p.value)}>
          <i style={{ height: `${Math.round((p.value / max) * 100)}%` }} />
          <em>{p.label}</em>
        </div>
      ))}
    </div>
  );
}

/** den=0 时显示「—」：「没人有需求」和「有需求没人复用」是两码事，不许显示成 0% */
function BigPct({ pct, unit = "%" }: { pct: number | null; unit?: string }) {
  return (
    <div className="bigv">
      {pct === null ? "—" : Math.round(pct)}
      {pct !== null && <span className="u">{unit}</span>}
    </div>
  );
}

export default function Dashboard() {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    get<DashboardResponse>("/dashboard")
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  if (error) return <div className="empty">看板数据加载失败：{error}</div>;
  if (!data) return <div className="empty">加载中…</div>;

  const reworkNow = data.rework_hours_trend.at(-1)?.value ?? 0;

  return (
    <>
      <div className="pagehead">
        <h1>数据看板</h1>
        <span className="sub">
          口径以复用事件为准，不用点击数冒充复用 · 统计区间：近 {data.window_days} 天（截至{" "}
          {data.generated_at.slice(0, 10)}）
        </span>
      </div>

      <div className="dashgrid">
        <div className="card">
          <h4 style={{ margin: "0 0 4px", fontSize: 12, color: "var(--ink3)" }}>有效复用率</h4>
          <BigPct pct={data.reuse_rate.pct} />
          <div className="footnote">
            = 非作者成功复用事件 <b className="num">{data.reuse_rate.num}</b> ÷ 适用知识需求事件{" "}
            <b className="num">{data.reuse_rate.den}</b>
            。需求事件 = 搜索/问答会话（同人同主题 30 分钟合并）+ 记录的缺口；成功复用 =
            点击「有用，完成任务」且复用者 ≠ 作者。
          </div>
          <TrendBars points={data.reuse_rate.trend} />
        </div>

        <div className="card">
          <h4 style={{ margin: "0 0 4px", fontSize: 12, color: "var(--ink3)" }}>搜索成功率</h4>
          <BigPct pct={data.search_ok.pct} />
          <div className="footnote">
            有结果且未反馈「没有找到答案」的搜索会话占比（
            <b className="num">{data.search_ok.ok_sessions}</b>/
            <b className="num">{data.search_ok.total_sessions}</b>
            ）。本期「没找到答案」共 <b className="num">{data.not_found_30d}</b> 次，沉淀为{" "}
            {data.gaps_total} 个缺口（{data.claimed_gaps} 个已认领）。
          </div>
          <TrendBars points={data.search_ok.trend} />
        </div>

        <div className="card">
          <h4 style={{ margin: "0 0 4px", fontSize: 12, color: "var(--ink3)" }}>
            重复探索工时（估算）
          </h4>
          <BigPct pct={reworkNow} unit="h" />
          <div className="footnote">
            = 同主题重复出现的需求会话 × 平均排查 {data.rework_hours_per_miss}h。
            该指标为估算值，仅看趋势，不计入考核。
          </div>
          <TrendBars points={data.rework_hours_trend} />
        </div>

        <div className="card">
          <h4 style={{ margin: "0 0 4px", fontSize: 12, color: "var(--ink3)" }}>
            当前库存与积压
          </h4>
          <div style={{ display: "flex", gap: 26, flexWrap: "wrap", marginTop: 6 }}>
            <div>
              <div className="bigv">{data.verified_count}</div>
              <div className="footnote">VERIFIED 资产</div>
            </div>
            <div>
              <div className="bigv" style={{ color: "var(--warn)" }}>
                {data.review_backlog}
              </div>
              <div className="footnote">
                REVIEW-DUE 积压
                <br />
                （目标 ≤ 5，超限报警）
              </div>
            </div>
            <div>
              <div className="bigv">{data.draft_count}</div>
              <div className="footnote">DRAFT（允许长期存在）</div>
            </div>
            <div>
              <div className="bigv">{data.open_gaps}</div>
              <div className="footnote">未认领缺口</div>
            </div>
          </div>
        </div>
      </div>

      <div className="section-t">知识覆盖矩阵（方向 × 状态）</div>
      <div className="tbl-wrap">
        <table className="tbl cov">
          <tbody>
            <tr>
              <th>方向</th>
              {STATUSES.map((s) => (
                <th key={s}>{s.replace("_", "-")}</th>
              ))}
              <th>非作者复用合计</th>
            </tr>
            {DIRECTIONS.map((d) => (
              <tr key={d}>
                <td>{DIRECTION_ZH[d]}</td>
                {STATUSES.map((s) => {
                  const n = data.coverage[d]?.[s] ?? 0;
                  return (
                    <td className={`num ${n ? "c1" : "c0"}`} key={s}>
                      {n || "·"}
                    </td>
                  );
                })}
                <td className="num">{data.reuse_by_direction[d] ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="footnote">
        覆盖解读：VERIFIED 存量薄、REVIEW-DUE 或 STALE 偏多的方向，结合首页缺口列表看就是当前最该补的空白。
      </div>
    </>
  );
}
