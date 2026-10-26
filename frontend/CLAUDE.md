# frontend — React + Vite + TS

## 结构

```
src/
  main.tsx        入口（BrowserRouter）
  App.tsx         左侧导航布局 + 7 页面路由（对照原型的侧栏）
  types.ts        与 backend/app/schemas.py 对齐的 TS 类型 — 后端改 schema 必须同步这里
  api/client.ts   fetch 封装（/api/v1，X-User 头）
  lib/            display（状态 pill/chip/时间）· highlight（命中词 <mark>）· markdown · users · toast
  pages/          Home Search AssetDetail Capture Review Ask Dashboard（七页全部已实现）
```

## UI 基准

**一切页面布局、文案、状态标注、交互以 `../prototype/kms-prototype.html` 为准**（浏览器直接打开对照）。
实现顺序跟随后端里程碑：M1 详情页+沉淀页 → M2 首页+搜索结果页 → M3 反馈条+记缺口+认领
→ M4 复核队列 → **M5 问答+看板（已完成 —— 七页面全部落地，MVP 前端交付）**。

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
- 首页数字条第五格「有效复用率」：M5 起接真数（`stats.reuse_rate`，与看板同口径）；
  `pct=null`（分母为 0）显示「—」——「没人有需求」和「有需求没人复用」是两码事，
  不许显示成 0%（硬规则 5）。
- ~~后端还没有的按钮一律 `disabled` 占位~~：**该规则 M5 退役** —— 已无占位页面。
  留给后人的形式是：新增页面时若后端未就绪，恢复这条规则。

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

M5 补充：

- **问答条目是五态的**：进行中（「正在检索并生成…」，生成要 10–40s）/ 正常回答 /
  not_found（固定话术 + 一键记缺口，带 `search_event_id`）/ 冲突并列 / 「问答暂不可用」
  （503 `AI_UNAVAILABLE` 渲染成条目而不是报错弹窗 —— 它是明确语义：检索与浏览不受影响）。
  提问期间整页 `busy`（按钮「生成中…」），不允许并发提问。
- **预置问题是真链路**：`PRESET_QUESTIONS` 只是把问句填进真 `/ask`，不携带任何写死的答案
  （原型的 QA_PRESETS 带答案是内存演示）。
- **风险块由后端组装**：`risks[].text`/`ai_impact_summary` 原样渲染，前端只补「前往复核
  队列 →」链接 —— 与三键/四选一的 toast 同一分工，文案不写两份。
- **看板 den=0 显示「—」**（`BigPct`），估算指标（重复工时）文案里必须保留「估算值，
  仅看趋势」——响应里 `rework_hours_estimated` 就是干这个的。趋势条全 0 时 max 取 1 防除 0。
- **认领跳沉淀页带 `location.state`**（gapId/gapCode/question/draftOut）：底稿 503 时
  `draftOut=null` 照样跳（认领已生效，作者手写），沉淀页据此切换文案。沉淀页无 state
  时保持原型演示数据（真会话接入是 V1.1）。发布 payload 带 `gap_id`，成功即缺口闭环。
- `lib/markdown.tsx` 的 `<Markdown>` 是详情正文和问答共用入口（V1.1 替换原来的六语法
  正则解析器）：React Markdown + GFM + remark-breaks 保留单换行，支持表格/嵌套列表。
  `$...$` 为行内公式，单独成行的 `$$` 包围公式块，`math` 围栏同样按块级公式渲染；
  KaTeX CSS/字体随构建产物发布，不依赖 CDN。普通代码块/行内代码中的公式语法不解释。
- 目录扩展：独立成段的 `[toc]` / `[TOC]`（大小写不敏感，与普通段落之间留空行）会在原位置
  生成全篇 h1–h6 的分级链接，兼容 Setext 标题；无标题时隐藏占位符，无标记不自动加目录。
  `rehype-toc.ts` 在 KaTeX 展开前提取安全纯文字，代码/链接/转义/原始 HTML 中的标记不解释。
  锚点用 Markdown 实例 ID + 标题序号，重复标题及同页多条回答不串跳；仅供当前页面内导航，
  不承诺作为跨刷新永久链接。安全策略不变。
- `mermaid` 围栏由 `lib/MermaidDiagram.tsx` 按需加载 `mermaid-renderer.ts` 渲染；多图串行
  初始化/绘制，深浅主题变化重绘，卸载或切换正文时忽略迟到结果。语法或加载失败保留源码。
  **安全边界不得放宽**：不启用原始 HTML 解析，不覆盖 Markdown 默认 URL 过滤，KaTeX
  `trust=false`；Mermaid `strict` + 禁止正文覆盖配置 + 关闭 HTML 标签/点击绑定，输出再经
  DOMPurify SVG 清洗。不要把 AI 输出视为可信 HTML。

## 命令

```bash
npm install
npm run dev      # http://localhost:5173，/api 已代理到 :8000
npm run build
npm test         # Vitest + jsdom：Markdown/公式、图表并发/失败/安全、真实 Mermaid 引擎
```

图表集成测试仅模拟 jsdom 缺失的 SVG 测量 API，不代表真实浏览器的排版验收。
手动验收：详情页与问答页分别放入 Mermaid 流程图/时序图、行内公式、分式公式块、普通
代码块和错误图表；检查深浅主题及窄屏滚动，确认错误块不影响后续正文与反馈操作。
目录验收：加入独立成段的 `[toc]`、多级/重复/长中文标题，检查链接定位、键盘访问、窄屏换行；
问答页放两条含同名标题的回答，确认各自目录只跳本条正文。本轮 26 项测试和构建通过，
目录浏览器视觉/真实滚动验收待完成（当前环境未配置浏览器）。
