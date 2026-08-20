import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { post } from "../api/client";
import type { AssetCreate, AssetDetail } from "../types";

// 轻量沉淀页：左侧 AI 来源节选与自动带出的元数据，右侧只让用户确认三件事 → 发布 DRAFT。
// UI 对照 prototype 的 renderCapture() / publishDraft()。
// 硬规则 6：必填项 ≤ 3 —— 这里只有「问题」和「结论」两项必填，环境 chips 可全部取消。
// M1 还没有真的 AI 抽取，左栏与默认值沿用原型里的硬编码示例（M4 接 services/ai.py 后替换）。

type EnvChip = {
  key: string;
  label: string;
  mono: boolean;
};

// 环境 chips 同时承担「组装 POST 载荷」的职责：取消勾选即从载荷里去掉对应字段
const ENV_CHIPS: EnvChip[] = [
  { key: "fw", label: "vllm-ascend v0.10.0rc1", mono: true },
  { key: "cann", label: "CANN 8.2.RC1", mono: true },
  { key: "model", label: "Qwen3-30B-A3B", mono: false },
  { key: "hw", label: "Atlas 800I A2 · TP4", mono: false },
];

const DEFAULT_Q = "Qwen3-30B-A3B 在 800I A2 单机 TP4 上 TTFT 异常（>8s）";
const DEFAULT_C =
  "默认 max_num_batched_tokens=2048 使长 prompt 分 4 块串行 prefill，TTFT 被拉长到 8s+。调至 8192 后 TTFT 降至 1.9s。注意：预算变化会使 aclgraph 捕获桶失效，需重新 warmup；文档问答类负载建议直接按 KA-006 的 8192–16384 档配置。";

export default function Capture() {
  const navigate = useNavigate();
  const [q, setQ] = useState(DEFAULT_Q);
  const [c, setC] = useState(DEFAULT_C);
  const [on, setOn] = useState<string[]>(ENV_CHIPS.map((x) => x.key));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const isOn = (key: string) => on.includes(key);
  const toggle = (key: string) =>
    setOn((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));

  const canPublish = q.trim().length > 0 && c.trim().length > 0 && !busy;

  async function publishDraft() {
    if (!canPublish) return;
    setBusy(true);
    setErr("");

    const envLine = ENV_CHIPS.filter((x) => isOn(x.key))
      .map((x) => x.label)
      .join(" · ");
    const body: AssetCreate = {
      title: q.trim(),
      direction: "feature",
      body_md: `## 问题\n\n${q.trim()}\n\n## 环境\n\n${envLine || "—"}\n\n## 结论\n\n${c.trim()}`,
      models: isOn("model") ? ["Qwen3-30B-A3B"] : [],
      framework: isOn("fw") ? "vllm-ascend" : "",
      fw_version: isOn("fw") ? "v0.10.0rc1" : "",
      env_note: ENV_CHIPS.filter((x) => (x.key === "cann" || x.key === "hw") && isOn(x.key))
        .map((x) => x.label)
        .join(" · "),
      tags: ["ttft", "调度", "调参", "qwen3", "continuous-batching"],
      source: "ai_session",
      source_ref: "",
      code_refs: [
        {
          kind: "repo_path",
          repo: "vllm-project/vllm",
          path_or_key: "vllm/v1/core/sched/scheduler.py",
          ref_id: "",
          note: "token 预算调度",
          watch: true,
        },
      ],
    };

    try {
      const created = await post<AssetDetail>("/assets", body);
      navigate(`/asset/${created.id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <>
      <div className="pagehead">
        <h1>沉淀一条工作记录</h1>
        <span className="sub">
          AI 已从你的调试会话生成草稿 —— 你只需确认三件事，全程 &lt; 1 分钟
        </span>
      </div>
      <div className="callout info" style={{ maxWidth: 900 }}>
        <span className="ct">低负担约定</span>
        发布为 DRAFT
        不需要评审、不需要承诺维护、不强制补齐字段。只有当它被别人搜到并用上时，系统才会请你（或使用者）补充版本与验证信息。
      </div>

      <div className="cap-grid">
        <div>
          <div className="section-t">来源 · AI 自动提取自</div>
          <div className="srcbox">
            <div style={{ color: "var(--ink3)" }}>
              // Claude Code 会话 #8f3a · 2026-08-20 14:32 · 节选
            </div>
            <div>
              <span className="who">wanglei&gt;</span> Qwen3-30B-A3B 在 800I A2 单机 TP4
              上首 token 要 8 秒多，帮我看看
            </div>
            <div>
              <span className="who">agent&gt;</span> 日志显示长 prompt 被切成 4 个 chunk
              串行 prefill，max_num_batched_tokens=2048（默认值）…
            </div>
            <div>
              <span className="who">wanglei&gt;</span> 调到 8192 再试
            </div>
            <div>
              <span className="who">agent&gt;</span> TTFT 1.9s。注意 aclgraph
              捕获桶按预算变化需要重新 warmup…
            </div>
            <div style={{ color: "var(--ink3)" }}>
              // 关联 diff：deploy/qwen3-30b.yaml (+2 −1)
            </div>
          </div>

          <div className="section-t">AI 同时自动带出</div>
          <div className="card" style={{ fontSize: 12, lineHeight: 2 }}>
            分类：<span className="tier">推理特性</span> → 调度 / TTFT
            <br />
            关联代码：
            <span className="mono" style={{ fontSize: 11 }}>
              vllm/v1/core/sched/scheduler.py
            </span>
            <br />
            相似资产：<span className="mono">KA-006</span>（不重复，互补）
            <br />
            命中缺口：无
          </div>
        </div>

        <div>
          <div className="section-t">只确认这三项</div>
          <div className="card">
            <div className="formfield">
              <label htmlFor="cap-q">① 问题（这条记录解决了什么）</label>
              <input
                id="cap-q"
                type="text"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            </div>

            <div className="formfield">
              <label>
                ② 环境 <span className="opt">（AI 已带出，可修改）</span>
              </label>
              <div className="chips" style={{ marginTop: 4 }}>
                {ENV_CHIPS.map((chip) => (
                  <button
                    key={chip.key}
                    type="button"
                    aria-pressed={isOn(chip.key)}
                    className={`chip${isOn(chip.key) ? " on" : ""}${chip.mono ? " mono" : ""}`}
                    onClick={() => toggle(chip.key)}
                  >
                    {chip.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="formfield">
              <label htmlFor="cap-c">③ 结论（AI 草拟，请核对）</label>
              <textarea id="cap-c" value={c} onChange={(e) => setC(e.target.value)} />
            </div>

            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <button
                className="btn pri"
                onClick={() => void publishDraft()}
                disabled={!canPublish}
              >
                {busy ? "发布中…" : "发布为 DRAFT"}
              </button>
              <span style={{ fontSize: 11.5, color: "var(--ink3)" }}>
                发布后立即可被搜索，标注「尚未验证」
              </span>
            </div>

            {err && (
              <div className="callout bad" style={{ marginBottom: 0 }}>
                <span className="ct">发布失败</span>
                {err}
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
