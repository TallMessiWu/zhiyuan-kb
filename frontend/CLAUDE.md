# frontend — React + Vite + TS

## 结构

```
src/
  main.tsx        入口（BrowserRouter）
  App.tsx         左侧导航布局 + 7 页面路由（对照原型的侧栏）
  types.ts        与 backend/app/schemas.py 对齐的 TS 类型 — 后端改 schema 必须同步这里
  api/client.ts   fetch 封装（/api/v1，X-User 头）
  lib/            display（状态 pill/chip/时间）· highlight（命中词 <mark>）· markdown · users · toast
  pages/          Home Search AssetDetail Capture Review（已实现）· Ask Dashboard（占位）
```

## UI 基准

**一切页面布局、文案、状态标注、交互以 `../prototype/kms-prototype.html` 为准**（浏览器直接打开对照）。
实现顺序跟随后端里程碑：M1 详情页+沉淀页 → M2 首页+搜索结果页 → M3 反馈条+记缺口+认领
→ **M4 复核队列（已完成）** → M5 问答+看板。

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
- 后端还没有的按钮一律 `disabled` + `title="M4/M5 …上线后可用"`，不要渲染成可点但点了没反应。
  M4 之后，这条只剩问答页、看板两个占位页面适用。

M3 补充：

- **toast 只有一份**：`lib/toast.tsx` 的 `useToast()`（详情页/搜索页/首页共用）。
  再抄一份实现，停留时长和堆叠规则会慢慢漂开。
- **详情页第三键要问一句**：「没有找到答案」记的是知识缺口，与当前资产无关，而详情页没有
  搜索词上下文 —— 所以它展开一个输入问「你想找的是什么」，那句话才是缺口内容。
  搜索页则不问：查询词现成的，连同 `search_event_id` 一起报，真正做到一次点击。
- **「内容可能过时」不展开任何输入**：说明由服务端从使用者与原状态组装。资产已是 REVIEW_DUE
  时该键 `disabled`（title 说明原因），不靠点了才报错来告知。
- **认领就地更新那一行**，不重拉 `/home`：认领不产出资产，整页刷新只会让人以为发生了别的事。

M4 补充：

- **四选一的 toast 文案由后端 `resolve` 的 `note` 给**，前端不自己组装 —— 三键反馈就是这个
  分工，两处各写一份文案迟早对不上。处理成功（或 409 发现已被别人处理）都把那行就地移除。
- **「接受 AI 更新草稿」在 `ai_draft` 为空时 disabled** + title 说明（网关降级没生成），
  不靠点了 409 才告知 —— 与详情页 REVIEW_DUE 时禁用「内容可能过时」同一条规则。
- **侧栏复核角标**：App 挂载时拉一次 `/home` 的 `stats.review_due`；Review 页处理完任务
  广播 `zy:review-changed`，App 监听重拉。不为一个数字引全局状态库。
- diff 的两种形态都要认：seed/原型是 `add:`/`del:` 前缀行（渲染 diffline 红绿），
  webhook 建的是 compare/PR 链接（渲染成外链）。

## 命令

```bash
npm install
npm run dev      # http://localhost:5173，/api 已代理到 :8000
npm run build
```
