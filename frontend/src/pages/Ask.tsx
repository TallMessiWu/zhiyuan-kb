import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, post } from "../api/client";
import { StatusPill, fmtAgo } from "../lib/display";
import { renderMarkdown } from "../lib/markdown";
import { useToast } from "../lib/toast";
import type { AskResponse, Citation, NotFoundOut } from "../types";

// AI 问答页：引用块（状态/适用版本/更新时间）+ 冲突并列 + 风险提示 + not_found 固定话术。
// UI 对照 prototype 的 renderAsk()/renderAnswer()。数据来自 POST /api/v1/ask（M5）。
//
// 与原型的三点差异（原型是内存演示，这里是真链路）：
// - 原型的预置问题携带写死的答案；这里只是把问句填进真 /ask（同样四条，方便演示与验收）。
// - 问答要等网关 10–25s，多一个「正在检索并生成…」的进行中条目。
// - 网关不可用（503 AI_UNAVAILABLE）渲染「问答暂不可用」条目 —— 后端没有兜底答案，
//   这不是错误页而是明确语义：检索与浏览不受影响。

const PRESET_QUESTIONS = [
  "DeepSeek-V3 在 vllm-ascend 上怎么启用 MLA？有什么限制？",
  "aclgraph / TorchAir 图模式怎么开？",
  "Ascend 上多机多卡部署应该用 Ray 还是 MP？",
  "PD 分离在 vllm-ascend 上怎么部署？",
];

/** 一条问答记录：进行中 / 成功 / 暂不可用 */
type QaEntry =
  | { kind: "pending"; q: string }
  | { kind: "answer"; q: string; data: AskResponse; gapReported: boolean }
  | { kind: "unavailable"; q: string; message: string };

function CiteBlock({ cite }: { cite: Citation }) {
  return (
    <div className="cite">
      <div className="ct-h">
        <StatusPill status={cite.status} />
        <Link to={`/asset/${cite.asset_id}`}>
          <b>{cite.code}</b> {cite.title}
        </Link>
      </div>
      <div className="meta">
        {cite.framework && (
          <>
            <span>
              适用：<span className="mono">{cite.framework} {cite.fw_version}</span>
            </span>
            <span className="sep" />
          </>
        )}
        {cite.models.length > 0 && (
          <>
            <span>{cite.models.join("/")}</span>
            <span className="sep" />
          </>
        )}
        <span>更新 {fmtAgo(cite.updated_at)}</span>
      </div>
      {cite.fragment && <div className="frag">{cite.fragment}</div>}
    </div>
  );
}

export default function Ask() {
  const [q, setQ] = useState("");
  const [log, setLog] = useState<QaEntry[]>([]);
  const busy = log.some((e) => e.kind === "pending");
  const { setToast, toastBox } = useToast();

  async function submit(question: string) {
    const trimmed = question.trim();
    if (!trimmed || busy) return;
    setQ("");
    setLog((prev) => [...prev, { kind: "pending", q: trimmed }]);
    try {
      const data = await post<AskResponse>("/ask", { question: trimmed });
      setLog((prev) =>
        prev.map((e) =>
          e.kind === "pending" ? { kind: "answer", q: trimmed, data, gapReported: false } : e,
        ),
      );
    } catch (e) {
      const message =
        e instanceof ApiError && e.code === "AI_UNAVAILABLE"
          ? e.message
          : `请求失败：${e instanceof Error ? e.message : String(e)}`;
      setLog((prev) =>
        prev.map((entry) =>
          entry.kind === "pending" ? { kind: "unavailable", q: trimmed, message } : entry,
        ),
      );
    }
  }

  // not_found 的一键记缺口：复用三键之三，带上问答会话的 search_event_id（需求闭环）
  async function reportGap(index: number, entry: Extract<QaEntry, { kind: "answer" }>) {
    try {
      const out = await post<NotFoundOut>("/feedback/not-found", {
        query: entry.q,
        search_event_id: entry.data.search_event_id,
      });
      setLog((prev) =>
        prev.map((e, i) => (i === index && e.kind === "answer" ? { ...e, gapReported: true } : e)),
      );
      setToast(
        out.created ? (
          <>已记录知识缺口 {out.gap.code}，累计次数将影响其在首页的优先级。</>
        ) : (
          <>已累计到缺口 {out.gap.code}（第 {out.gap.hit_count} 次被提出）。</>
        ),
      );
    } catch (e) {
      setToast(<>记录失败：{e instanceof Error ? e.message : String(e)}</>);
    }
  }

  return (
    <>
      <div className="pagehead">
        <h1>AI 问答</h1>
        <span className="sub">
          仅基于知识资产回答 · 引用来源与状态 · STALE 不参与结论 · 冲突并列展示
        </span>
      </div>

      <form
        className="searchrow"
        onSubmit={(e) => {
          e.preventDefault();
          void submit(q);
        }}
      >
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="问一个推理问题，例如「DeepSeek MLA 有什么限制」…"
          autoComplete="off"
        />
        <button className="btn pri" type="submit" disabled={busy}>
          {busy ? "生成中…" : "提问"}
        </button>
      </form>

      <div className="preset-qs">
        {PRESET_QUESTIONS.map((p) => (
          <button key={p} onClick={() => void submit(p)} disabled={busy}>
            {p}
          </button>
        ))}
      </div>

      {log.map((entry, i) => (
        <div key={i}>
          <div className="qa-q">{entry.q}</div>
          <div className="qa-a">
            {entry.kind === "pending" && (
              <p style={{ color: "var(--ink3)" }}>正在检索知识资产并生成回答…</p>
            )}

            {entry.kind === "unavailable" && (
              <div className="callout bad">
                <span className="ct">问答暂不可用</span>
                {entry.message}
              </div>
            )}

            {entry.kind === "answer" && entry.data.not_found && (
              <>
                <div className="callout bad">
                  <span className="ct">没有找到经过验证的知识</span>
                  {entry.data.answer_md.replace(/^没有找到经过验证的知识[。，]?/, "")}
                </div>
                <p>
                  <button
                    className="btn sm"
                    disabled={entry.gapReported}
                    onClick={() => void reportGap(i, entry)}
                  >
                    {entry.gapReported ? "已记录为知识缺口" : "记录 / 累计为知识缺口"}
                  </button>
                </p>
              </>
            )}

            {entry.kind === "answer" && !entry.data.not_found && (
              <>
                {/* answer_md 来自后端约束生成，renderMarkdown 先整体转义再套标签，注入安全 */}
                <div dangerouslySetInnerHTML={{ __html: renderMarkdown(entry.data.answer_md) }} />

                {entry.data.conflict && (
                  <div className="callout warn">
                    <span className="ct">多份资产结论冲突 —— 系统展示差异，不代为裁决</span>
                    <b>说法 A：</b>
                    <Link to={`/asset/${entry.data.conflict.a.asset_id}`}>
                      {entry.data.conflict.a.code}
                    </Link>
                    <br />
                    <span style={{ color: "var(--ink2)" }}>{entry.data.conflict.a.stand}</span>
                    <br />
                    <b>说法 B：</b>
                    <Link to={`/asset/${entry.data.conflict.b.asset_id}`}>
                      {entry.data.conflict.b.code}
                    </Link>
                    <br />
                    <span style={{ color: "var(--ink2)" }}>{entry.data.conflict.b.stand}</span>
                  </div>
                )}

                {entry.data.risks.map((risk, ri) => (
                  <div className={`callout ${risk.type}`} key={ri}>
                    <span className="ct">
                      {risk.type === "bad" ? "来源可信度警告" : "可能过时提示"}
                    </span>
                    {risk.text}
                    {risk.ai_impact_summary && (
                      <>
                        <br />
                        <span style={{ color: "var(--ink2)" }}>
                          AI 变化摘要：{risk.ai_impact_summary}
                        </span>
                      </>
                    )}
                    {risk.review_task_id !== null && (
                      <>
                        {" "}
                        <Link to="/review">前往复核队列 →</Link>
                      </>
                    )}
                  </div>
                ))}

                {entry.data.citations.length > 0 && (
                  <>
                    <div style={{ fontSize: "11.5px", color: "var(--ink3)", marginTop: 10 }}>
                      来源（{entry.data.citations.length}）：
                    </div>
                    {entry.data.citations.map((c) => (
                      <CiteBlock key={c.asset_id} cite={c} />
                    ))}
                  </>
                )}
              </>
            )}
          </div>
        </div>
      ))}

      {log.length === 0 && (
        <div className="empty" style={{ marginTop: 20 }}>
          回答规则：每个结论必须给出来源资产、命中段落、资产状态与适用版本；没有可靠依据时明确说「没有找到经过验证的知识」，不编造。
        </div>
      )}

      {toastBox}
    </>
  );
}
