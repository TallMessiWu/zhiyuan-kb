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

## 当前状态（2026-08-24 M3 完成）

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
- [ ] 自动更新、问答、看板仍是骨架（M4–M5）

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

### M1 期间发现并修掉的两个 PG 专属问题（改动别退回去）

1. `KnowledgeAsset.current_version_id` ↔ `AssetVersion.asset_id` 是环形外键，必须 `use_alter`，
   建表期先建两表再 ALTER 补约束。SQLite 容忍前向引用，会掩盖这个问题。
2. `Trigger` 枚举的 PG 类型名是 `transition_trigger`：`pg_catalog` 有内置伪类型 `trigger`
   且隐式排在 search_path 最前，同名会被遮蔽并报 `column "trigger" has pseudo-type trigger`。

## 下一步 Backlog（按序执行）

- ~~**M3 反馈闭环**~~：已完成（2026-08-24）。
- **M4 自动更新**：GitHub/GitLab webhook → CodeReference 匹配（24h 去抖）→ REVIEW-DUE +
  AI diff 摘要/草稿（`services/ai.py`）；复核队列四选一 API。
- **M5 问答与看板**：RAG 问答（引用/无据明说/冲突并列，规则见 design.md §6）；看板 7 指标由事件表实时聚合。

每个 M 完成后：对照 `prototype/kms-prototype.html` 的对应页面做 UI 验收。

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
