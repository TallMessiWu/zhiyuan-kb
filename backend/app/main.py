from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import assets, dashboard, feedback, gaps, hooks, review, search
from .services.state_machine import InvalidTransition

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


# ---------- 统一错误形状：{"error": {"code", "message"}}（docs/api-contract.md 末节） ----------

def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


@app.exception_handler(InvalidTransition)
def _invalid_transition(request: Request, exc: InvalidTransition) -> JSONResponse:
    """状态机拒绝的流转一律 409 INVALID_TRANSITION。"""
    return _error(409, "INVALID_TRANSITION", str(exc))


@app.exception_handler(StarletteHTTPException)
def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # 路由层用 HTTPException(status_code, detail=(code, message)) 传递业务码；未传则按状态码兜底。
    detail = exc.detail
    if isinstance(detail, (tuple, list)) and len(detail) == 2:
        code, message = detail
    else:
        code = {404: "NOT_FOUND", 401: "UNAUTHORIZED", 403: "FORBIDDEN"}.get(exc.status_code, "ERROR")
        message = str(detail)
    return _error(exc.status_code, str(code), str(message))


@app.exception_handler(RequestValidationError)
def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _error(422, "VALIDATION_ERROR", str(exc.errors()))


@app.get("/healthz")
def healthz():
    return {"ok": True}
