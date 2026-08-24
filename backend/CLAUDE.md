# backend — FastAPI 服务

## 结构

```
app/
  main.py            FastAPI 入口，挂载 api/ 下全部 router
  config.py          Settings（env 前缀 ZY_，见 .env.example）
  db.py              engine / SessionLocal / Base / get_db
  models.py          11 实体 SQLAlchemy 模型（与 docs/design.md §3 一一对应，已完整）
  schemas.py         Pydantic 请求/响应模型（与 docs/api-contract.md 对应）
  api/               search assets feedback home gaps（已实现）· review dashboard hooks（骨架）
  services/
    state_machine.py 五态状态机（已实现，唯一合法的状态修改入口）
    text.py          jieba 中文分词 / 索引文本 / 查询词（M2）
    indexing.py      检索索引维护：分词文档 + 向量回填（M2）
    recall.py        双路召回（关键词/向量）+ 能力探测降级 + RRF 融合（M2）
    search.py        业务重排公式 + run_search 编排（M2）
    gaps.py          知识缺口建新/累计 + 同一需求的合并判据（M3）
    review_queue.py  复核任务创建 + 24h 去抖合并（M3）；优先级与四选一处理仍是骨架（M4）
    ai.py            LLM 网关：chat / embed / summarize，同步 + 超时 + 熔断降级
tests/
  test_state_machine.py  状态机规则
  test_assets_api.py     发布/详情/流水     test_feedback_api.py  三键反馈
  test_search.py         分词·RRF·版本·重排（纯函数）
  test_search_api.py     GET /search 端到端  test_home_api.py  首页与缺口
  test_gaps_api.py       记缺口·累计·认领（M3）
  test_seed.py           种子数据一致性
```

## 规则（继承根 CLAUDE.md，此处是实现层细则）

- 改状态**只能**调 `state_machine.transition(db, asset, to, trigger, evidence, actor)`；
  它负责合法性校验、非作者校验、StatusTransition 落库。api/ 层禁止直接赋值 `asset.status`。
- `services/ai.py` 的所有函数返回值必须标注 ai 来源（版本 created_from="ai_draft"），
  且**永远不许**调用 transition 把任何资产置为 VERIFIED。
- 事件表（ReuseEvent/SearchEvent/UserFeedback/StatusTransition）只 INSERT，禁止 UPDATE/DELETE。
- 搜索评分权重集中在 `search.py::WEIGHTS`，调参只改那里并同步 docs/design.md §5。
- `services/ai.py` 的调用一律走模块属性（`ai.embed(...)`，不要 `from .ai import embed`），
  否则测试打不上桩；网关失败一律返回 None 让调用方降级，不要把异常抛给用户。
- 改了分词规则或索引字段组装，必须跑 `python scripts/reindex.py --force`，否则老索引还是旧词。

## 命令

```bash
pip install -e ".[dev]"        # 安装（或 uv sync）
uvicorn app.main:app --reload  # 启动；先起库：docker compose up -d db 或 ../scripts/devdb.ps1 start
python scripts/reindex.py      # 重建检索索引（--force 忽略指纹，--embeddings 回填向量）
pytest                         # 测试（不需要 PG，全部走 sqlite 内存库）
ruff check app tests           # lint
```

## 当前状态与 TODO

- M2 已实现：`GET /search`（双路召回 + RRF + 重排 + 分项得分 + SearchEvent）、
  `GET /home`、`GET /gaps`、AI 摘要（可降级）、`scripts/reindex.py`
- models.py / state_machine.py / search.py 评分：已实现
- Alembic 已接：`alembic/` + 首个迁移 `562da9d71450`。连接串只有一个来源
  （`ZY_DATABASE_URL` → `settings.database_url` → env.py 注入），`alembic.ini` 的
  `sqlalchemy.url` 留空且必须保持纯 ASCII —— configparser 用系统 locale 解码，中文会崩。
- 已实现：`POST /assets`、`GET /assets/{id}`、`GET /assets/{id}/transitions`、
  三键反馈全部（`useful` M1 / `stale`·`not-found` M3）、`POST /gaps/{id}/claim`；
  `main.py` 统一错误形状 `{error:{code,message}}`
- 仍是骨架：`/ask`（M5）、review/dashboard/hooks、ai.py 的
  impact_summary·update_draft（M4）、draft_from_session（M5，缺口认领的 AI 底稿）
- 种子数据：`scripts/seed.py` 从 `../prototype/kms-prototype.html` 提取 18 条资产 + 4 条缺口，
  末尾建检索索引（`--no-embeddings` 可跳过向量）
- 测试：`pytest` 133 passed，全部走 sqlite 内存库。conftest 有个 autouse 夹具默认关掉
  AI/向量开关 —— 开发机上万一有服务监听 9000，测试结果就会随环境漂移

### M2 验收（真实 PostgreSQL 上跑过一遍才算数）

```bash
powershell -File ../scripts/devdb.ps1 start
python -m alembic upgrade head     # 建 asset_search_doc/asset_embedding + tsv 生成列 + HNSW
python scripts/seed.py --reset     # 导入并建索引
python -m uvicorn app.main:app --reload
```
验收点：中文查询召回、字段权重（标题命中 > 正文命中）、VERIFIED 压过 DRAFT、
REVIEW_DUE −10 仍可见、STALE/ARCHIVED 只在 `hist=1` 出现、显式 framework 硬过滤 vs
查询里推断的框架 ±6/−8、`recall` 字段如实反映降级。

### M3 验收（同样在真实 PostgreSQL 上跑过）

M3 没动表结构，不需要新迁移。验收点（2026-08-24 实测通过）：
VERIFIED→REVIEW_DUE 流转 + `transition_trigger` 枚举往返、24h 内第二次反馈合并进同一条
ReviewTask（`merged=true` 且返回同一个 task id）、缺口按同一需求累计（hit_count+1、
reporters 去重）、resolved 缺口不吸收新反馈、STALE/ARCHIVED 上反馈 409 `ASSET_NOT_ACTIVE`、
他人重复认领 409 `GAP_ALREADY_CLAIMED`、编错 `search_event_id` 422。

注意：`curl` 发中文 JSON 请求体要用 `--data-binary @文件`（根 CLAUDE.md 的 Windows 坑 3）；
另外 Git Bash 控制台按 ANSI 码页显示，中文响应看着像乱码但库里是好的 —— 别照着"修"编码。

### 写代码前值得知道的坑

- **检索索引不会自己跟上**：`POST /assets` 在同一个事务里调 `indexing.refresh_doc`；
  任何其它写正文/标签/摘要的路径（M4 的复核接受草稿）也必须补这一步，否则内容更新了搜不到。
- **`recall.capabilities` 有进程内缓存**：跑完迁移要重启服务才会重新探测；
  测试里换库要调 `recall.reset_capabilities_cache()`。
- **PG 的 tsquery 别手拼**：版本号里的点和标识符里的连字符都会让手拼的 `to_tsquery`
  语法报错。用 `plainto_tsquery(:term)` 逐词参数化再用 `||` 做 OR（见 `recall._tsquery`）。
- **pgvector 的查询向量要带类型绑定**：`bindparam(..., type_=Vector(n))`；
  写成 `CAST(:qv AS vector)` 的文本片段过不了 SQLAlchemy 的类型强制（M2 踩过，
  已由 `test_search.py::test_pgvector_statement_compiles_for_postgres` 挡住）。
- **sqlite 不校验 VARCHAR 长度**，超长字段只有在 PG 上才炸。请求 schema 必须显式写
  `max_length`，对齐 models.py 的列宽。
- **sqlite 不存时区**：同一份数据 POST 响应（内存里带 tzinfo）与 GET 响应（读回来的裸值）
  序列化结果差一个 `Z`。PG 的 timestamptz 会原样往返。测试里比较时间串要注意。
- `updated_at` 带 `onupdate`：只有显式赋值才不会被刷成当前时间（种子回填历史时间靠这点）。
- `transition()` / `create_as_draft()` 的 `at` 参数只给种子回填用，线上路径一律省略。
- **JSON 列改了不会自己入库**：`KnowledgeGap.reporters` 是普通 JSON 列，没挂 Mutable 追踪，
  `gap.reporters.append(x)` 改的是同一个 list 对象，SQLAlchemy 看不见、不会发 UPDATE。
  必须整体赋新值（见 `services/gaps.py::record`）。
- **计数列一律原子自增**：`reuse_count` / `hit_count` 都走 `UPDATE ... SET c = c + 1`，
  不用读改写 —— 两个并发反馈会互相覆盖，让计数和事件表长期对不上。
- `review_queue.open_task` 返回 `(task, created)`；去抖合并时 created=False，
  返回的是被合并进的那条已存在任务，调用方要拿它的 id 回执。
