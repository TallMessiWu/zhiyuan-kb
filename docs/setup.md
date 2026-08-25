# 知源（zhiyuan-kb）安装与启动指南

从零装机看「二、首次安装」；装过了只是电脑重启，直接看「一、日常启动」。

## 一、日常启动（电脑重启后，三条命令）

三样东西都不会开机自启，按顺序起：

```powershell
# 1. 数据库（pgserver，端口 5433）
powershell -File scripts/devdb.ps1 start

# 2. 后端（必须先 cd 进 backend/ —— .env 按进程当前目录解析，在别处起会静默丢掉网关配置）
cd backend
python -m uvicorn app.main:app --reload --port 8000

# 3. 前端（另开一个终端）
cd frontend
npm run dev
```

打开 http://localhost:5173 即可使用；后端接口文档在 http://localhost:8000/docs。

> 用 Docker 的话第 1 步换成 `docker compose up -d db`，其余一样。

## 二、首次安装（从零）

### 0. 前置要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | 3.11+ | 后端 FastAPI |
| Node.js | 18+（含 npm） | 前端 React + Vite |
| Docker | 可选 | 没有也行，`scripts/devdb.ps1` 用 PyPI 的 pgserver 起库，无需管理员权限 |

### 1. 克隆仓库

```bash
git clone https://github.com/TallMessiWu/zhiyuan-kb.git
cd zhiyuan-kb
```

### 2. 数据库（二选一）

**方式 A：Docker（有 Docker 就选这个）**

```bash
docker compose up -d db
```

**方式 B：pgserver（Windows 免 Docker、免管理员权限）**

```powershell
powershell -File scripts/devdb.ps1 init     # 首次：建 venv + 数据目录 + 建库（几分钟）
powershell -File scripts/devdb.ps1 start    # 启动
```

两种方式的账号/密码/端口一致（`zhiyuan` / `zhiyuan_dev` / 5433），正好是后端默认值，
所以不需要设置 `ZY_DATABASE_URL`。这个 pgserver 构建自带 pgvector 0.6.2，向量检索可用。

### 3. 后端

```bash
cd backend
pip install -e ".[dev]"        # 或 uv sync
```

### 4. 配置 LLM 网关（`backend/.env`，可跳过）

不配也能跑：搜索/浏览/反馈/复核全部正常，只是 AI 摘要落回规则式、向量召回降级、
问答与缺口底稿返回「暂不可用」。要完整体验（问答 / AI 摘要 / AI 底稿），复制模板并填 key：

```bash
cp .env.example .env
```

`.env` 里填任何 OpenAI 兼容端点。当前团队用的组合（key 自己申请）：

```ini
# chat：DeepSeek
ZY_LLM_GATEWAY_URL=https://api.deepseek.com/v1
ZY_LLM_MODEL=deepseek-v4-flash
ZY_LLM_API_KEY=sk-你的key

# embedding：SiliconFlow 的 bge-m3（必须 1024 维 —— pgvector 列钉死 vector(1024)）
ZY_EMBEDDING_GATEWAY_URL=https://api.siliconflow.cn/v1
ZY_EMBEDDING_API_KEY=sk-你的key
ZY_EMBEDDING_MODEL=BAAI/bge-m3
```

注意：
- `.env` 在 `.gitignore` 里，**key 永远不进 git**。
- key 留空 = 内网免鉴权网关模式（不带 Authorization 头）。
- 换 embedding 模型必须保持 1024 维；换维度要重建 vec 列（新迁移）并
  `python scripts/reindex.py --embeddings --force` 全量回填，见 `.env.example` 注释。

### 5. 建表 + 导入种子数据

仍在 `backend/` 目录下（读 `.env` 需要）：

```bash
python -m alembic upgrade head       # 建 17 张表 + tsvector 生成列 + pgvector HNSW 索引
python scripts/seed.py               # 导入 18 条示例资产 + 4 条缺口，并建检索索引
```

配了 embedding 网关的话，seed 会顺带真实回填 18 条向量（几秒）；没配就跳过，
向量召回自动降级、不影响使用。之后想重来一遍用 `python scripts/seed.py --reset`。

### 6. 前端

```bash
cd ../frontend
npm install
npm run dev        # http://localhost:5173，/api 已代理到 :8000
```

### 7. 起后端并验证

```bash
cd ../backend
python -m uvicorn app.main:app --reload --port 8000
```

验证清单：

- http://localhost:8000/healthz 返回 ok
- http://localhost:8000/docs 能看到全部接口
- http://localhost:5173 首页数字条有数（在库 17 / VERIFIED 8）
- 搜索「MLA」能出结果；配了网关的话，问答页点预置问题能出带引用的回答（生成要 10–40s）

## 三、常见问题

| 症状 | 原因与解法 |
|---|---|
| 问答/摘要报「网关不可达」，但 `.env` 明明配了 | uvicorn 不是在 `backend/` 目录下起的 —— `.env` 按进程当前目录解析，换目录就静默失效。`cd backend` 重起 |
| 前端所有请求转圈，`/healthz` 却正常 | 数据库没起（重启后 pgserver 不自启）。`powershell -File scripts/devdb.ps1 status` 看一眼，没跑就 `start` |
| 5173 或 8000 端口被占 | 前端：`npm run dev -- --port 5174`；后端：`--port 8001`（同时改 `frontend/vite.config.ts` 的 proxy target） |
| 跑测试要不要起库？ | 不用。`cd backend && pytest` 全部走 sqlite 内存库，211 个测试离线可跑 |
| Git Bash / 终端里中文显示乱码 | 控制台 ANSI 码页的显示问题，库里数据是好的，别按乱码去「修」编码 |
| 问答偶尔返回「暂不可用」 | 公有云生成延迟波动（超 60s 超时）或熔断静默期（60s），稍后重试即可；这是明确语义不是 bug |

## 四、停止与重置

```powershell
# 停数据库
powershell -File scripts/devdb.ps1 stop
# 删库重来（危险：数据全清）
powershell -File scripts/devdb.ps1 reset
# 重置为种子数据（保留库，清掉试用产生的数据）
cd backend; python scripts/seed.py --reset
```

后端/前端进程 Ctrl+C 停止即可。
