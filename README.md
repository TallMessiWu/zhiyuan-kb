# 知源 zhiyuan-kb

团队推理知识管理与智能搜索系统（MVP）。服务于 vLLM Ascend / SGLang / 模型适配 / 推理框架开发团队。

- 一处沉淀：AI 从会话 / Diff / Issue 生成草稿，作者只确认「问题、环境、结论」三项
- 三级知识：工作记录 → 共享知识 → 核心资产（按需求拉动升级）
- 五态管理：DRAFT / VERIFIED / REVIEW-DUE / STALE / ARCHIVED（可信度标签，自动流转）
- 按需治理：只有被使用、高风险、受代码或版本变化影响的知识才进入人工复核队列

开发指引见 [CLAUDE.md](CLAUDE.md)，设计规则见 [docs/design.md](docs/design.md)，
交互原型打开 [prototype/kms-prototype.html](prototype/kms-prototype.html)。

## 快速开始

```bash
docker compose up -d db
cd backend && pip install -e ".[dev]" && uvicorn app.main:app --reload
cd frontend && npm install && npm run dev
```
