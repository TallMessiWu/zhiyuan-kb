# API 约定（MVP）

Base：`/api/v1`。鉴权 MVP 用请求头 `X-User`（内网单团队），V1.1 换 SSO。
所有时间 ISO8601；分页 `?limit=&offset=`。

## 资产

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/assets` | 发布 DRAFT（沉淀页）。body: title, direction, body_md(问题/环境/结论), models[], framework, fw_version, tags[], source, code_refs[] |
| GET | `/assets/{id}` | 详情：资产 + 当前版本 + 验证/复用记录 + 代码引用 + 版本历史 |
| GET | `/assets/{id}/transitions` | 状态流转审计流水 |
| POST | `/assets/{id}/versions` | 新版本（人工修订） |

## 搜索与问答

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/search` | q, direction?, model?, framework?, status?, hist?(bool), limit=20, offset=0。返回 `{items[{asset, score:{total,parts[{label,value}]}}], total, terms[], recall{keyword,vector,keyword_hits,vector_hits}, search_event_id, hist}`，同时落 SearchEvent（零结果也落 —— 它是需求事件） |
| POST | `/ask` | {question}。返回 {answer_md, citations[{asset_id, fragment, status, fw_version, updated_at}], risks[], conflict?, not_found:bool} |

`/search` 细则（实现见 docs/design.md §5）：

- **q 为空** = 浏览模式：不召回，按状态 + 新鲜度 + 复用把候选铺开重排。
- **硬过滤**：direction / status / hist，以及**显式**传入的 framework / model（「通用」视作匹配任何筛选）。
- **软信号**：从 q 里推断出的框架（含 sglang / ascend / vllm 字样）只加减分，出现在 score.parts 里。
- **terms**：分词后的查询词（已滤掉单字），前端据此做 `<mark>` 高亮。
- **recall**：这次实际走的召回后端。`keyword` ∈ {pg_tsvector, portable}，
  `vector` ∈ {pgvector, python, off, unavailable}。降级要看得见，不能静默。

## 反馈（三键，均免表单）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/feedback/useful` | {asset_id, task_note?, search_event_id?} → 建 ReuseEvent(success)；若资产 DRAFT 且 user≠author → 自动升 VERIFIED |
| POST | `/feedback/stale` | {asset_id, note?} → 置 REVIEW_DUE + 建 ReviewTask(user_feedback) |
| POST | `/feedback/not-found` | {query, search_event_id?} → 建/累计 KnowledgeGap |

## 复核队列

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/review` | open 任务，按 priority 降序；含 ai_impact_summary、diff_ref、ai_draft |
| POST | `/review/{task_id}/resolve` | {action: confirm\|accept_draft\|stale\|archive, note?, replaced_by?} → 走状态机 |

## 缺口与看板

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/gaps` · POST `/gaps/{id}/claim` | 列表（open 优先、按 hit_count 降序，resolved 不返回）/ 认领（认领后 AI 生成 DRAFT 底稿） |
| GET | `/home` | 首页一屏：`{stats{total,verified,review_due,open_gaps}, recent_validated[{asset,validator_id,note,at}], hot[asset], gaps[]}`。与 `/dashboard` 不是一回事 —— 那边是带口径的 7 指标，这边只是首屏展示数据 |
| GET | `/dashboard` | 7 指标：reuse_rate{num,den,trend[]}, search_ok, not_found_30d, review_backlog, verified_count, rework_hours_trend[], coverage[direction][status] |

## Webhook

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/hooks/git` | GitHub/GitLab push·PR 事件；匹配 CodeReference(watch=true)，24h 去抖后建 ReviewTask |

错误统一 `{error:{code, message}}`；状态机非法流转返回 409 `INVALID_TRANSITION`。
