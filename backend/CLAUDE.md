# backend — FastAPI 服务

## 结构

```
app/
  main.py            FastAPI 入口，挂载 api/ 下全部 router
  config.py          Settings（env 前缀 ZY_，见 .env.example）
  db.py              engine / SessionLocal / Base / get_db
  models.py          11 实体 SQLAlchemy 模型（与 docs/design.md §3 一一对应，已完整）
  schemas.py         Pydantic 请求/响应模型（与 docs/api-contract.md 对应）
  api/               路由骨架：search assets feedback review gaps dashboard hooks
  services/
    state_machine.py 五态状态机（已实现，唯一合法的状态修改入口）
    search.py        混合检索重排公式（评分已实现，召回待接 PG/pgvector）
    review_queue.py  复核任务创建/去抖/优先级（骨架）
    ai.py            LLM 网关接口占位（草稿/摘要/diff 分析/embedding）
tests/
  test_state_machine.py  状态机规则测试（sqlite 内存库，可直接 pytest）
```

## 规则（继承根 CLAUDE.md，此处是实现层细则）

- 改状态**只能**调 `state_machine.transition(db, asset, to, trigger, evidence, actor)`；
  它负责合法性校验、非作者校验、StatusTransition 落库。api/ 层禁止直接赋值 `asset.status`。
- `services/ai.py` 的所有函数返回值必须标注 ai 来源（版本 created_from="ai_draft"），
  且**永远不许**调用 transition 把任何资产置为 VERIFIED。
- 事件表（ReuseEvent/SearchEvent/UserFeedback/StatusTransition）只 INSERT，禁止 UPDATE/DELETE。
- 搜索评分权重集中在 `search.py::WEIGHTS`，调参只改那里并同步 docs/design.md §5。

## 命令

```bash
pip install -e ".[dev]"        # 安装（或 uv sync）
uvicorn app.main:app --reload  # 启动，先 docker compose up -d db
pytest                         # 测试（状态机测试不需要 PG）
ruff check app tests           # lint
```

## 当前状态与 TODO

- models.py / state_machine.py / search.py 评分：已实现
- Alembic 已接：`alembic/` + 首个迁移 `562da9d71450`。连接串只有一个来源
  （`ZY_DATABASE_URL` → `settings.database_url` → env.py 注入），`alembic.ini` 的
  `sqlalchemy.url` 留空且必须保持纯 ASCII —— configparser 用系统 locale 解码，中文会崩。
- 已实现：`POST /assets`、`GET /assets/{id}`、`GET /assets/{id}/transitions`、
  `POST /feedback/useful`；`main.py` 统一错误形状 `{error:{code,message}}`
- 仍是骨架：search/ask（M2/M5）、feedback 的 stale·not-found（M3）、review/gaps/dashboard/hooks、ai.py
- 种子数据：`scripts/seed.py` 从 `../prototype/kms-prototype.html` 提取 18 条资产
- 测试：`pytest` 39 passed，全部走 sqlite 内存库

### 写代码前值得知道的坑

- **sqlite 不校验 VARCHAR 长度**，超长字段只有在 PG 上才炸。请求 schema 必须显式写
  `max_length`，对齐 models.py 的列宽。
- **sqlite 不存时区**：同一份数据 POST 响应（内存里带 tzinfo）与 GET 响应（读回来的裸值）
  序列化结果差一个 `Z`。PG 的 timestamptz 会原样往返。测试里比较时间串要注意。
- `updated_at` 带 `onupdate`：只有显式赋值才不会被刷成当前时间（种子回填历史时间靠这点）。
- `transition()` / `create_as_draft()` 的 `at` 参数只给种子回填用，线上路径一律省略。
