from fastapi import FastAPI

from .api import assets, dashboard, feedback, gaps, hooks, review, search

app = FastAPI(
    title="知源 zhiyuan-kb",
    description="团队推理知识管理与智能搜索系统 — API 约定见 docs/api-contract.md",
    version="0.1.0",
)

API = "/api/v1"
app.include_router(search.router, prefix=API, tags=["search"])
app.include_router(assets.router, prefix=API, tags=["assets"])
app.include_router(feedback.router, prefix=API, tags=["feedback"])
app.include_router(review.router, prefix=API, tags=["review"])
app.include_router(gaps.router, prefix=API, tags=["gaps"])
app.include_router(dashboard.router, prefix=API, tags=["dashboard"])
app.include_router(hooks.router, prefix=API, tags=["hooks"])


@app.get("/healthz")
def healthz():
    return {"ok": True}
