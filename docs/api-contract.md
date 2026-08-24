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
| POST | `/feedback/stale` | {asset_id, note?} → UserFeedback(maybe_stale) + ReviewTask(user_feedback) + 置 REVIEW_DUE。返回 `{feedback_id, asset_id, status, review_task_id, merged, note}` |
| POST | `/feedback/not-found` | {query, search_event_id?} → UserFeedback(not_found) + 建/累计 KnowledgeGap。返回 `{feedback_id, gap, created}` |

反馈细则：

- `search_event_id` 传了就必须存在（否则 422）—— 它是看板把「需求事件」和「复用/缺口」对上的唯一线索。
- `stale`：24h 去抖窗口内同资产的重复反馈并进同一条 ReviewTask（`merged=true`，不新建、不重复流转）；
  资产已是 STALE/ARCHIVED 时返回 409 `ASSET_NOT_ACTIVE` —— 死状态不进复核队列。
- `not-found`：同一个需求累计到同一条缺口（`created=false`，hit_count+1、reporters 去重并集），
  判据见 `backend/app/services/gaps.py`；`query` 留空按「（无关键词浏览）」记。
  已 resolved 的缺口不吸收新反馈 —— 那说明是搜不到而不是缺知识，另开一条。

## 复核队列

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/review` | open 任务按 priority 降序。返回 `{items[{id, asset, trigger, trigger_detail, diff_ref, ai_impact_summary, ai_draft_version_id, ai_draft, priority, priority_label, usage_30d, created_at}], total}` |
| POST | `/review/{task_id}/resolve` | {action: confirm\|accept_draft\|stale\|archive, note?, replaced_by?} → 走状态机。返回 `{task_id, action, asset_id, status, current_version_id, note}`（note 给前端 toast） |

复核细则（实现见 `backend/app/services/review_queue.py`）：

- **按需治理过滤**（design.md §4）：只列「近 90 天有使用 / tier=core / 高风险标签」且资产仍是
  REVIEW_DUE 的任务，其余只降权不打扰。「有使用」以 ReuseEvent ∪ UserFeedback 近似。
- `priority = max(近30天复用, 1) × 风险系数(core=3 / 高风险标签=2 / 其他=1)`；
  `priority_label`：≥8 高、≥2 中、否则低。
- `ai_draft` 是草稿版本正文；空串 = 生成时网关不可用（前端禁用「接受草稿」，后端也会拦）。
- `confirm` 恢复**进入 REVIEW_DUE 前的状态**：VERIFIED 进来的回 VERIFIED，DRAFT 进来的只回
  DRAFT —— 复核回答「变更是否影响了这份知识」，不判对错，确认不构成验证证据（硬规则 3）。
- `accept_draft`：current_version 切到草稿版本 + 回 DRAFT + 同事务刷新检索索引；
  任务没有草稿时 409 `NO_AI_DRAFT`。
- `archive`：`replaced_by` 传了必须存在（否则 422），回链写进流转 note。
- 任务已处理 409 `TASK_ALREADY_DONE`；任务不存在 404；资产状态已变（他人已处理）409
  `INVALID_TRANSITION`。处理成功会顺带关闭同资产其它 open 任务（跨去抖窗口的重复触发）。

## 缺口与看板

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/gaps` · POST `/gaps/{id}/claim` | 列表（open 优先、按 hit_count 降序，resolved 不返回）/ 认领 → status=claimed + claimed_by，返回 GapOut。同一人重复认领幂等；已被他人认领 409 `GAP_ALREADY_CLAIMED`，已解决 409 `GAP_RESOLVED`。认领只是登记「我来写」，不产出资产 —— AI 底稿等 `ai.draft_from_session`（M5） |
| GET | `/home` | 首页一屏：`{stats{total,verified,review_due,open_gaps}, recent_validated[{asset,validator_id,note,at}], hot[asset], gaps[]}`。与 `/dashboard` 不是一回事 —— 那边是带口径的 7 指标，这边只是首屏展示数据 |
| GET | `/dashboard` | 7 指标：reuse_rate{num,den,trend[]}, search_ok, not_found_30d, review_backlog, verified_count, rework_hours_trend[], coverage[direction][status] |

## Webhook

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/hooks/git` | GitHub/GitLab push · tag push · PR(MR) merged；匹配 CodeReference(watch=true)，24h 去抖后建 ReviewTask + AI 摘要/草稿（可降级）。返回 `{handled, reason, event, repo, matched_refs, tasks[{review_task_id, asset_id, created}]}` |

Webhook 细则（实现见 `backend/app/api/hooks.py`）：

- **签名**：GitHub `X-Hub-Signature-256`（HMAC-SHA256，密钥 `ZY_WEBHOOK_SECRET`）；
  GitLab `X-Gitlab-Token`（明文比对）。缺失/不符一律 401。
- **事件面**：push（`code_change`）、tag push（`version_change`，按 repo 批量触发 —— 基线升级
  语义）、PR/MR merged（`code_change`）。其余事件返回 200 `handled=false`（非 2xx 会被平台重试）。
- **匹配**：repo 一致（引用的 repo 为空则不限，但 tag 批量要求非空）+ repo_path 前缀命中变更文件
  / config_key 在提交说明或文件路径中出现 / issue·pr 按编号出现在文本里。PR 事件拿不到文件列表，
  repo_path 退化为在标题/正文里找路径字符串。
- STALE/ARCHIVED 资产命中也不建任务（死状态不进队列）；命中的引用回填 `last_seen_sha`。
- `created=false` 表示并进了去抖窗口内的已有任务（复用 M3 `open_task` 契约）；合并时若旧任务
  缺 AI 摘要/草稿会补生成，已有的不重复花网关。

错误统一 `{error:{code, message}}`；状态机非法流转返回 409 `INVALID_TRANSITION`。
