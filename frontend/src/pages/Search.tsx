import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { get } from "../api/client";
import { DirectionChip, StatusPill, TierChip, fmtAgo } from "../lib/display";
import { Highlight } from "../lib/highlight";
import { userName } from "../lib/users";
import { DIRECTION_ZH, type Direction, type SearchResponse } from "../types";

// 搜索结果页：状态标注 + 命中高亮 + 分项得分（「为什么排在这里」）+ 历史资产开关 + 记缺口。
// DOM 结构与文案对照 prototype 的 renderSearch()；数据来自 GET /api/v1/search。
//
// 筛选语义（design.md §5）：方向/状态/历史是硬过滤，框架与模型也是硬过滤 —— 因为它们是
// 用户**显式**选的；只有从查询词里推断出来的框架才降权而非过滤（那部分在后端 rerank 里，
// 会以「框架匹配 +6 / 框架不符 −8」出现在分项得分中）。

// 框架清单与原型一致写死：M2 还没有 facet 接口，而这两个就是团队当前的全部方向。
const FRAMEWORKS = ["vllm-ascend", "sglang"];
const LIVE_STATUSES = ["VERIFIED", "DRAFT", "REVIEW_DUE"];
const DEAD_STATUSES = ["STALE", "ARCHIVED"];

type Filters = {
  q: string;
  direction: string;
  model: string;
  framework: string;
  status: string;
  hist: boolean;
};

function readFilters(params: URLSearchParams): Filters {
  return {
    q: params.get("q") ?? "",
    direction: params.get("direction") ?? "",
    model: params.get("model") ?? "",
    framework: params.get("framework") ?? "",
    status: params.get("status") ?? "",
    hist: params.get("hist") === "1",
  };
}

function toQueryString(f: Filters): string {
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  if (f.direction) p.set("direction", f.direction);
  if (f.model) p.set("model", f.model);
  if (f.framework) p.set("framework", f.framework);
  if (f.status) p.set("status", f.status);
  if (f.hist) p.set("hist", "1");
  return p.toString();
}

export default function Search() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => readFilters(params), [params]);

  const [draftQuery, setDraftQuery] = useState(filters.q);
  const [data, setData] = useState<SearchResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => setDraftQuery(filters.q), [filters.q]);

  useEffect(() => {
    setLoading(true);
    const qs = new URLSearchParams({
      q: filters.q,
      direction: filters.direction,
      model: filters.model,
      framework: filters.framework,
      status: filters.status,
      hist: String(filters.hist),
      limit: "20",
    });
    get<SearchResponse>(`/search?${qs}`)
      .then((r) => {
        setData(r);
        setError("");
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [filters]);

  const update = useCallback(
    (patch: Partial<Filters>) => setParams(toQueryString({ ...filters, ...patch })),
    [filters, setParams],
  );

  // 模型下拉的选项来自当前结果集：M2 没有 facet 接口，与其写死一份会过期的清单，
  // 不如照实呈现「这批结果里有哪些模型」，并保证当前选中的值不会从列表里消失。
  const modelOptions = useMemo(() => {
    const seen = new Set<string>(filters.model ? [filters.model] : []);
    data?.items.forEach((it) => it.asset.models.forEach((m) => m !== "通用" && seen.add(m)));
    return [...seen].sort();
  }, [data, filters.model]);

  const statuses = filters.hist ? DEAD_STATUSES : LIVE_STATUSES;
  const recallNote = data
    ? `召回：关键词 ${data.recall.keyword}（${data.recall.keyword_hits} 条）` +
      ` · 向量 ${data.recall.vector}（${data.recall.vector_hits} 条）`
    : "";

  return (
    <>
      <div className="pagehead">
        <h1>搜索结果</h1>
        <span className="sub">
          {filters.hist
            ? "历史资产模式：仅展示 STALE / ARCHIVED"
            : "排序：相关度 + 状态可信度 + 版本匹配 + 新鲜度 + 复用证据；VERIFIED 优先"}
        </span>
      </div>

      <form
        className="searchrow"
        onSubmit={(e) => {
          e.preventDefault();
          update({ q: draftQuery.trim() });
        }}
      >
        <input
          type="search"
          value={draftQuery}
          onChange={(e) => setDraftQuery(e.target.value)}
          placeholder="继续搜索…"
          autoComplete="off"
        />
        <button className="btn pri" type="submit">
          搜索
        </button>
      </form>

      <div className="filterbar">
        <label>
          类型{" "}
          <select value={filters.direction} onChange={(e) => update({ direction: e.target.value })}>
            <option value="">全部</option>
            {Object.entries(DIRECTION_ZH).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          模型{" "}
          <select value={filters.model} onChange={(e) => update({ model: e.target.value })}>
            <option value="">全部</option>
            {modelOptions.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label>
          框架{" "}
          <select value={filters.framework} onChange={(e) => update({ framework: e.target.value })}>
            <option value="">全部</option>
            {FRAMEWORKS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </label>
        <label>
          状态{" "}
          <select value={filters.status} onChange={(e) => update({ status: e.target.value })}>
            <option value="">全部</option>
            {statuses.map((s) => (
              <option key={s} value={s}>
                {s.replace("_", "-")}
              </option>
            ))}
          </select>
        </label>
        <label className="tog">
          <input
            type="checkbox"
            checked={filters.hist}
            // 切历史模式时清掉状态筛选：两组状态互斥，留着会得到空结果
            onChange={(e) => update({ hist: e.target.checked, status: "" })}
          />{" "}
          查看历史资产（STALE/ARCHIVED）
        </label>
        <span style={{ marginLeft: "auto", color: "var(--ink3)" }} title={recallNote}>
          {loading ? "检索中…" : `${data?.total ?? 0} 条结果`}
        </span>
      </div>

      {error && <div className="empty">检索失败：{error}</div>}

      {data?.items.map(({ asset, score }) => (
        <div className="result" key={asset.id}>
          <h3>
            <Link to={`/asset/${asset.id}`}>
              <Highlight text={asset.title} terms={data.terms} />
            </Link>
            &nbsp; <StatusPill status={asset.status} /> <TierChip tier={asset.tier} />{" "}
            <DirectionChip direction={asset.direction as Direction} />
          </h3>
          <div className="sum">
            <Highlight text={asset.summary} terms={data.terms} />
            {asset.summary_source === "ai" && (
              <span className="chip" style={{ marginLeft: 6 }} title="摘要由 AI 生成，正文与状态不受影响">
                AI 摘要
              </span>
            )}
          </div>
          <div className="meta">
            <span>{asset.models.join(" / ")}</span>
            <span className="sep" />
            <span className="mono">
              {asset.framework} {asset.fw_version}
            </span>
            <span className="sep" />
            <span>更新 {fmtAgo(asset.updated_at)}</span>
            <span className="sep" />
            <span>
              复用 <b className="num">{asset.reuse_count}</b> 次
            </span>
            <span className="sep" />
            <span>{userName(asset.author_id)}</span>
          </div>
          <details className="why">
            <summary>为什么排在这里（得分 {score.total}）</summary>
            <div className="parts">
              {score.parts.map((p, i) => (
                <span key={i} className={`p ${p.value > 0 ? "pos" : p.value < 0 ? "neg" : ""}`}>
                  {p.label}{" "}
                  <b>
                    {p.value > 0 ? "+" : ""}
                    {p.value}
                  </b>
                </span>
              ))}
            </div>
          </details>
        </div>
      ))}

      {!loading && data?.items.length === 0 && (
        <div className="empty">
          没有匹配的资产。
          <br />
          <br />
          <button className="btn" disabled title="M3 反馈闭环上线后可用">
            没有找到答案 —— 记录为知识缺口
          </button>
          <div style={{ marginTop: 8, fontSize: 11.5 }}>
            缺口会进入首页待补充列表，累计后可由成员认领生成草稿。
          </div>
        </div>
      )}

      {!loading && (data?.items.length ?? 0) > 0 && (
        <div style={{ marginTop: 14 }}>
          <button className="btn sm" disabled title="M3 反馈闭环上线后可用">
            这些结果没有解决我的问题 → 记录知识缺口
          </button>
        </div>
      )}
    </>
  );
}
