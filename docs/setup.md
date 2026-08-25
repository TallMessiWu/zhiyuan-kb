# 知源（zhiyuan-kb）安装与启动指南

| 你的场景 | 看哪一节 |
|---|---|
| 本机开发，电脑重启后要起服务 | 一、日常启动 |
| 本机开发，从零装 | 二、首次安装 |
| **部署到 Linux 服务器** | **五、Linux 服务器：Docker 一键部署** —— 装好 Docker 后三条命令 |

一~四节写的是**本机开发**流程（以 Windows 为例；macOS/Linux 开发机同理，把
`scripts/devdb.ps1` 换成 `docker compose up -d db` 即可）。服务器部署不要照抄这几节：
那里跑的是构建好的前端静态产物和 uvicorn 生产进程，全部由容器负责，见第五节。

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

> 部署到服务器则**只需要 Docker**，Python 与 Node 都不用装（都在镜像里）—— 直接跳到第五节。

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

Docker 部署专属（第五节）：

| 症状 | 原因与解法 |
|---|---|
| 改了根 `.env` 里的 key，容器里没生效 | `restart` 不重新注入环境变量，要 `docker compose up -d`（compose 会发现配置变了并重建容器） |
| 端口被占 / 想换端口 | 改根 `.env` 的 `ZY_HTTP_PORT`，再 `docker compose up -d`。数据库只绑 `127.0.0.1:5433`，不对公网开 |
| 构建卡在 pip / npm 拉包 | 国内服务器在 `.env` 里打开镜像源两行（`PIP_INDEX_URL` / `NPM_REGISTRY`，模板里已注释好），再 `docker compose up -d --build` |
| 问答请求 60s 左右返回 504 | 反代超时被改小了。`frontend/nginx.conf` 的 `proxy_read_timeout` 必须大于后端 `generation_timeout`（60s），默认写的是 180s，别调 |
| 填了网关 key，容器里仍报「网关不可达」 | 网关若跑在这台服务器上，`localhost` 在容器里指的是容器自己。URL 改成 `http://host.docker.internal:9000/v1`（compose 里已配好这个主机名映射） |
| 首页数字条全 0 | 种子没导。看 `docker compose logs backend` 里的 seed 行；要手动导：`docker compose exec backend python scripts/seed.py` |
| 后端一直 unhealthy | 先看 `docker compose logs backend`：多半是迁移失败或连不上库。`docker compose ps` 确认 db 是 healthy |

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

## 五、Linux 服务器：Docker 一键部署

服务器上**只需要 Docker**。三个容器：`db`（pgvector/PostgreSQL 16）、`backend`（uvicorn）、
`frontend`（nginx 托管构建产物，并把 `/api` 反代给 backend）。只有 frontend 对外暴露端口。

### 1. 装 Docker（Ubuntu/Debian，装过就跳过）

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # 之后重新登录一次，docker 命令才不用 sudo
```

CentOS/openEuler 同样可用这个脚本。确认 compose 插件在：`docker compose version`。

### 2. 拉代码，填配置

```bash
git clone https://github.com/TallMessiWu/zhiyuan-kb.git
cd zhiyuan-kb
cp .env.docker.example .env
vi .env        # 填两组网关 key、改端口、改 webhook 密钥
```

`.env` 里真正要动的只有四项（其余留默认）：

| 变量 | 说明 |
|---|---|
| `ZY_HTTP_PORT` | 对外端口，默认 8080 |
| `ZY_LLM_API_KEY` + `ZY_LLM_GATEWAY_URL`/`_MODEL` | chat 网关。不填 = 问答与 AI 底稿返回「暂不可用」，其余功能正常 |
| `ZY_EMBEDDING_API_KEY` + `ZY_EMBEDDING_GATEWAY_URL`/`_MODEL` | embedding 网关，**必须 1024 维**（如 SiliconFlow 的 `BAAI/bge-m3`）。不填 = 向量召回降级，关键词检索照常 |
| `ZY_WEBHOOK_SECRET` | 接 Git webhook 才需要，改成随机串 |

`.env` 在 `.gitignore` 里，key 不会进 git；镜像里也不含任何 `.env`（`.dockerignore` 挡掉了），
配置全部由 compose 在运行时注入。

### 3. 起服务

```bash
docker compose up -d --build
```

首次构建 3–8 分钟（装 Python 依赖 + 构建前端）。backend 容器启动时会自动：
`alembic upgrade head` 建表 → 库为空则导入 18 条示例资产 + 4 条缺口并建检索索引 →
起 uvicorn。配了 embedding 网关的话，种子会顺带回填 18 条向量（几十秒）。

看进度：

```bash
docker compose logs -f backend
```

### 4. 验证

```bash
curl -s localhost:8080/healthz            # {"ok":true}（走的是 nginx 反代，能通说明链路对）
curl -s localhost:8080/api/v1/home        # 首页数字：在库 17 / VERIFIED 8
docker compose ps                         # 三个容器都应是 running，backend 应是 healthy
```

浏览器打开 `http://<服务器IP>:8080`：首页数字条有数、搜索「MLA」出结果并高亮命中词、
详情页底部三键在。配了网关就去问答页点一个预置问题，10–40 秒后应出带引用的回答。
接口文档在 `http://<服务器IP>:8080/docs`（同样走反代，后端 8000 端口不对外暴露）。

### 5. 日常运维

```bash
docker compose logs -f backend            # 看日志
docker compose restart backend            # 重启（改代码/改 .env 用下一条）
git pull && docker compose up -d --build  # 更新到最新代码（会自动跑新迁移）
docker compose down                       # 停服务（数据保留在 volume 里）
docker compose down -v                    # 停服务并删库（数据全清，慎用）
```

数据备份与恢复（数据在 named volume `zhiyuan_pgdata`，`down` 不会删）：

```bash
docker compose exec db pg_dump -U zhiyuan zhiyuan > backup-$(date +%F).sql
cat backup-2026-08-25.sql | docker compose exec -T db psql -U zhiyuan zhiyuan
```

重置为种子数据（保留库，清掉试用产生的数据）：

```bash
docker compose exec backend python scripts/seed.py --reset
```

改了分词规则或想补向量：

```bash
docker compose exec backend python scripts/reindex.py --embeddings --force
```

### 6. 部署前必须知道的三件事

1. **MVP 没有鉴权**：当前用户身份靠前端写死的 `X-User` 头（V1.1 才上 SSO）。
   只部署在内网，或放在带认证的反向代理之后，别直接挂公网。
2. **`ZY_WEBHOOK_SECRET` 一定要改**：默认值 `change-me` 谁都能伪造 Git 事件触发复核。
3. **换 embedding 模型必须保持 1024 维**：pgvector 的列是 `vector(1024)`。换维度要新写迁移
   重建 vec 列，再 `reindex.py --embeddings --force` 全量回填，不是改个环境变量的事。

### 7. 用服务器上已有的 PostgreSQL（可选）

在 `.env` 里填 `ZY_DATABASE_URL=postgresql+psycopg://user:pass@主机:5432/库名`，
然后只起这两个服务（`--no-deps` 必须带：backend 的 `depends_on` 会连带把内置 db 拉起来）：

```bash
docker compose up -d --build --no-deps backend frontend
```

那台 PG 需要装 pgvector 扩展；没有也能跑，迁移会跳过 vec 列，向量召回自动降级为
Python 余弦（功能不缺，只是没有 ANN 加速）。
