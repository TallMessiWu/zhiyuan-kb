// 复核队列（M4）：触发原因 + AI 变化摘要 + diff 片段 + 折叠 AI 草稿 + 四选一。
// UI/文案对照 prototype 的 renderReview()/revAct()；toast 文案由后端 resolve 返回（note），
// 前端不再自己组装 —— 三键反馈也是这个分工。
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, CURRENT_USER, get, post } from "../api/client";
import { fmtDate } from "../lib/display";
import { useToast } from "../lib/toast";
import { userName } from "../lib/users";
import {
  REVIEW_TRIGGER_ZH,
  type ReviewAction,
  type ReviewListOut,
  type ReviewResolveOut,
  type ReviewTaskOut,
} from "../types";

/** seed/原型的 diff_ref 是 "add:/del:" 前缀的行；webhook 建的是 compare/PR 链接 */
function DiffBlock({ diffRef }: { diffRef: string }) {
  if (/^https?:\/\//.test(diffRef)) {
    return (
      <div className="rev-difflink">
        <a href={diffRef} target="_blank" rel="noreferrer">
          查看变更 diff →
        </a>
      </div>
    );
  }
  const lines = diffRef.split("\n").filter(Boolean);
  if (lines.length === 0) return null;
  return (
    <div className="rev-diff">
      {lines.map((line, i) => {
        const m = /^(add|del):\s?(.*)$/.exec(line);
        return (
          <div key={i} className={`diffline ${m ? m[1] : ""}`}>
            {m ? m[2] : line}
          </div>
        );
      })}
    </div>
  );
}

function ReviewItem({
  task,
  busy,
  onResolve,
}: {
  task: ReviewTaskOut;
  busy: boolean;
  onResolve: (task: ReviewTaskOut, action: ReviewAction) => void;
}) {
  return (
    <div className="rev-item">
      <div className="rev-h">
        <span className="rev-trigger">
          {REVIEW_TRIGGER_ZH[task.trigger] ?? task.trigger} · {task.priority_label}优先
        </span>
        <Link to={`/asset/${task.asset.id}`} className="rev-title">
          {task.asset.title}
        </Link>
        <span className="meta rev-right">
          <span>
            近 30 天复用 <b className="num">{task.usage_30d}</b> 次
          </span>
          <span className="sep" />
          <span>{fmtDate(task.created_at)} 检出</span>
        </span>
      </div>
      <div className="rev-b">
        <div className="rev-detail">
          <b>触发：</b>
          {task.trigger_detail}
        </div>
        {task.ai_impact_summary && (
          <div className="rev-ai">
            <b>AI 变化摘要：</b>
            {task.ai_impact_summary}
          </div>
        )}
        {task.diff_ref && <DiffBlock diffRef={task.diff_ref} />}
        {task.ai_draft && (
          <details className="draftbox">
            <summary>AI 更新草稿（接受后生成新版本，状态回到 DRAFT，不会自动 VERIFIED）</summary>
            <div className="inner">{task.ai_draft}</div>
          </details>
        )}
        <div className="rev-actions">
          <button className="btn ok sm" disabled={busy} onClick={() => onResolve(task, "confirm")}>
            ✓ 内容仍然有效
          </button>
          <button
            className="btn sm"
            disabled={busy || !task.ai_draft}
            title={task.ai_draft ? undefined : "该任务没有 AI 更新草稿（生成时网关不可用）"}
            onClick={() => onResolve(task, "accept_draft")}
          >
            接受 AI 更新草稿
          </button>
          <button className="btn bad sm" disabled={busy} onClick={() => onResolve(task, "stale")}>
            标记失效
          </button>
          <button className="btn sm" disabled={busy} onClick={() => onResolve(task, "archive")}>
            已被替代 → 归档
          </button>
        </div>
      </div>
    </div>
  );
}

export default function Review() {
  const [items, setItems] = useState<ReviewTaskOut[] | null>(null);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const { setToast, toastBox } = useToast();

  useEffect(() => {
    get<ReviewListOut>("/review")
      .then((r) => setItems(r.items))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const resolve = async (task: ReviewTaskOut, action: ReviewAction) => {
    setBusyId(task.id);
    try {
      const r = await post<ReviewResolveOut>(`/review/${task.id}/resolve`, { action });
      setItems((prev) => (prev ?? []).filter((t) => t.id !== task.id));
      setToast(r.note);
      window.dispatchEvent(new Event("zy:review-changed")); // 侧栏角标重拉
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // 已被别人处理 / 资产状态已变：这行数据过期了，从列表里拿掉并说明原因
        setItems((prev) => (prev ?? []).filter((t) => t.id !== task.id));
        window.dispatchEvent(new Event("zy:review-changed"));
        setToast(e.message);
      } else {
        setToast(e instanceof Error ? `处理失败：${e.message}` : "处理失败，请重试");
      }
    } finally {
      setBusyId(null);
    }
  };

  return (
    <>
      <div className="pagehead">
        <h1>复核队列</h1>
        <span className="sub">
          只列入「实际被使用 / 高风险 / 受代码或版本变化影响」的资产 —— DRAFT 躺着不动不会出现在这里
        </span>
      </div>
      {error ? (
        <div className="empty">加载失败：{error}</div>
      ) : items === null ? (
        <div className="empty">加载中…</div>
      ) : items.length === 0 ? (
        <div className="empty">
          队列已清空。资产在关联代码 / 版本变化或收到「可能过时」反馈时会自动进入这里。
        </div>
      ) : (
        items.map((t) => (
          <ReviewItem key={t.id} task={t} busy={busyId === t.id} onResolve={resolve} />
        ))
      )}
      <div className="footnote">
        轮值提示：本周复核轮值 —— {userName(CURRENT_USER)}。处理动作都会写入资产的验证与版本记录，作为审计轨迹。
      </div>
      {toastBox}
    </>
  );
}
