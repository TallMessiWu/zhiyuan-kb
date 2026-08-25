# 知源 · 设计文档（仓内权威版）

> 完整排版版（含架构图/状态机图/时序图）：https://claude.ai/code/artifact/45f5139a-c673-437c-8526-1d5701fcc845
> 本文件是**实现依据**：数据模型、状态机、评分公式、指标口径与此处不一致时，以此处为准并同步更新本文件。

## 1. 目标与边界

一句话：让推理团队的每一次调试、适配与踩坑，以 ≤1 分钟的成本沉淀为带版本与可信度标签的知识，并在下一个人第一次搜索时被找到、被判断、被复用。

MVP 范围内：三方向知识（模型结构/执行链路/推理特性）的沉淀、混合检索、AI 问答、五态治理、代码联动复核、看板。
明确不做：文档协作编辑器、细粒度权限、多租户、自动判定知识正确性、老 Wiki 一次性迁移、IM/IDE 入口（V2）。

关键假设：A1 团队 5–20 人；A2 Git 平台可 webhook；A3 有内部 LLM 网关与 embedding；
A4 **AI 不能判断知识正确性**（硬约束）；A5 成员只接受一次点击级反馈；A6 老 Wiki 按需治理；A7 需求事件可由搜索/问答/缺口近似统计。

## 2. 三级知识 × 五态

**层级（决定字段要求与维护义务）**
- 工作记录 note：AI 生成，作者只确认 问题/环境/结论；无评审、无维护义务
- 共享知识 shared：被他人使用时补版本、来源、验证信息
- 核心资产 core：高频（复用≥5）/高风险/影响交付才升级；含原理、版本、代码入口、限制、验证步骤、案例

**状态（决定可信度展示与搜索权重）** — 是可信度标签，不是审批关卡。

## 3. 数据模型（11 实体）

```
KnowledgeAsset   id title direction(model|chain|feature) tier(note|shared|core) status
                 summary tags[] author_id current_version_id source source_ref reuse_count timestamps
AssetVersion     id asset_id seq body_md change_note created_by created_from(author|ai_draft|review) created_at
StatusTransition id asset_id from_status to_status trigger evidence_type evidence_id actor note at   # append-only
Framework        id name repo_url；关联表带 version_min/version_max/verified_on
Model            id name family arch_notes hf_ref（保留值「通用」）
CodeReference    id asset_id kind(repo_path|config_key|issue|pr) repo path_or_key ref_id watch last_seen_sha
ValidationRecord id asset_id version_id validator_id kind(reuse_success|manual_review|review_confirm)
                 result(pass|fail|stale_confirm) env_snapshot(json) note at
ReuseEvent       id asset_id version_id user_id task_note outcome(success|partial|failed)
                 search_event_id fw_version_at_use at
SearchEvent      id user_id query filters(json) mode(search|qa) result_ids[] clicked_ids[] session_id at
UserFeedback     id user_id asset_id? search_event_id? kind(useful|maybe_stale|not_found) note? at
KnowledgeGap     id question hit_count first_at last_at reporters[] status(open|claimed|resolved)
                 claimed_by resolved_asset_id
ReviewTask       id asset_id trigger(code_change|version_change|user_feedback) trigger_detail diff_ref
                 ai_impact_summary ai_draft_version_id? priority state(open|done) handled_by action at
```

审计规则：
- 当前态冗余在 `KnowledgeAsset.status`；历史全部在 `StatusTransition`（append-only），同事务写入，禁止绕过。
- 每次流转必须带证据：`evidence_type + evidence_id` 指向 ReuseEvent / UserFeedback / ReviewTask / ValidationRecord。
- 正文审计由 AssetVersion 不可变快照承担；AI 产出版本必须 `created_from=ai_draft`。
- 事件三表（Reuse/Search/Feedback）只增不改；看板指标可由事件表重放。

## 4. 五态状态机

| 流转 | 触发 | 执行方 | 证据 |
|---|---|---|---|
| → DRAFT | 沉淀发布 / 缺口认领生成 / 复核接受 AI 草稿 | 自动 | AssetVersion |
| DRAFT → VERIFIED | 非作者「有用，完成任务」(outcome=success) 或非作者人工验证 | 自动 | ReuseEvent/ValidationRecord，**validator≠author 强校验** |
| VERIFIED/DRAFT → REVIEW_DUE | watch 代码路径/配置变更；框架版本基线变化；「内容可能过时」反馈 | 自动 | ReviewTask |
| REVIEW_DUE → VERIFIED | 复核选「仍然有效」（限 VERIFIED 进入 REVIEW_DUE 的资产） | 人工 | ValidationRecord(review_confirm) |
| REVIEW_DUE → DRAFT | 复核选「接受 AI 更新草稿」→ 新版本，**绝不直达 VERIFIED**；或从 DRAFT 进入 REVIEW_DUE 的资产被确认「未受影响」 | 人工 | AssetVersion(ai_draft) / ValidationRecord(review_confirm) |
| REVIEW_DUE → STALE | 复核确认失效（保留失效说明与替代指引） | 人工 | ValidationRecord(stale_confirm) |
| 任意 → ARCHIVED | 被替代/重复/不再需要（填替代资产回链） | 人工 | StatusTransition.note |

「仍然有效」的落地口径（M4）：confirm 恢复**进入 REVIEW_DUE 前的状态** —— 复核回答的是
「变更是否影响了这份知识」，不是「知识对不对」；从未被验证过的 DRAFT 不能借一次复核确认
绕过非作者校验变成 VERIFIED。

按需治理：进入人工队列需 `status=REVIEW_DUE` **且**（近 90 天有复用或点击 / tier=core / 高风险标签）。
其余 REVIEW_DUE 只降权不打扰。DRAFT 可永久存在。老 Wiki 被命中≥3 次/被引用/被变更关联才生成盘查任务。
M4 落地口径：「有复用或点击」以 ReuseEvent ∪ UserFeedback 近似 —— 点击流水前端尚未上报，
而反馈本身就是最强的「有人在用」信号；高风险标签集合由 `ZY_HIGH_RISK_TAGS` 配置（默认只有「高风险」）。

## 5. 检索与排序

双路召回（BM25 + bge-m3 向量）→ RRF 融合 → 业务重排：

```
final = rel + trust + fit + fresh + proof
rel   = RRF 归一化 0–30（字段权重 title×4 / tags×3 / summary×2 / body×1）
trust = VERIFIED:+14 · DRAFT:0 · REVIEW_DUE:−10（STALE/ARCHIVED 不参与正常检索）
fit   = 框架匹配 ±6~8 + 模型匹配 ±6~8 + 版本区间命中 +4
fresh = <30d:+5 · <90d:+3 · <180d:+1
proof = min(非作者复用次数 × 0.4, 8)
```

展示规则：
- 每条结果返回分项得分（排序可解释，前端有「为什么排在这里」）
- DRAFT 标「尚未验证」；REVIEW_DUE 标「可能过时」
- STALE/ARCHIVED 仅「查看历史资产」模式可检索
- 筛选不匹配默认降权而非硬过滤（显式筛选除外）
  —— 落地口径：**用户在筛选器里点的** framework/model 是显式筛选，召回层直接过滤；
  **从查询词里推断出来的**框架只加减分（±6/−8）。「通用」对任何筛选都算匹配。

M2 落地细节（实现在 `backend/app/services/{text,indexing,recall,search}.py`）：

- **中文分词用 jieba 预分词**，不是 zhparser：后者要编译 PG 扩展，本项目的 Windows 开发库装不了，
  而 jieba 是纯 Python，PG 与 sqlite 两条路能共用同一份分词结果，测试不必依赖 PG。
  版本号（`v0.10.0rc1`）和带连接符的标识符（`max_num_batched_tokens`）额外整体保留一份，
  它们是本领域最该精确命中的东西。
- **字段权重**在 PG 上由 `asset_search_doc.tsv` 生成列的 `setweight(A/B/C/D)` 表达。
- **RRF 的 k 取 10**（不是论文里的 60）：两路各召回 50 条，k=60 会把 rel 压成一条平线
  （第 1 名 30.0、第 10 名 29.5），相关度实际失声、排序全由 trust 说了算；
  k=10 时 1 名 30 / 10 名 15 / 50 名 5，梯度才有意义。归一化按**非空**路数算，
  只有一路可用时该路第一名照样拿满 30 —— 否则一降级 rel 就被系统性压低、排序悄悄变形。
- **两路都可降级**：没有 pgvector 就用 Python 余弦，网关不可达就整路跳过；
  实际用了哪条路随响应返回（`recall` 字段），降级必须看得见。

## 6. AI 问答硬性规则

1. 只基于检索到的资产段落作答；引用块含 资产链接/命中段落/状态/适用版本/更新时间
2. 无 VERIFIED/DRAFT 命中或分数低于阈值 → 固定回答「没有找到经过验证的知识」+ 一键记录缺口；禁止用模型通用知识补位
3. STALE/ARCHIVED 不入 RAG 上下文；历史模式引用需挂「已失效，仅供追溯」横幅
4. 多资产结论互斥 → 并列展示「说法 A / 说法 B」与各自证据，不选边
5. 引用 REVIEW_DUE 必须附「可能过时」提示并链接其 AI 变化摘要

M5 落地注记（实现 `backend/app/services/ask.py`）：

- 规则 2 的「分数阈值」打在 **rel 分项**（关键词+语义，`ZY_ASK_MIN_REL` 默认 5.0）上，
  且候选按 rel（不按总分）选前 `ZY_ASK_MAX_CONTEXT` 条 —— trust/fresh 是搜索列表的排序
  信号：真实验收踩过 REVIEW_DUE 资产 rel 全场最高、却被弱相关 VERIFIED 的总分挤出上下文，
  那样连规则 5 都无从谈起。
- 「禁止通用知识补位」不能靠提示词自觉：无命中根本**不调 LLM**；LLM 自报 insufficient
  或给不出一条有效引用，同样按无据返回固定话术。引用片段服务端逐字校验，编造的换成
  按词重合选出的真实段落。
- 资产状态是可信度分层，不是能否引用的开关：DRAFT / 标注（待验证）的内容照常作答并
  引用，可信度由引用块状态标注传达（提示词里写明，否则模型对底稿类资产会过度拒答）。
- 规则 5 的风险提示由**服务端**从该资产 open 复核任务取 M4 的 ai_impact_summary 组装，
  不许 LLM 代笔。
- 问答**没有规则式兜底**（摘要可以截正文凑合，答案不能编）：网关不可用/输出不可解析
  返回 503 `AI_UNAVAILABLE`（明确的「问答暂不可用」语义）。SearchEvent(mode=qa) 在生成
  **之前**落库 —— 需求在提问那一刻已发生，AI 失败不抹掉分母。
- 规则 3 后半句（历史模式引用挂「已失效，仅供追溯」）：问答页 MVP 无历史模式，记 V1.1。
- 冲突（规则 4）由 LLM 判互斥、服务端只校验引用索引有效性。注意 seed 里的冲突对
  （KA-016 vs KA-008）后者是 STALE，被规则 3 隔离后模型对「Ray 还是 MP」会诚实拒答 ——
  这是规则叠加的正确行为；原型的预置冲突演示恰恰依赖 STALE 资产入镜，真实系统不复现。

## 7. 自动更新机制

webhook（push/PR merge/tag/基线升级）→ 匹配 CodeReference(watch=true)（**24h 聚合去抖**）
→ 资产置 REVIEW_DUE + 建 ReviewTask → AI 生成「可能受影响内容」摘要 + 更新草稿（只到草稿为止）
→ 轮值四选一（仍有效/接受草稿/失效/归档）。
纯格式化/注释 diff 由 AI 预判抑制，抑制记录可抽查。
框架版本基线是全局订阅：基线升级一次批量触发相关资产。

M4 落地注记：webhook payload 只有变更文件清单与提交说明，没有 diff 正文 —— AI 摘要/草稿
基于这两样生成（够指出「哪些节可能受影响」）；「纯格式化 diff 预判抑制」需要真 diff，
等接入 Git API 拉取后再做。tag push 按 repo 批量触发（即「基线升级」的 MVP 形态），
AI 网关不可用时任务照建，只是没有摘要与草稿（降级不阻塞复核流程）。

## 8. 页面（7 个，UI 以 prototype/kms-prototype.html 为准）

首页（搜索+筛选+最近验证+热门+缺口认领）/ 搜索结果页（状态标注+高亮摘要+分项得分+历史开关+记缺口）/
AI 问答页（引用+风险提示+冲突并列）/ 详情页（正文+右栏环境/代码/验证/复用/版本 + 底部三键反馈条）/
沉淀页（AI 来源+三项确认+发布 DRAFT）/ 复核队列（触发+AI 摘要+diff+草稿折叠+四选一）/
看板（复用率含公式、搜索成功率、缺口、积压、VERIFIED 存量、重复工时趋势、方向×状态覆盖矩阵）

## 9. 指标口径（防作弊）

```
有效复用率 = 非作者成功复用事件数 ÷ 适用知识需求事件数 × 100%
分子：ReuseEvent(outcome=success, user≠author)
分母：搜索/问答会话去重（同人同主题 30min 合并）+ 记录的缺口 − 导航式查询
禁止用点击量/PV/问答次数替代分子。
```

M5 冻结的实现口径（`backend/app/services/metrics.py`，7 指标全部由事件表实时聚合）：

- **会话去重**：SearchEvent（search 与 qa 合并去重 —— 同人同主题先搜后问是一次需求）按
  （user_id, jieba 词集合归一化主题）在 `ZY_DASHBOARD_SESSION_MINUTES`(30) 分钟滑动窗口
  内合并。「导航式查询」MVP 无法识别、不扣减。
- **分母防双算**：分母 = 去重会话数 + **不带 search_event_id 的** not_found 反馈数。
  带 event_id 的缺口反馈，其需求已随那次搜索会话计入，再加一次就是双算。
- **搜索成功率**：去重后的搜索会话（含 search 事件的会话；纯 qa 会话不进这项）里
  「有结果且未反馈没找到答案」的占比。MVP 没有点击上报，以「有结果」代替原型口径的
  「有结果点击」。
- **重复探索工时（估算）**：同一自然月内同主题（跨用户）的第 2+ 次需求会话数 ×
  `ZY_REWORK_HOURS_PER_MISS`(3.5h)。浏览会话（空查询）不构成主题。**估算式指标必须在
  响应里自报**（`rework_hours_estimated=true`），不冒充实测。
- **den=0 时 pct=null**（前端显示「—」）：「没人有需求」和「有需求没人复用」是两码事，
  不许显示成 0%。首页第五格与看板取数同一函数（`metrics.reuse_rate`），不许另算。
- 趋势条：近 5 个自然月（含当月），月界按 UTC。coverage 与 reuse_by_direction 为
  全时段快照（direction × status 资产计数 / 非作者成功复用事件计数）。

MVP 目标（8 周）：复用率≥50%；搜索成功率≥70%；沉淀中位耗时≤60s；REVIEW_DUE 超 7 天积压≤5；周新增 DRAFT≥5。

## 10. 里程碑

M1 存储+状态机落地 → M2 检索 → M3 反馈闭环 → M4 webhook 自动更新 → M5 问答与看板。
详细任务见根 CLAUDE.md 的 Backlog。
