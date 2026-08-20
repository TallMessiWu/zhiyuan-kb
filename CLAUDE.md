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

# 后端（Python 3.11+）
cd backend
pip install -e ".[dev]"          # 或 uv sync
alembic upgrade head             # 建表（连接串取 ZY_DATABASE_URL）
python scripts/seed.py           # 导入原型 18 条示例资产；覆盖导入加 --reset
uvicorn app.main:app --reload    # http://localhost:8000/docs
pytest                           # 全部测试走 sqlite 内存库，不需要 PG

# 前端
cd frontend
npm install
npm run dev                      # http://localhost:5173，/api 代理到 8000
```

## 当前状态（2026-08-20 M1 完成）

- [x] 设计定稿（docs/design.md）、原型验证通过
- [x] backend：11 实体模型 + 状态机；Alembic 首个迁移；`POST /assets`、`GET /assets/{id}`、
      `GET /assets/{id}/transitions`、`POST /feedback/useful`；`pytest` 39 passed
- [x] frontend：theme.css（原型双主题 token）、详情页、沉淀页跑通真实接口
- [x] 种子数据：`backend/scripts/seed.py` 导入原型 18 条资产（含验证/复用/代码引用/流水）
- [ ] 迁移尚未在真实 PostgreSQL 上跑过（本机无 Docker）：目前证据是 SQLite 上 `upgrade head`
      建表成功 + `alembic upgrade head --sql` 渲染出的 PG DDL 顺序与类型正确。
      有 Docker 后请补跑一次，见下方命令。
- [ ] 检索、问答、复核队列、看板仍是骨架（M2–M5）

### M1 期间发现并修掉的两个 PG 专属问题（改动别退回去）

1. `KnowledgeAsset.current_version_id` ↔ `AssetVersion.asset_id` 是环形外键，必须 `use_alter`，
   建表期先建两表再 ALTER 补约束。SQLite 容忍前向引用，会掩盖这个问题。
2. `Trigger` 枚举的 PG 类型名是 `transition_trigger`：`pg_catalog` 有内置伪类型 `trigger`
   且隐式排在 search_path 最前，同名会被遮蔽并报 `column "trigger" has pseudo-type trigger`。

## 下一步 Backlog（按序执行）

- **M1 收尾（待 Docker）**：`docker compose up -d db` 后 `cd backend && alembic upgrade head`
  与 `python scripts/seed.py`，确认真实 PG 建表与导入无误。
- **M2 检索**：PG 全文（zhparser 或 jieba 预分词）+ pgvector(bge-m3) 双路召回，
  RRF 融合 + `services/search.py` 重排公式；`GET /search` 返回分项得分（排序可解释）。
  顺带把 `POST /assets` 里规则式的 summary 派生换成 AI 生成。
- **M3 反馈闭环**：补 `POST /feedback/stale`、`POST /feedback/not-found`
  → ReviewTask/KnowledgeGap；`useful` 已在 M1 落地。
- **M4 自动更新**：GitHub/GitLab webhook → CodeReference 匹配（24h 去抖）→ REVIEW-DUE +
  AI diff 摘要/草稿（`services/ai.py`）；复核队列四选一 API。
- **M5 问答与看板**：RAG 问答（引用/无据明说/冲突并列，规则见 design.md §6）；看板 7 指标由事件表实时聚合。

每个 M 完成后：对照 `prototype/kms-prototype.html` 的对应页面做 UI 验收。
