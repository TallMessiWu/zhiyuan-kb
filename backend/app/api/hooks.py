"""GitHub/GitLab webhook —— 自动更新的入口（M4，docs/design.md §7）。

流程：签名校验 → 事件归一化（push / tag push / PR merged，其余 200 忽略）→
匹配 CodeReference(watch=true) → 按资产聚合 → review_queue.open_task（自带 24h 去抖，
返回 (task, created)，合并时拿已存在任务 id 回执）→ AI 影响摘要/更新草稿（可降级）。

两条边界约定：
- 不相关事件也返回 200（handled=False）：非 2xx 会被 Git 平台当失败重试，
  同一个事件反复砸过来只会制造重复日志。
- STALE/ARCHIVED 资产不建任务：死状态不进复核队列（与三键反馈的 409 同语义，
  webhook 场景没有「调用方」要教育，跳过即可）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import CodeReference, KnowledgeAsset, RefKind, Status, Trigger
from ..schemas import HookAck, HookTaskOut
from ..services import review_queue

router = APIRouter()


@dataclass
class GitChange:
    """两平台事件归一化后的变更描述。"""

    platform: str        # github / gitlab
    kind: str            # push / tag / pr
    repo: str            # owner/name（GitLab 是 path_with_namespace）
    ref: str             # 分支或 tag 名
    head_sha: str
    files: list[str]     # 变更文件路径；PR 事件的 payload 拿不到文件列表，为空
    texts: list[str]     # commit message / PR 标题正文 —— config_key 与 issue/pr 的匹配面
    link: str            # compare / PR URL，落 ReviewTask.diff_ref
    summary: str         # 一句话事件描述，落 trigger_detail


def _verify_signature(raw: bytes, github_sig: str | None, gitlab_token: str | None) -> None:
    """GitHub 是 HMAC-SHA256（X-Hub-Signature-256），GitLab 是明文口令（X-Gitlab-Token）。"""
    secret = settings.webhook_secret
    if github_sig:
        expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, github_sig):
            raise HTTPException(401, detail=("INVALID_SIGNATURE", "X-Hub-Signature-256 校验失败"))
        return
    if gitlab_token:
        if not hmac.compare_digest(secret.encode(), gitlab_token.encode()):
            raise HTTPException(401, detail=("INVALID_SIGNATURE", "X-Gitlab-Token 校验失败"))
        return
    raise HTTPException(401, detail=(
        "MISSING_SIGNATURE", "缺少 X-Hub-Signature-256（GitHub）或 X-Gitlab-Token（GitLab）"))


def _branch(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _commit_fields(commits: list[dict]) -> tuple[list[str], list[str]]:
    files = sorted({
        f for c in commits
        for key in ("added", "modified", "removed")
        for f in c.get(key) or []
    })
    texts = [c.get("message") or "" for c in commits]
    return files, texts


def _push_summary(platform: str, repo: str, branch: str, texts: list[str]) -> str:
    heads = "；".join(t.splitlines()[0] for t in texts if t)[:200]
    return f"{platform} push {repo}@{branch}（{len(texts)} 个提交：{heads}）"


def _parse_github(event: str, p: dict) -> GitChange | None:
    if event == "push":
        ref = p.get("ref") or ""
        kind = "tag" if ref.startswith("refs/tags/") else "push"
        files, texts = _commit_fields(p.get("commits") or [])
        repo = (p.get("repository") or {}).get("full_name") or ""
        name = _branch(ref)
        if kind == "tag":
            summary = f"GitHub {repo} 发布 tag {name}"
            texts.append(name)
        else:
            summary = _push_summary("GitHub", repo, name, texts)
        return GitChange("github", kind, repo, name, p.get("after") or "",
                         files, texts, p.get("compare") or "", summary)
    if event == "pull_request":
        pr = p.get("pull_request") or {}
        if p.get("action") != "closed" or not pr.get("merged"):
            return None
        repo = (((pr.get("base") or {}).get("repo") or {}).get("full_name")
                or (p.get("repository") or {}).get("full_name") or "")
        number, title = p.get("number"), pr.get("title") or ""
        return GitChange(
            "github", "pr", repo, "", pr.get("merge_commit_sha") or "", [],
            [title, pr.get("body") or "", f"#{number}"], pr.get("html_url") or "",
            f"GitHub PR #{number} 合入 {repo}：{title}",
        )
    return None


def _parse_gitlab(p: dict) -> GitChange | None:
    kind_raw = p.get("object_kind") or ""
    project = p.get("project") or {}
    repo = project.get("path_with_namespace") or ""
    if kind_raw in ("push", "tag_push"):
        kind = "tag" if kind_raw == "tag_push" else "push"
        files, texts = _commit_fields(p.get("commits") or [])
        name = _branch(p.get("ref") or "")
        head = p.get("checkout_sha") or p.get("after") or ""
        link = ""
        if project.get("web_url") and p.get("before") and p.get("after"):
            link = f"{project['web_url']}/-/compare/{p['before']}...{p['after']}"
        if kind == "tag":
            summary = f"GitLab {repo} 发布 tag {name}"
            texts.append(name)
        else:
            summary = _push_summary("GitLab", repo, name, texts)
        return GitChange("gitlab", kind, repo, name, head, files, texts, link, summary)
    if kind_raw == "merge_request":
        attrs = p.get("object_attributes") or {}
        if attrs.get("action") != "merge" and attrs.get("state") != "merged":
            return None
        iid, title = attrs.get("iid"), attrs.get("title") or ""
        return GitChange(
            "gitlab", "pr", repo, "", (attrs.get("last_commit") or {}).get("id") or "", [],
            [title, attrs.get("description") or "", f"!{iid}"], attrs.get("url") or "",
            f"GitLab MR !{iid} 合入 {repo}：{title}",
        )
    return None


def _path_hit(changed_file: str, watched_path: str) -> bool:
    """watch 的是目录或具体文件，变更列表里永远是具体文件：相等或落在目录下都算命中。"""
    path = watched_path.rstrip("/")
    return bool(path) and (changed_file == path or changed_file.startswith(path + "/"))


def _match_refs(refs: list[CodeReference], change: GitChange) -> list[CodeReference]:
    hits = []
    text_blob = "\n".join(change.texts)
    for ref in refs:
        if ref.repo and change.repo and ref.repo.lower() != change.repo.lower():
            continue
        if change.kind == "tag":
            # tag/基线升级是仓库级事件，按 repo 批量触发（design.md §7「基线升级一次批量触发相关资产」）。
            # 要求 ref.repo 非空且相等：空 repo 的引用不参与批量，否则任何 tag 都会全库扫射。
            if ref.repo and change.repo and ref.repo.lower() == change.repo.lower():
                hits.append(ref)
            continue
        key = ref.path_or_key
        if ref.kind is RefKind.repo_path:
            # PR 事件拿不到文件列表，退化为在标题/正文里找路径字符串
            if any(_path_hit(f, key) for f in change.files) or (not change.files and key and key in text_blob):
                hits.append(ref)
        elif ref.kind is RefKind.config_key:
            if key and (key in text_blob or any(key in f for f in change.files)):
                hits.append(ref)
        elif ref.ref_id and ref.ref_id in text_blob:   # issue / pr 引用按编号匹配
            hits.append(ref)
    return hits


def _change_text(change: GitChange) -> str:
    """给 AI 的变更描述。webhook payload 没有 diff 正文，能给的是文件清单与提交说明 ——
    「纯格式化 diff 的 AI 预判抑制」（design.md §7）需要真 diff，等接了 Git API 拉取再做。"""
    parts = [change.summary]
    if change.files:
        parts.append("变更文件：\n" + "\n".join(change.files[:50]))
    joined = "\n".join(t for t in change.texts if t).strip()
    if joined:
        parts.append("提交/合入说明：\n" + joined[:1500])
    return "\n\n".join(parts)


def _process(db: Session, change: GitChange) -> HookAck:
    refs = db.scalars(select(CodeReference).where(CodeReference.watch.is_(True))).all()
    hits = _match_refs(refs, change)
    by_asset: dict[int, list[CodeReference]] = {}
    for ref in hits:
        by_asset.setdefault(ref.asset_id, []).append(ref)

    trigger = Trigger.version_change if change.kind == "tag" else Trigger.code_change
    change_text = _change_text(change)
    tasks: list[HookTaskOut] = []
    for asset_id, asset_refs in sorted(by_asset.items()):
        asset = db.get(KnowledgeAsset, asset_id)
        if asset.status in (Status.STALE, Status.ARCHIVED):
            continue
        anchors = "、".join(dict.fromkeys(r.path_or_key or r.ref_id for r in asset_refs))
        task, created = review_queue.open_task(
            db, asset, trigger,
            trigger_detail=f"{change.summary}，命中关注点：{anchors}",
            actor="system", diff_ref=change.link[:400],
        )
        review_queue.attach_ai_review(db, asset, task, change_text)
        for ref in asset_refs:
            if change.head_sha:
                ref.last_seen_sha = change.head_sha[:64]
        tasks.append(HookTaskOut(review_task_id=task.id, asset_id=asset_id, created=created))

    db.commit()
    return HookAck(handled=True, event=change.kind, repo=change.repo,
                   matched_refs=len(hits), tasks=tasks)


@router.post("/hooks/git", response_model=HookAck)
async def git_webhook(request: Request, db: Session = Depends(get_db)):
    """接收 push / tag push / PR(MR) merged。签名校验失败 401；无关事件 200 handled=False。"""
    raw = await request.body()
    _verify_signature(raw, request.headers.get("X-Hub-Signature-256"),
                      request.headers.get("X-Gitlab-Token"))
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(422, detail=("VALIDATION_ERROR", "请求体不是合法 JSON")) from None

    gh_event = request.headers.get("X-GitHub-Event")
    if gh_event:
        change = _parse_github(gh_event, payload)
        reason = f"忽略 GitHub 事件 {gh_event}（只处理 push / tag push / PR merged）"
    elif payload.get("object_kind"):
        change = _parse_gitlab(payload)
        reason = f"忽略 GitLab 事件 {payload.get('object_kind')}（只处理 push / tag push / MR merged）"
    else:
        change, reason = None, "无法识别的事件来源（缺 X-GitHub-Event 头且无 object_kind）"
    if change is None:
        return HookAck(handled=False, reason=reason)

    # 匹配与 AI 回填是同步阻塞逻辑（含最多两次网关往返），丢线程池跑，别占事件循环
    return await run_in_threadpool(_process, db, change)
