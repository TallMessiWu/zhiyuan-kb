from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Direction, KnowledgeGap, RefKind
from ..schemas import CodeRefIn, GapDraft, GapDraftOut, GapOut
from ..services import ai, indexing
from ..services.search import run_search
from .assets import USER_ID_MAX

router = APIRouter()

# 送入底稿 prompt 的检索上下文：条数与每条正文截断
DRAFT_CONTEXT_ASSETS = 3
DRAFT_CONTEXT_CHARS = 2000


def load_gaps(db: Session, *, limit: int = 20) -> list[KnowledgeGap]:
    """未解决的缺口：open 排在 claimed 前，再按被问次数、最近时间排序。
    首页与缺口列表共用，排序口径只有这一处。"""
    order_by_state = case((KnowledgeGap.status == "open", 0), else_=1)
    return list(db.scalars(
        select(KnowledgeGap)
        .where(KnowledgeGap.status != "resolved")
        .order_by(order_by_state, KnowledgeGap.hit_count.desc(), KnowledgeGap.last_at.desc())
        .limit(limit)
    ).all())


@router.get("/gaps", response_model=list[GapOut])
def list_gaps(limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)):
    """知识缺口列表。写入路径是 POST /feedback/not-found（M3）。"""
    return [GapOut.model_validate(g) for g in load_gaps(db, limit=limit)]


@router.post("/gaps/{gap_id}/claim", response_model=GapOut)
def claim(gap_id: int, db: Session = Depends(get_db),
          x_user: str = Header(default="anonymous", max_length=USER_ID_MAX)):
    """认领缺口：status=claimed + 记认领人，让别人不再重复补同一份知识。

    认领是「我来写」的登记，不是「已经写了」—— 所以它只改缺口状态，不建任何资产。
    缺口关到 resolved 由 M5 的沉淀回链完成（resolved_asset_id）。

    TODO(M5)：认领后调 ai.draft_from_session 生成 DRAFT 底稿（现在那个函数还是骨架，
    原型的文案也只承诺「AI 将…生成草稿底稿」，没有当场产出资产）。
    """
    gap = db.get(KnowledgeGap, gap_id)
    if gap is None:
        raise HTTPException(404, detail=("NOT_FOUND", f"缺口 {gap_id} 不存在"))
    if gap.status == "resolved":
        raise HTTPException(409, detail=("GAP_RESOLVED", "该缺口已解决，无需认领"))
    if gap.status == "claimed" and gap.claimed_by != x_user:
        raise HTTPException(409, detail=("GAP_ALREADY_CLAIMED", f"该缺口已由 {gap.claimed_by} 认领"))

    gap.status = "claimed"
    gap.claimed_by = x_user
    db.commit()
    db.refresh(gap)
    return GapOut.model_validate(gap)


def _clean_draft(data: dict) -> GapDraft:
    """把 LLM 的 JSON 清洗成 GapDraft：非法 direction 回落 feature，超长/畸形字段丢弃。
    这是预填底稿不是发布 —— 宁可少填一格，不能因为 AI 输出畸形就 500。"""
    def _s(key: str, limit: int = 2000) -> str:
        v = data.get(key)
        return str(v).strip()[:limit] if isinstance(v, (str, int, float)) else ""

    def _list(key: str, limit: int = 8, item_len: int = 64) -> list[str]:
        v = data.get(key)
        if not isinstance(v, list):
            return []
        return [str(x).strip()[:item_len] for x in v if str(x).strip()][:limit]

    try:
        direction = Direction(str(data.get("direction")))
    except ValueError:
        direction = Direction.feature

    code_refs: list[CodeRefIn] = []
    raw_refs = data.get("code_refs")
    for row in (raw_refs if isinstance(raw_refs, list) else [])[:5]:
        if not isinstance(row, dict):
            continue
        path_or_key = str(row.get("path_or_key") or "").strip()[:400]
        if not path_or_key:
            continue
        code_refs.append(CodeRefIn(
            kind=RefKind.repo_path,
            repo=str(row.get("repo") or "").strip()[:200],
            path_or_key=path_or_key,
            note=str(row.get("note") or "").strip()[:300],
            watch=True,
        ))

    return GapDraft(
        title=_s("title", 300),
        problem=_s("problem"),
        env=_s("env", 500),
        conclusion=_s("conclusion", 4000),
        tags=_list("tags"),
        direction=direction,
        models=_list("models"),
        framework=_s("framework", 64),
        fw_version=_s("fw_version", 40),
        code_refs=code_refs,
    )


@router.post("/gaps/{gap_id}/draft", response_model=GapDraftOut)
def draft(gap_id: int, db: Session = Depends(get_db),
          x_user: str = Header(default="anonymous", max_length=USER_ID_MAX)):
    """认领人请求 AI 底稿：从缺口问句 + 相关检索上下文生成沉淀页预填内容（M5）。

    与 M3 的约定不冲突：认领（/claim）只登记、永不产出资产；这一步也只返回**预填建议**，
    不落库任何东西 —— 作者在沉淀页确认三项后走 POST /assets 发布为 DRAFT（带 gap_id 回链）。

    这里的检索是系统辅助，不落 SearchEvent —— 它不是用户的知识需求事件，落了会污染
    复用率分母（硬规则 5）。网关不可用返回 503 AI_UNAVAILABLE，认领状态不受影响。
    """
    gap = db.get(KnowledgeGap, gap_id)
    if gap is None:
        raise HTTPException(404, detail=("NOT_FOUND", f"缺口 {gap_id} 不存在"))
    if gap.status == "resolved":
        raise HTTPException(409, detail=("GAP_RESOLVED", "该缺口已解决，不需要底稿"))
    if gap.status != "claimed":
        raise HTTPException(409, detail=("GAP_NOT_CLAIMED", "先认领这个缺口，再生成底稿"))
    if gap.claimed_by != x_user:
        raise HTTPException(409, detail=("GAP_ALREADY_CLAIMED", f"该缺口已由 {gap.claimed_by} 认领"))

    outcome = run_search(db, q=gap.question, limit=DRAFT_CONTEXT_ASSETS)
    context = "\n\n".join(
        f"KA-{s.asset.id:03d}「{s.asset.title}」（{s.asset.status.value}）\n"
        f"{indexing.body_md_of(db, s.asset)[:DRAFT_CONTEXT_CHARS]}"
        for s in outcome.items
    )

    data = ai.draft_from_session(gap.question, context)
    if data is None:
        raise HTTPException(503, detail=(
            "AI_UNAVAILABLE",
            "AI 底稿暂不可用（LLM 网关不可达或输出异常）；认领仍然有效，可以直接在沉淀页手写。",
        ))

    return GapDraftOut(
        gap_id=gap.id,
        draft=_clean_draft(data),
        sources=[s.asset.id for s in outcome.items],
    )
