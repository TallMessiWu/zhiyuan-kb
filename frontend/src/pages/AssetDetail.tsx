import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, CURRENT_USER, get, post } from "../api/client";
import { DirectionChip, StatusPill, TierChip, fmtDate } from "../lib/display";
import { renderMarkdown } from "../lib/markdown";
import { useToast } from "../lib/toast";
import { userName } from "../lib/users";
import {
  REUSE_OUTCOME_ZH,
  TIER_ZH,
  VALIDATION_RESULT_ZH,
  type AssetDetail as Asset,
  type CodeRefOut,
  type FrameworkOut,
  type NotFoundOut,
  type StaleOut,
  type UsefulOut,
} from "../types";

// 知识详情页：正文 + 右栏（适用环境/关联代码/Issue·PR/验证记录/复用记录/历史版本）
// + 底部常驻三键反馈条。DOM 结构、文案、右栏顺序逐条对照 prototype 的 renderAsset()。
// 三键在 M3 全部接通：有用 → /feedback/useful、内容可能过时 → /feedback/stale、
// 没有找到答案 → /feedback/not-found。前两键只作用于本资产，第三键记的是「库里缺这份知识」，
// 与本资产无关 —— 所以它要问一句「你想找的是什么」，那句话才是缺口的内容。

/** 框架 + 版本：优先显示验证过的版本，否则退回 min–max 区间 */
function fwLabel(f: FrameworkOut): string {
  const range = [f.version_min, f.version_max].filter(Boolean).join("–");
  const ver = f.verified_on || range;
  return ver ? `${f.name} ${ver}` : f.name;
}

/** Issue/PR 编号：库里 repo 与 ref_id 分开存，展示时拼回原型的 vllm-ascend#1523 形态 */
function issueLabel(c: CodeRefOut): string {
  if (c.repo && c.ref_id) return `${c.repo}#${c.ref_id}`;
  return c.ref_id || c.repo || c.path_or_key;
}

type LoadState = "loading" | "ready" | "notfound" | "error";

export default function AssetDetail() {
  const { id } = useParams();
  const [asset, setAsset] = useState<Asset | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [errMsg, setErrMsg] = useState("");

  // 反馈条：「有用」展开一个可选的任务说明输入，「没有找到答案」展开一个待找内容输入。
  // 两者都只有一个字段，且同时最多展开一个（fbForm），不许再加字段（硬规则 6）。
  const [fbForm, setFbForm] = useState<"useful" | "gap" | null>(null);
  const [fbTask, setFbTask] = useState("");
  const [gapQuery, setGapQuery] = useState("");
  const [fbBusy, setFbBusy] = useState(false);
  const { setToast, toastBox } = useToast();
  const fbInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      setAsset(await get<Asset>(`/assets/${id}`));
      setLoadState("ready");
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setLoadState("notfound");
      } else {
        setErrMsg(e instanceof Error ? e.message : String(e));
        setLoadState("error");
      }
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  // 切换资产时收起反馈表单，避免上一条资产的输入残留
  useEffect(() => {
    setFbForm(null);
    setFbTask("");
    setGapQuery("");
  }, [id]);

  useEffect(() => {
    if (fbForm) fbInputRef.current?.focus();
  }, [fbForm]);

  async function confirmUseful() {
    if (!asset || fbBusy) return;
    setFbBusy(true);
    try {
      const r = await post<UsefulOut>("/feedback/useful", {
        asset_id: asset.id,
        task_note: fbTask.trim() || "（未填写任务说明）",
        search_event_id: null,
      });
      const fw = asset.frameworks[0];
      setFbForm(null);
      setFbTask("");
      setToast(
        <>
          已记录复用事件：{userName(CURRENT_USER)} · {fmtDate(new Date().toISOString())} ·{" "}
          {asset.code}
          {fw?.verified_on ? ` @ ${fw.verified_on}` : ""}
          {r.promoted && (
            <>
              <br />
              <b>该资产已凭「非作者成功复用」证据自动升级为 VERIFIED。</b>
            </>
          )}
          {!r.promoted && r.note && (
            <>
              <br />
              {r.note}
            </>
          )}
        </>,
      );
      await load();
    } catch (e) {
      setToast(<>记录失败：{e instanceof Error ? e.message : String(e)}</>);
    } finally {
      setFbBusy(false);
    }
  }

  // 「内容可能过时」：一次点击，不展开任何输入 —— 说明由服务端从使用者与原状态自动组装。
  async function reportStale() {
    if (!asset || fbBusy) return;
    setFbBusy(true);
    try {
      const r = await post<StaleOut>("/feedback/stale", { asset_id: asset.id, note: "" });
      setToast(
        r.merged ? (
          <>{r.note}</>
        ) : (
          <>
            已将 {asset.code} 标记为 REVIEW-DUE 并加入复核队列。{r.note}
          </>
        ),
      );
      await load();
    } catch (e) {
      setToast(<>记录失败：{e instanceof Error ? e.message : String(e)}</>);
    } finally {
      setFbBusy(false);
    }
  }

  // 「没有找到答案」：记的是知识缺口，不改这份资产的任何状态。
  async function confirmGap() {
    const question = gapQuery.trim();
    if (!question || fbBusy) return;
    setFbBusy(true);
    try {
      const r = await post<NotFoundOut>("/feedback/not-found", {
        query: question,
        search_event_id: null,
      });
      setFbForm(null);
      setGapQuery("");
      setToast(
        <>
          已记录知识缺口：「{r.gap.question}」
          <br />
          {r.created
            ? "它已进入首页待补充列表，累计后可由成员认领。"
            : `已并入同一需求的 ${r.gap.code}，累计被问 ${r.gap.hit_count} 次。`}
        </>,
      );
    } catch (e) {
      setToast(<>记录失败：{e instanceof Error ? e.message : String(e)}</>);
    } finally {
      setFbBusy(false);
    }
  }

  if (loadState === "loading") return <div className="empty">加载中…</div>;
  if (loadState === "notfound")
    return (
      <div className="empty">
        资产不存在或已被删除。<Link to="/">返回首页</Link>
      </div>
    );
  if (loadState === "error" || !asset)
    return (
      <div className="empty">
        加载失败：{errMsg}
        <br />
        <button className="btn sm" style={{ marginTop: 10 }} onClick={() => void load()}>
          重试
        </button>
      </div>
    );

  // STALE / ARCHIVED 不再收集复用证据（硬规则 4：它们已被隔离出正常流转）
  const dead = asset.status === "STALE" || asset.status === "ARCHIVED";
  const codeRefs = asset.code_refs.filter(
    (c) => c.kind === "repo_path" || c.kind === "config_key",
  );
  const issueRefs = asset.code_refs.filter((c) => c.kind === "issue" || c.kind === "pr");
  const versions = [...asset.versions].sort((a, b) => b.seq - a.seq);

  return (
    <>
      <div className="pagehead" style={{ marginBottom: 2 }}>
        <h1 style={{ maxWidth: "64ch" }}>{asset.title}</h1>
      </div>
      <div
        style={{
          display: "flex",
          gap: 8,
          alignItems: "center",
          flexWrap: "wrap",
          margin: "6px 0 14px",
        }}
      >
        <StatusPill status={asset.status} />
        <TierChip tier={asset.tier} />
        <DirectionChip direction={asset.direction} />
        <span className="meta">
          <span className="mono">{asset.code}</span>
          <span className="sep" />
          <span>
            {userName(asset.author_id)} 创建于 {fmtDate(asset.created_at)}
          </span>
          <span className="sep" />
          <span>更新 {fmtDate(asset.updated_at)}</span>
        </span>
      </div>

      {asset.status === "DRAFT" && (
        <div className="callout info">
          <span className="ct">尚未验证</span>
          本内容为{TIER_ZH[asset.tier]}
          ，还没有非作者成功复用的记录。可以参考，但请自行核对环境与版本；你复用成功后点下方「有用」即可为它提供验证证据。
        </div>
      )}
      {asset.status === "REVIEW_DUE" && (
        <div className="callout warn">
          <span className="ct">可能过时 —— 已进入复核队列</span>
          {asset.status_reason}{" "}
          <Link to="/review">查看 AI 变化摘要与更新草稿 →</Link>
        </div>
      )}
      {asset.status === "STALE" && (
        <div className="callout bad">
          <span className="ct">已确认失效</span>
          {asset.status_reason}
        </div>
      )}
      {asset.status === "ARCHIVED" && (
        <div
          className="callout bad"
          style={{ background: "var(--arch-bg)", borderColor: "var(--arch-line)" }}
        >
          <span className="ct" style={{ color: "var(--arch)" }}>
            已归档
          </span>
          {asset.status_reason}
        </div>
      )}

      <div className="detail-grid">
        <div className="doc">
          <div
            dangerouslySetInnerHTML={{
              __html: renderMarkdown(asset.current_version?.body_md ?? ""),
            }}
          />

          {!dead && (
            <>
              <div className="feedbackbar">
                <span className="q">用完这份知识了？一次点击记录结果：</span>
                <button
                  className="btn ok sm"
                  onClick={() => setFbForm("useful")}
                  disabled={fbBusy}
                >
                  ✓ 有用，完成任务
                </button>
                <button
                  className="btn warn sm"
                  onClick={() => void reportStale()}
                  disabled={fbBusy || asset.status === "REVIEW_DUE"}
                  title={
                    asset.status === "REVIEW_DUE"
                      ? "该资产已在复核队列中"
                      : "标记为可能过时并进入复核队列"
                  }
                >
                  内容可能过时
                </button>
                <button
                  className="btn sm"
                  onClick={() => setFbForm("gap")}
                  disabled={fbBusy}
                >
                  没有找到答案
                </button>
                <span style={{ fontSize: 11, color: "var(--ink3)" }}>
                  系统自动记录：使用者、资产版本、时间；无需填表。
                </span>
              </div>

              {fbForm && (
                <div
                  className="card"
                  style={{
                    marginTop: 10,
                    display: "flex",
                    gap: 8,
                    alignItems: "center",
                    flexWrap: "wrap",
                  }}
                >
                  <span style={{ fontSize: 12 }}>
                    {fbForm === "useful"
                      ? "这次用它做了什么？（可留空）"
                      : "你想找的是什么？记为知识缺口"}
                  </span>
                  <input
                    ref={fbInputRef}
                    type="text"
                    value={fbForm === "useful" ? fbTask : gapQuery}
                    onChange={(e) =>
                      fbForm === "useful" ? setFbTask(e.target.value) : setGapQuery(e.target.value)
                    }
                    onKeyDown={(e) => {
                      if (e.key !== "Enter") return;
                      void (fbForm === "useful" ? confirmUseful() : confirmGap());
                    }}
                    placeholder={
                      fbForm === "useful"
                        ? "如：客户环境部署 / 精度排查…"
                        : "如：PD 分离在 vllm-ascend 的部署方式…"
                    }
                    style={{
                      flex: 1,
                      minWidth: 180,
                      border: "1px solid var(--border2)",
                      background: "var(--surface)",
                      color: "var(--ink)",
                      borderRadius: 5,
                      padding: "5px 9px",
                      fontSize: 12.5,
                    }}
                  />
                  <button
                    className="btn pri sm"
                    onClick={() => void (fbForm === "useful" ? confirmUseful() : confirmGap())}
                    disabled={fbBusy || (fbForm === "gap" && !gapQuery.trim())}
                  >
                    {fbBusy ? "记录中…" : fbForm === "useful" ? "确认记录" : "记录缺口"}
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        <aside>
          <div className="sidebl card">
            <h4>适用环境</h4>
            <dl className="kv">
              <dt>模型</dt>
              <dd>{asset.models.length ? asset.models.join("、") : "—"}</dd>
              <dt>框架</dt>
              <dd className="mono" style={{ fontSize: 11.5 }}>
                {asset.frameworks.length ? asset.frameworks.map(fwLabel).join("、") : "—"}
              </dd>
              {asset.env_note && asset.env_note !== "—" && (
                <>
                  <dt>依赖</dt>
                  <dd className="mono" style={{ fontSize: 11.5 }}>
                    {asset.env_note}
                  </dd>
                </>
              )}
              <dt>状态原因</dt>
              <dd style={{ color: "var(--ink2)" }}>{asset.status_reason || "—"}</dd>
            </dl>
          </div>

          {codeRefs.length > 0 && (
            <div className="sidebl card">
              <h4>关联代码（变更将触发复核）</h4>
              {codeRefs.map((c) => (
                <div className="row" key={c.id}>
                  <span className="mono" style={{ fontSize: 11 }}>
                    {c.repo}
                  </span>
                  <br />
                  <span
                    className="mono"
                    style={{ fontSize: 11.5, color: "var(--accent)" }}
                  >
                    {c.path_or_key}
                  </span>
                  <br />
                  <span className="d">{c.note}</span>
                </div>
              ))}
            </div>
          )}

          {issueRefs.length > 0 && (
            <div className="sidebl card">
              <h4>关联 Issue / PR</h4>
              {issueRefs.map((c) => (
                <div className="row" key={c.id}>
                  <span className="mono" style={{ fontSize: 11 }}>
                    {issueLabel(c)}
                  </span>{" "}
                  {c.note}
                </div>
              ))}
            </div>
          )}

          <div className="sidebl card">
            <h4>验证记录（{asset.validations.length}）</h4>
            {asset.validations.length ? (
              asset.validations.map((v) => (
                <div className="row" key={v.id}>
                  <b>{userName(v.validator_id)}</b> · {fmtDate(v.at)} ·{" "}
                  <span
                    style={{ color: v.result === "pass" ? "var(--ok)" : "var(--bad)" }}
                  >
                    {VALIDATION_RESULT_ZH[v.result]}
                  </span>
                  <br />
                  <span className="d">{v.note}</span>
                </div>
              ))
            ) : (
              <div className="row d" style={{ color: "var(--ink3)" }}>
                暂无非作者验证
              </div>
            )}
          </div>

          <div className="sidebl card">
            <h4>复用记录（{asset.reuses.length}）</h4>
            {asset.reuses.length ? (
              asset.reuses.map((r) => (
                <div className="row" key={r.id}>
                  <b>{userName(r.user_id)}</b> · {fmtDate(r.at)}
                  {r.outcome !== "success" && (
                    <>
                      {" · "}
                      <span style={{ color: "var(--warn)" }}>
                        {REUSE_OUTCOME_ZH[r.outcome]}
                      </span>
                    </>
                  )}
                  <br />
                  <span className="d">{r.task_note}</span>
                </div>
              ))
            ) : (
              <div className="row" style={{ color: "var(--ink3)" }}>
                暂无
              </div>
            )}
          </div>

          {versions.length > 0 && (
            <div className="sidebl card">
              <h4>历史版本</h4>
              {versions.map((v) => (
                <div className="row" key={v.id}>
                  <span className="mono">v{v.seq}</span> · {fmtDate(v.created_at)} ·{" "}
                  {userName(v.created_by)}
                  <br />
                  <span className="d">{v.change_note}</span>
                </div>
              ))}
            </div>
          )}
        </aside>
      </div>

      {toastBox}
    </>
  );
}
