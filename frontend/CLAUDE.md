# frontend — React + Vite + TS

## 结构

```
src/
  main.tsx        入口（BrowserRouter）
  App.tsx         左侧导航布局 + 7 页面路由（对照原型的侧栏）
  types.ts        与 backend/app/schemas.py 对齐的 TS 类型 — 后端改 schema 必须同步这里
  api/client.ts   fetch 封装（/api/v1，X-User 头）
  lib/            display（状态 pill/chip/时间）· highlight（命中词 <mark>）· markdown · users
  pages/          Home Search AssetDetail Capture（已实现）· Ask Review Dashboard（占位）
```

## UI 基准

**一切页面布局、文案、状态标注、交互以 `../prototype/kms-prototype.html` 为准**（浏览器直接打开对照）。
实现顺序跟随后端里程碑：M1 详情页+沉淀页 → **M2 首页+搜索结果页（已完成）** → M3 反馈条 →
M4 复核队列 → M5 问答+看板。

关键约定（来自设计文档 §5/§6/§8）：
- 状态 pill 固定五色语义：VERIFIED 绿 / DRAFT 石板灰(标「尚未验证」) / REVIEW-DUE 琥珀(标「可能过时」) / STALE 红 / ARCHIVED 中灰
- 搜索结果每条要渲染 score.parts（「为什么排在这里」可展开）
- 详情页底部常驻三键反馈条；「有用」仅一个可选的任务说明输入，不许加字段
- 问答页引用块必须含 状态/适用版本/更新时间；not_found=true 渲染固定话术
- 样式直接从原型抄 CSS 变量（含深浅双主题 token），不引 UI 组件库

M2 补充：

- 搜索页的筛选状态**只存在 URL 里**（`useSearchParams`），刷新/分享链接都能复现同一份结果；
  别再往组件里放一份筛选 state，两处会漂。
- 高亮词由后端给（`SearchResponse.terms`，已滤掉单字），前端只负责套 `<mark>`；
  用 `lib/highlight.tsx` 的组件而不是 `dangerouslySetInnerHTML`。
- 结果行的模型/框架/版本来自 `AssetBrief`（后端批量查好），不要为每条结果再拉一次详情。
- 首页数字条第五格「有效复用率」显示「—」：口径是看板指标，M5 才有，
  硬规则 5 禁止拿近似值冒充（详见根 CLAUDE.md）。
- M3 才有后端的按钮（记录缺口/认领缺口）一律 `disabled` + `title="M3 …上线后可用"`，
  与 M1 详情页三键的做法保持一致 —— 不要渲染成可点但点了没反应。

## 命令

```bash
npm install
npm run dev      # http://localhost:5173，/api 已代理到 :8000
npm run build
```
