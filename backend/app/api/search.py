from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import AskIn, AskResponse, SearchResponse

router = APIRouter()


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = "",
    direction: str | None = None,
    model: str | None = None,
    framework: str | None = None,
    status: str | None = None,
    hist: bool = False,
    db: Session = Depends(get_db),
    x_user: str = Header(default="anonymous"),
):
    """混合检索。TODO(M2)：
    1. bm25_recall + vector_recall（hist=False 时召回层排除 STALE/ARCHIVED）
    2. rrf_fuse -> rel；services.search.rerank 逐条计算分项得分
    3. 落 SearchEvent（query/filters/result_ids/user），返回 search_event_id
    """
    raise NotImplementedError("M2")


@router.post("/ask", response_model=AskResponse)
def ask(body: AskIn, db: Session = Depends(get_db), x_user: str = Header(default="anonymous")):
    """RAG 问答。TODO(M5)，硬性规则见 docs/design.md §6：
    引用必须带来源/状态/版本；无据返回 not_found=True 与固定话术；
    STALE/ARCHIVED 不入上下文；冲突并列展示（conflict 字段）；REVIEW_DUE 加 risks。
    """
    raise NotImplementedError("M5")
