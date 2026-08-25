import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, get, post } from "../api/client";
import { StatusPill } from "../lib/display";
import { fmtAgo } from "../lib/display";
import { useToast } from "../lib/toast";
import { userName } from "../lib/users";
import type { GapDraftOut, GapOut, HomeResponse } from "../types";

// 首页：统一搜索框 + 快捷入口 + 数字条 + 最近验证 + 热门 + 待认领缺口 + 状态规则速览。
// DOM 结构与文案逐条对照 prototype 的 renderHome()。数据来自 GET /api/v1/home。
//
// 数字条第五格「本月有效复用率」：与看板同一口径（services/metrics.py 实时聚合），
// den=0 时后端给 pct=null，这里显示「—」—— 硬规则 5 禁止拿近似值或 0% 冒充。

const QUICK_LINKS = ["显存 OOM", "投机推理", "图模式"];

export default function Home() {
  const navigate = useNavigate();
  const [data, setData] = useState<HomeResponse | null>(null);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");
  const [claiming, setClaiming] = useState<number | null>(null);
  const { setToast, toastBox } = useToast();

  useEffect(() => {
    get<HomeResponse>("/home")
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  // 认领 = 登记「我来写这份知识」（M3：只就地更新那一行，不产出资产）。
  // M5 接上底稿：认领成功后请求 AI 预填（POST /gaps/{id}/draft），带着结果跳沉淀页；
  // 网关降级（503）时照样跳 —— 认领本身不受影响，作者手写就是。
  async function claimGap(gap: GapOut) {
    if (claiming !== null) return;
    setClaiming(gap.id);
    try {
      const claimed = await post<GapOut>(`/gaps/${gap.id}/claim`, {});
      setData((prev) =>
        prev ? { ...prev, gaps: prev.gaps.map((g) => (g.id === claimed.id ? claimed : g)) } : prev,
      );
    } catch (e) {
      setToast(<>认领失败：{e instanceof Error ? e.message : String(e)}</>);
      setClaiming(null);
      return;
    }
    try {
      const out = await post<GapDraftOut>(`/gaps/${gap.id}/draft`, {});
      navigate("/capture", {
        state: { gapId: gap.id, gapCode: gap.code, question: gap.question, draftOut: out },
      });
    } catch (e) {
      const message =
        e instanceof ApiError && e.code === "AI_UNAVAILABLE"
          ? "AI 底稿暂不可用，已为你打开空白沉淀页；认领仍然有效。"
          : `底稿生成失败（${e instanceof Error ? e.message : String(e)}），已为你打开空白沉淀页。`;
      setToast(<>{message}</>);
      navigate("/capture", {
        state: { gapId: gap.id, gapCode: gap.code, question: gap.question, draftOut: null },
      });
    } finally {
      setClaiming(null);
    }
  }

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    navigate(`/search?q=${encodeURIComponent(q.trim())}`);
  };

  return (
    <>
      <div className="pagehead">
        <h1>找推理知识，先看可信度</h1>
        <span className="sub">覆盖 vLLM Ascend / SGLang · 模型结构 · 执行链路 · 推理特性</span>
      </div>

      <form className="searchrow" onSubmit={submit}>
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索：如「MLA 图模式限制」「显存 OOM」「EngineCore 调用链」…"
          autoComplete="off"
        />
        <button className="btn pri" type="submit">
          搜索
        </button>
      </form>

      <div className="filterbar">
        快捷入口：
        {QUICK_LINKS.map((text) => (
          <Link key={text} className="chip" to={`/search?q=${encodeURIComponent(text)}`}>
            {text}
          </Link>
        ))}
        <Link className="chip" to="/search?q=%E8%B0%83%E7%94%A8%E9%93%BE">
          执行链路
        </Link>
        <Link className="chip" to="/search?hist=1">
          查看历史资产（STALE / ARCHIVED）
        </Link>
      </div>

      {error && <div className="empty">首页数据加载失败：{error}</div>}

      <div className="statstrip">
        <div className="st">
          <div className="v">{data?.stats.total ?? "–"}</div>
          <div className="l">在库资产（不含归档）</div>
        </div>
        <div className="st">
          <div className="v">{data?.stats.verified ?? "–"}</div>
          <div className="l">VERIFIED 已验证</div>
        </div>
        <div className="st">
          <div className="v">{data?.stats.review_due ?? "–"}</div>
          <div className="l">待复核 REVIEW-DUE</div>
        </div>
        <div className="st">
          <div className="v">{data?.stats.open_gaps ?? "–"}</div>
          <div className="l">待认领知识缺口</div>
        </div>
        <div
          className="st"
          title={
            data
              ? `非作者成功复用 ${data.stats.reuse_rate.num} ÷ 需求事件 ${data.stats.reuse_rate.den}（近 30 天，口径同数据看板）`
              : "口径为「非作者成功复用 ÷ 需求事件」（design.md §9）"
          }
        >
          <div className="v">
            {data ? (data.stats.reuse_rate.pct === null ? "—" : `${Math.round(data.stats.reuse_rate.pct)}%`) : "–"}
          </div>
          <div className="l">本月有效复用率</div>
        </div>
      </div>

      <div className="home-cols">
        <div>
          <div className="section-t">最近验证</div>
          {data?.recent_validated.length === 0 && (
            <div className="mini-item">还没有验证记录 —— 非作者点「有用，完成任务」即产生。</div>
          )}
          {data?.recent_validated.map((r) => (
            <div className="mini-item" key={`${r.asset.id}-${r.at}`}>
              <span className="n">{r.at.slice(5, 10)}</span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <Link to={`/asset/${r.asset.id}`}>{r.asset.title}</Link>
                <div className="meta" style={{ marginTop: 1 }}>
                  <span>{userName(r.validator_id)} 验证</span>
                  {r.note && <span className="sep" />}
                  {r.note && <span>{r.note}</span>}
                </div>
              </span>
              <StatusPill status={r.asset.status} />
            </div>
          ))}

          <div className="section-t">热门知识（按非作者复用次数）</div>
          {data?.hot.map((a) => (
            <div className="mini-item" key={a.id}>
              <span className="n num">×{a.reuse_count}</span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <Link to={`/asset/${a.id}`}>{a.title}</Link>
              </span>
              <StatusPill status={a.status} />
            </div>
          ))}
        </div>

        <div>
          <div className="section-t">待补充知识缺口（来自「没有找到答案」反馈）</div>
          <div className="card" style={{ padding: "6px 16px" }}>
            {data?.gaps.length === 0 && (
              <div className="gap-item">
                <div className="m">暂无缺口记录。</div>
              </div>
            )}
            {data?.gaps.map((g) => (
              <div className="gap-item" key={g.id}>
                <div className="q">{g.question}</div>
                <div className="m">
                  近 30 天被问 <b className="num">{g.hit_count}</b> 次 · 最近 {fmtAgo(g.last_at)} ·
                  提出人：{g.reporters.map(userName).join("、")}
                  {g.status === "claimed" ? (
                    <> · <span style={{ color: "var(--ok)" }}>已由 {userName(g.claimed_by)} 认领</span></>
                  ) : (
                    <>
                      {" · "}
                      <button
                        className="btn sm"
                        onClick={() => void claimGap(g)}
                        disabled={claiming !== null}
                      >
                        {claiming === g.id ? "认领中，正在生成底稿…" : "认领并生成草稿"}
                      </button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>

          <div className="section-t">状态规则速览</div>
          <div className="card" style={{ fontSize: 12, lineHeight: 2 }}>
            <StatusPill status="VERIFIED" /> 已由非作者成功复用或人工验证，默认优先展示
            <br />
            <StatusPill status="DRAFT" /> 工作记录 / 未经非作者验证，可长期存在、不强制维护
            <br />
            <StatusPill status="REVIEW_DUE" /> 关联代码或版本变化、或被反馈过时，降权展示
            <br />
            <StatusPill status="STALE" /> 确认失效，不进入正常搜索，仅历史入口可见
            <br />
            <StatusPill status="ARCHIVED" /> 已被替代或重复，仅历史入口可见
          </div>
        </div>
      </div>

      {toastBox}
    </>
  );
}
