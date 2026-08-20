from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db

router = APIRouter()


@router.post("/hooks/git")
async def git_webhook(request: Request, db: Session = Depends(get_db)):
    """GitHub/GitLab webhook。TODO(M4)：
    1. 校验签名（ZY_WEBHOOK_SECRET）
    2. 解析变更文件列表 / PR 标题 / tag
    3. 匹配 CodeReference(watch=True)（路径前缀匹配 + 配置项 grep）
    4. review_queue.open_task(code_change/version_change)（自带 24h 去抖）
    5. 异步 ai.impact_summary + ai.update_draft 回填任务
    纯格式化/注释 diff 由 AI 预判抑制，抑制记录落 StatusTransition.note 供抽查。
    """
    raise NotImplementedError("M4")
