# 知源（zhiyuan-kb）— 团队推理知识管理与智能搜索系统

面向 vLLM Ascend / SGLang / 模型适配团队的知识底座 MVP。
核心理念：**一处沉淀、三级知识、五态管理、按需治理**，解决"找得到、看得懂、可信任、低负担、可持续更新"。

## 权威资料（开发前必读）

| 资料 | 位置 | 说明 |
|---|---|---|
| 设计文档（仓内权威版） | `docs/design.md` | 数据模型、状态机、评分公式、指标口径 — **实现以此为准** |
| API 约定 | `docs/api-contract.md` | MVP 接口清单与请求/响应形状 |
| 交互原型（UI 基准） | `prototype/kms-prototype.html` | 直接浏览器打开；七页面的布局、文案、交互都照它做 |
| 设计文档（完整排版版） | https://claude.ai/code/artifact/45f5139a-c673-437c-8526-1d5701fcc845 | 含架构图/状态机图/时序图 |
| 原型（在线版） | https://claude.ai/code/artifact/2fbc43d4-10af-4d58-954e-23cbd80446a0 | 与 prototype/ 目录同一份 |

## 仓库结构

```
backend/    FastAPI + SQLAlchemy + PostgreSQL(pgvector)  — 见 backend/CLAUDE.md
frontend/   React + Vite + TS                            — 见 frontend/CLAUDE.md
docs/       设计文档与 API 约定
prototype/  单文件交互原型（内存数据，勿当作生产代码）
```

## 硬规则（任何实现不得违反）

1. **AI 永远不产出可信状态**：AI 只能生成摘要、分类、关联、草稿。`VERIFIED` 只能由
   「非作者成功复用」或「人工验证/复核确认」证据产生；接受 AI 更新草稿后资产回到 `DRAFT`。
2. **状态流转必须带证据**：改 `KnowledgeAsset.status` 只能通过
   `services/state_machine.py::transition()`，同事务追加 `StatusTransition` 流水
   （from/to/trigger/evidence/actor），禁止直接 UPDATE status。
3. **非作者校验**：DRAFT→VERIFIED 的 ReuseEvent/ValidationRecord 必须 `user_id != author_id`，服务端强校验。
4. **STALE/ARCHIVED 隔离**：默认不进搜索结果、不进 RAG 上下文；仅"历史资产"模式可检索。
5. **指标口径**：有效复用率 = 非作者成功复用事件 ÷ 需求事件（搜索/问答会话去重 + 缺口）。
   **禁止**用点击量/PV 冒充复用。
6. **低负担**：任何面向普通成员的表单不得超过 3 个必填项；反馈是三键单击。

## 开发命令

```bash
# 数据库（postgres16 + pgvector，端口 5433）
docker compose up -d db
# 无 Docker 时的替代方案见下方「不装 Docker 起 PostgreSQL」

# 后端（Python 3.11+）
cd backend
pip install -e ".[dev]"          # 或 uv sync
alembic upgrade head             # 建表（连接串取 ZY_DATABASE_URL）
python scripts/seed.py           # 导入原型 18 条示例资产 + 4 条缺口，并建检索索引；覆盖导入加 --reset
python scripts/reindex.py        # 改了分词规则/字段组装后重建索引；--embeddings 回填向量
uvicorn app.main:app --reload    # http://localhost:8000/docs
pytest                           # 全部测试走 sqlite 内存库，不需要 PG

# 前端
cd frontend
npm install
npm run dev                      # http://localhost:5173，/api 代理到 8000
```

## 当前状态（2026-08-25 M5 完成 —— MVP 全部里程碑交付）

- [x] 设计定稿（docs/design.md）、原型验证通过
- [x] **M1** backend：11 实体模型 + 状态机；Alembic 首个迁移；`POST /assets`、`GET /assets/{id}`、
      `GET /assets/{id}/transitions`、`POST /feedback/useful`
- [x] **M1** frontend：theme.css（原型双主题 token）、详情页、沉淀页跑通真实接口
- [x] **M1** 迁移已在真实 PostgreSQL 16.2 上验证：环形外键 `fk_knowledge_asset_current_version`
      存在，`status_transition.trigger` 解析到 `transition_trigger` 而非 `pg_catalog` 伪类型
- [x] **M2** 双路召回：PG 全文（jieba 预分词 + tsvector 生成列 + GIN）+ pgvector（HNSW），
      RRF 融合 → `services/search.py` 重排；`GET /search` 逐条返回分项得分与召回后端
- [x] **M2** `POST /assets` 摘要改由 AI 生成（`summary_source` 标注来源），网关不可用回落规则式
- [x] **M2** `GET /home`、`GET /gaps`（只读）；前端首页与搜索结果页对齐原型
- [x] **M2** 在真实 PG 上验收：中文查询、字段权重、状态可信度、历史隔离、显式/推断筛选、
      pgvector `<=>` + HNSW 全部实测通过；`pytest` 103 passed（sqlite）
- [x] **M3** 三键反馈闭环打通：`POST /feedback/stale`（→ ReviewTask + REVIEW_DUE，24h 去抖合并）、
      `POST /feedback/not-found`（→ KnowledgeGap 建新/累计）、`POST /gaps/{id}/claim`（认领登记）
- [x] **M3** frontend：详情页三键全部接通、搜索页两处「记录知识缺口」、首页「认领并生成草稿」；
      toast 收敛为 `lib/toast.tsx` 一份
- [x] **M3** 在真实 PG 上验收：VERIFIED→REVIEW_DUE 流转与 `transition_trigger` 枚举往返、
      去抖合并、缺口累计（hit_count/reporters）、STALE/ARCHIVED 409、认领冲突 409 全部实测通过；
      `pytest` 133 passed（sqlite）
- [x] **M4** 自动更新闭环：`POST /hooks/git`（GitHub HMAC / GitLab token 校验；push·tag·PR merged
      三类事件；匹配 CodeReference(watch=true)；复用 M3 `open_task` 24h 去抖契约）→ REVIEW_DUE +
      AI 影响摘要/更新草稿（`ai.impact_summary`/`update_draft`，网关不可用任务照建）
- [x] **M4** 复核队列：`GET /review`（priority 降序 + design.md §4 按需治理过滤）、
      `POST /review/{id}/resolve` 四选一全走状态机；接受草稿回 DRAFT 且同事务刷新检索索引；
      frontend 复核队列页照原型（diff 红绿行、草稿折叠、四选一、侧栏角标）
- [x] **M4** 在真实 PG 上验收：签名 401、去抖合并、枚举往返、四选一状态/证据/409 语义、
      降级路径（无摘要草稿 + `NO_AI_DRAFT`）、accept 后 pg_tsvector 立即可检索、tag 批量触发
      全部实测通过；`pytest` 169 passed（sqlite）
- [x] **M5-0** LLM 网关 API key：`ZY_LLM_API_KEY`（空 = 内网免鉴权，非空带 Bearer 头）+
      embedding 独立网关 `ZY_EMBEDDING_GATEWAY_URL/_API_KEY`（空 = 跟随主网关）；熔断按
      chat/embedding 端点分开；超时两档（检索 6s / 生成 60s）。实测接入：chat=DeepSeek
      `deepseek-v4-flash`、embedding=SiliconFlow `BAAI/bge-m3`（1024 维，与 vec 列吻合）
- [x] **M5-1** `POST /ask` RAG 问答：§6 五条硬性规则逐条落地（引用逐字校验、not_found
      不调 LLM、STALE 召回层隔离、冲突并列、REVIEW_DUE 风险挂 M4 影响摘要）；无兜底，
      降级 503 `AI_UNAVAILABLE`；SearchEvent(mode=qa) 生成前落库
- [x] **M5-1** `GET /dashboard` 7 指标全部由事件表实时聚合（`services/metrics.py`，口径
      冻结进 design.md §9）；`/home` 第五格复用率同口径同函数
- [x] **M5-1** 缺口认领闭环：`POST /gaps/{id}/draft`（AI 底稿，只返回预填不落库、内部
      检索不落 SearchEvent）→ `POST /assets` 带 `gap_id` 发布 → 缺口自动 resolved 回链
- [x] **M5-2** frontend：问答页（引用块/风险/冲突/not_found+一键记缺口/「暂不可用」五态）、
      看板页（4 卡 + 趋势条 + 覆盖矩阵、公式与分子分母展示、估算自报）、首页复用率真数、
      认领→AI 底稿→沉淀页预填→发布回链；「后端没有的按钮 disabled 占位」规则退役
- [x] **M5** 在真实 PG + 真实公有云网关（DeepSeek + SiliconFlow）上验收：MLA 双 VERIFIED
      引用、KA-010 REVIEW_DUE 引用带 M4 影响摘要与复核链接、PD 分离 not_found→记缺口→
      认领→底稿(22s)→发布 KA-019→立即可检索可引用、重复回链 409、降级 503 语义、
      看板与 /home 数字对账全部通过；`pytest` 211 passed（sqlite）；seed --reset 已清场

### M2 期间定下的三件事（改动别退回去）

1. **PG 专属结构不进 ORM**：`asset_search_doc.tsv`（tsvector 生成列）和 `asset_embedding.vec`
   （pgvector 列）都只在迁移里建，models.py 不声明它们 —— 这两个类型是 PG 专属，声明了
   `models.py` 就没法在 sqlite 上 `create_all`，全部测试都得改成依赖 PG。
   召回层用 `literal_column` 引用它们，并在启动时探测存在与否（`recall.capabilities`）。
2. **生成列的表达式必须 immutable**：`to_tsvector('simple', col)`（配置名写成字面量）才行；
   写成 `to_tsvector(col)` 走默认配置是 stable，PG 直接拒绝建列。
3. **两路召回都必须能降级**，且降级要随响应返回（`recall` 字段）。判断可用性的顺序是
   「方言 → 列在不在 → 网关这次通不通」，任何一环失败都只影响那一路，不影响搜索可用性。

### M3 期间定下的四件事（改动别退回去）

1. **缺口合并不用 embedding**：判据是 jieba 词集合（Jaccard ≥ `ZY_GAP_MERGE_SIMILARITY`，
   或短侧整体落在长侧且 ≥2 词），实现在 `services/gaps.py`。累计发生在写路径上，
   不能挂在会超时/会熔断的网关上；词集合还让 PG 与 sqlite 行为一致，测试不必依赖 PG。
2. **缺口问句照搜索词原样存**，不套「关于「X」的可用知识」这类壳子 —— 壳子词（可用/知识/关于）
   会进词集合，把互不相干的短查询系统性地拉相似，合并判据直接失真。
3. **认领不产出资产**：`/gaps/{id}/claim` 只登记「我来写」（status=claimed + claimed_by）。
   原型的文案也只承诺「AI 将…生成草稿底稿」；真的生成要等 `ai.draft_from_session`（M5）。
4. **`review_queue.open_task` 返回 `(task, created)`**：去抖合并时要把已存在的任务 id 交回给
   调用方，否则第二个反馈者只能收到「合并了」却指不出并进了哪条。M4 的 webhook 照这个契约用。

### M4 期间定下的四件事（改动别退回去）

1. **复核确认恢复「进入 REVIEW_DUE 前的状态」**：VERIFIED 进来的回 VERIFIED，DRAFT 进来的
   只回 DRAFT —— 复核回答「变更是否影响了这份知识」，不判对错；从未被验证的 DRAFT 借一次
   复核确认直达 VERIFIED 就绕过了非作者校验（硬规则 3）。状态机为此给
   `REVIEW_DUE→DRAFT` 加了 `review_confirm` 触发器。
2. **AI 草稿在生成时就落成 AssetVersion(created_from=ai_draft)**，任务只存 `ai_draft_version_id`，
   接受（accept_draft）只是把 `current_version_id` 切过去 —— 版本表是不可变审计快照，
   「生成过但没被采纳」的草稿也要留痕；confirm 掉的任务留下的草稿版本不是脏数据，别清。
   接受后必须同事务 `indexing.refresh_doc`/`refresh_embedding`，否则更新完的正文搜不到。
3. **webhook 对无关事件返回 200 handled=false 而不是 4xx**：GitHub/GitLab 对非 2xx 会重试，
   报错只会让同一事件反复砸过来。签名错才是 401。payload 没有 diff 正文，AI 摘要/草稿基于
   文件清单 + 提交说明生成；「纯格式化 diff 预判抑制」要等接入 Git API 拉真 diff。
4. **`resolve` 顺带关闭同资产其它 open 任务**，且队列查询只列资产仍是 REVIEW_DUE 的任务 ——
   两道防线防的是同一件事：不让人对着已处理完的资产点四选一收 409。

### M5 期间定下的五件事（改动别退回去）

1. **问答检索按 rel 分项选择，不按总分**：trust/fresh/proof 是搜索列表的排序信号，不代表
   「与问题相关」。真链路验收踩过：aclgraph 提问时 KA-010(REVIEW_DUE) rel=30 全场最高，
   却被六条弱相关 VERIFIED 的总分挤出 top5 —— 问答答不出来，§6 规则 5 也无从谈起。
   实现在 `services/ask.py::retrieve`（取大池按 rel 排序，阈值也打 rel）。
2. **超时两档、熔断按端点分**：`llm_timeout`(6s) 只管检索路径的 embed；chat 生成
   （摘要/草稿/问答/底稿）一律 `generation_timeout`(60s，公有云波动实测 22s 与 30s+ 都出现过)。
   拿检索超时卡生成会频繁超时→熔断→连带把问答一起降级 60 秒。chat 与 embedding 的熔断
   必须分开 —— 两端可能是两家服务（DeepSeek 没有 embedding），一路失败不许静默另一路。
3. **问答没有兜底，降级是明确语义**：网关不可用/输出不可解析 → 503 `AI_UNAVAILABLE`，
   绝不悄悄用模型通用知识把答案编出来（§6 规则 2 靠「无命中不调 LLM」+「无有效引用即无据」
   双保险，不靠提示词自觉）。SearchEvent(mode=qa) 在生成之前 commit —— 503 的会话也进分母。
4. **复用率分母防双算**：分母 = 去重需求会话 + **不带 search_event_id 的** not_found 反馈。
   带 event_id 的缺口反馈，其需求已随搜索会话计入。同理，`/gaps/{id}/draft` 的内部检索
   **不落 SearchEvent** —— 系统辅助不是用户需求，落了就是自己污染自己的分母。
5. **底稿只是预填**：`/gaps/{id}/draft` 不落库任何东西，闭环发生在 `POST /assets` 带
   `gap_id` 时（缺口置 resolved + 回链资产）。这与 M3「认领不产出资产」是同一条线的
   两端，中间不许插入任何自动发布。

### M1 期间发现并修掉的两个 PG 专属问题（改动别退回去）

1. `KnowledgeAsset.current_version_id` ↔ `AssetVersion.asset_id` 是环形外键，必须 `use_alter`，
   建表期先建两表再 ALTER 补约束。SQLite 容忍前向引用，会掩盖这个问题。
2. `Trigger` 枚举的 PG 类型名是 `transition_trigger`：`pg_catalog` 有内置伪类型 `trigger`
   且隐式排在 search_path 最前，同名会被遮蔽并报 `column "trigger" has pseudo-type trigger`。

## 下一步（V1.1 清单 —— MVP 五个里程碑已全部完成）

MVP（M1–M5）已交付。以下按价值/依赖排序，是 V1.1 的候选项：

1. **接 Git API 拉真 diff**（M4 遗留）：AI 影响摘要/更新草稿现基于 webhook 的文件清单 +
   提交说明；拉到真 diff 后顺带做「纯格式化 diff 预判抑制」（抑制记录可抽查）。
2. **SSO 鉴权**：替换 `X-User` 头（`frontend/src/api/client.ts::CURRENT_USER` 一并删除）。
3. **图片链路**：沉淀页图片上传 + 存储 → `ai.embed` 多模态化（候选 Qwen/Qwen3-VL-Embedding-8B，
   4096 维需 pgvector ≥0.7 的 halfvec 才能建 HNSW —— `vector` 类型索引上限 2000 维，
   devdb 自带 0.6.2 要先升级）→ 问答 chat 换 VL 模型。换 embedding 维度 = 新迁移重建
   vec 列 + `reindex.py --embeddings --force` 全量回填（.env.example 里已写明）。
4. **沉淀页会话接入**：`来源 · AI 自动提取` 现在是原型演示数据；接真实 Claude Code /
   IDE 会话后由 `ai.py` 抽取（沉淀页的另一半 AI 预填，与缺口底稿共用清洗逻辑）。
5. **问答历史模式**：§6 规则 3 后半句 —— 历史模式引用挂「已失效，仅供追溯」横幅。
6. **发布路径 embedding 后台化**：`POST /assets` 现同步等一次 embedding 网关往返，
   库量上来后挪后台任务（`api/assets.py` 里有 TODO）。
7. **导航式查询识别**：§9 分母的「− 导航式查询」MVP 未实现，需要点击行为数据积累后再定。

每项动手前：先在本文件登记范围，完成后对照 `prototype/kms-prototype.html` 做 UI 验收（如涉及）。

## 不装 Docker 起 PostgreSQL（`scripts/devdb.ps1`）

没有 Docker 时的替代：PyPI 的 `pgserver` 自带 PostgreSQL 16 二进制，不需要管理员权限。

```powershell
powershell -File scripts/devdb.ps1 init     # 首次：建 venv + 数据目录 + 建库
powershell -File scripts/devdb.ps1 start    # 启动
powershell -File scripts/devdb.ps1 status   # 是否在跑 + 各状态资产条数
powershell -File scripts/devdb.ps1 psql     # 交互式 psql
powershell -File scripts/devdb.ps1 stop     # 停止
powershell -File scripts/devdb.ps1 reset    # 删库重来
```

账号/密码/端口与 `docker-compose.yml` 一致（`zhiyuan` / `zhiyuan_dev` / 5433），
正好是 `config.py` 的默认值，所以 `ZY_DATABASE_URL` 不用设。
数据目录 `.pgdata/` 与 venv `.pgvenv/` 都在 `.gitignore` 里。

**这个 Windows 构建自带 pgvector 0.6.2**（M2 实测：`CREATE EXTENSION vector` 成功，
`vector(1024)` 列 + HNSW 索引建得出来，`<=>` 查询跑得通）。早前「不带 pgvector」的判断是错的。
即便换到没有该扩展的 PG 也能跑：迁移会跳过 vec 列，向量召回自动降级为 Python 余弦。

写这个脚本时踩到的三个 Windows 坑（改动别退回去）：
1. `.ps1` 含中文必须存成 **UTF-8 with BOM** —— Windows PowerShell 5.1 没 BOM 就按系统 ANSI 码页读，直接语法报错。
2. 启动 daemon 不能用 `Start-Process -Wait`（它等整个进程树，含不会退出的 postgres），
   也不能让 daemon 继承调用方的控制台/管道（`&` 调用或 `-NoNewWindow` 都会），
   否则调用方永远挂住。用 `-WindowStyle Hidden` 且不加 `-Wait`，就绪与否靠轮询 `pg_isready`。
3. 传给 `psql.exe -c` 的 SQL 要保持纯 ASCII：命令行参数按系统 ANSI 码页编码，
   中文（哪怕只是列别名）到服务端就是非法 UTF-8。同理，`curl` 发中文 JSON 请求体要用 `--data-binary @文件`。
