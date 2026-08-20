"""Pydantic 模型 — 与 docs/api-contract.md 对应。M1 起逐个补全。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .models import Direction, Status, Tier


class CodeRefIn(BaseModel):
    kind: str = "repo_path"
    repo: str = ""
    path_or_key: str = ""
    ref_id: str = ""
    note: str = ""
    watch: bool = True


class AssetCreate(BaseModel):
    """沉淀页发布 DRAFT：三项确认（问题=title、环境、结论）+ AI 带出的元数据。"""

    title: str = Field(max_length=300)
    direction: Direction
    body_md: str                      # 问题/环境/结论 三节 markdown
    models: list[str] = []
    framework: str = "vllm-ascend"
    fw_version: str = ""
    env_note: str = ""
    tags: list[str] = []
    source: str = "ai_session"
    source_ref: str = ""
    code_refs: list[CodeRefIn] = []


class ScorePartOut(BaseModel):
    label: str
    value: float


class ScoreOut(BaseModel):
    total: float
    parts: list[ScorePartOut]


class AssetBrief(BaseModel):
    id: int
    title: str
    direction: Direction
    tier: Tier
    status: Status
    summary: str
    tags: list[str]
    author_id: str
    reuse_count: int
    updated_at: datetime

    model_config = {"from_attributes": True}


class SearchItem(BaseModel):
    asset: AssetBrief
    score: ScoreOut


class SearchResponse(BaseModel):
    items: list[SearchItem]
    search_event_id: int
    hist: bool = False


class UsefulIn(BaseModel):
    asset_id: int
    task_note: str = ""
    search_event_id: int | None = None


class StaleIn(BaseModel):
    asset_id: int
    note: str = ""


class NotFoundIn(BaseModel):
    query: str
    search_event_id: int | None = None


class ReviewResolveIn(BaseModel):
    action: str  # confirm / accept_draft / stale / archive
    note: str = ""
    replaced_by: int | None = None


class AskIn(BaseModel):
    question: str


class Citation(BaseModel):
    asset_id: int
    fragment: str
    status: Status
    fw_version: str
    updated_at: datetime


class AskResponse(BaseModel):
    answer_md: str
    citations: list[Citation] = []
    risks: list[str] = []
    conflict: dict | None = None
    not_found: bool = False
