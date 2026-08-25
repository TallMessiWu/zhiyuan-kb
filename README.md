# 知源 zhiyuan-kb

团队推理知识管理与智能搜索系统（MVP）。服务于 vLLM Ascend / SGLang / 模型适配 / 推理框架开发团队。

- 一处沉淀：AI 从会话 / Diff / Issue 生成草稿，作者只确认「问题、环境、结论」三项
- 三级知识：工作记录 → 共享知识 → 核心资产（按需求拉动升级）
- 五态管理：DRAFT / VERIFIED / REVIEW-DUE / STALE / ARCHIVED（可信度标签，自动流转）
- 按需治理：只有被使用、高风险、受代码或版本变化影响的知识才进入人工复核队列

开发指引见 [CLAUDE.md](CLAUDE.md)，设计规则见 [docs/design.md](docs/design.md)，
交互原型打开 [prototype/kms-prototype.html](prototype/kms-prototype.html)。

## 快速开始

### 部署到服务器（只需要 Docker）

```bash
cp .env.docker.example .env   # 填 LLM 网关 key 与对外端口
docker compose up -d --build  # 自动建表 + 导入示例数据 + 起前后端
```

打开 `http://<服务器IP>:8080`。详细步骤与运维命令见 [docs/setup.md](docs/setup.md) 第五节。

### 本机开发

```bash
docker compose up -d db       # Windows 免 Docker 方案：powershell -File scripts/devdb.ps1 start
cd backend && pip install -e ".[dev]" && python -m alembic upgrade head && python scripts/seed.py
cd backend && uvicorn app.main:app --reload   # .env 按进程 cwd 解析，必须在 backend/ 起
cd frontend && npm install && npm run dev
```

从零装机的完整指引见 [docs/setup.md](docs/setup.md)。
