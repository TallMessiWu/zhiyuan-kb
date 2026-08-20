"""把 prototype/kms-prototype.html 里的 18 条示例资产导入数据库。

用法（先 alembic upgrade head）：
    python scripts/seed.py            # 空库导入
    python scripts/seed.py --reset    # 先清空业务表再导入

原型里的 ASSETS 是 JS 对象字面量（键不带引号），本脚本用一个「字符串感知」的转换器把它
变成 JSON 再解析 —— 不能用正则粗暴加引号，正文里有 "ValueError: No available memory" 这种
带冒号的字符串会被误伤。

导入时的三条硬约束（根 CLAUDE.md 规则 1/2/3）：
- 状态一律经 services.state_machine 推进，不直接赋值 asset.status；
- 每条流转都补一条证据（ValidationRecord / ReuseEvent / ReviewTask / AssetVersion）；
- DRAFT→VERIFIED 只用非作者证据，作者本人的验证记录不会被当成升级依据。
末尾有一致性自检：任何 DRAFT 资产都不允许存在「非作者 + success」的复用事件，
否则说明种子数据自身违反了状态机规则。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from datetime import datetime, timezone

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.db import Base, get_engine, get_sessionmaker
from app.models import (
    AssetFramework,
    AssetModel,
    AssetVersion,
    CodeReference,
    Direction,
    Framework,
    KnowledgeAsset,
    Model,
    RefKind,
    ReuseEvent,
    ReviewTask,
    Status,
    StatusTransition,
    Tier,
    Trigger,
    UserFeedback,
    ValidationRecord,
    VersionOrigin,
)
from app.services import state_machine

PROTOTYPE = pathlib.Path(__file__).resolve().parents[2] / "prototype" / "kms-prototype.html"

# 原型用中文名展示，库里存 ASCII 账号：X-User 是 HTTP 头，不能放非 ASCII。
# 中文名到账号的对应关系（展示名映射在 frontend/src/lib/users.ts）。
USER_IDS = {
    "王磊": "wanglei",
    "陈雨薇": "chenyuwei",
    "孙晓东": "sunxiaodong",
    "李昊": "lihao",
    "张启元": "zhangqiyuan",
}

DIRECTIONS = {"model": Direction.model, "chain": Direction.chain, "feature": Direction.feature}
TIERS = {"note": Tier.note, "shared": Tier.shared, "core": Tier.core}
STATUSES = {s.value: s for s in Status}

# REVIEW_META.trigger（中文）到状态机触发器的对应
REVIEW_TRIGGERS = {
    "代码变更": Trigger.code_change,
    "版本变更": Trigger.version_change,
    "人工反馈": Trigger.user_feedback,
}
PRIORITIES = {"高": 3, "中": 2, "低": 1}


# ---------- 原型数据提取 ----------

def _extract_literal(html: str, marker: str, open_ch: str, close_ch: str) -> str:
    start = html.index(marker) + len(marker)
    depth, i = 0, start
    while True:
        ch = html[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                break
        i += 1
    return html[start:i + 1]


def js_to_json(src: str) -> str:
    """给 JS 对象字面量的裸键补引号，字符串内部原样保留。"""
    out: list[str] = []
    last_significant = ""
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch == '"':                                   # 整段字符串原样拷贝（含 \" 转义）
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == '"':
                    break
                j += 1
            out.append(src[i:j + 1])
            last_significant = '"'
            i = j + 1
            continue
        m = re.match(r"([A-Za-z_$][\w$]*)\s*:", src[i:])
        if m and last_significant in "{,[":              # 只有处于「键位置」才补引号
            out.append(f'"{m.group(1)}":')
            last_significant = ":"
            i += m.end()
            continue
        out.append(ch)
        if not ch.isspace():
            last_significant = ch
        i += 1
    return "".join(out)


def load_prototype() -> tuple[list[dict], dict]:
    html = PROTOTYPE.read_text(encoding="utf-8")
    assets = json.loads(js_to_json(_extract_literal(html, "const ASSETS = ", "[", "]")))
    review_meta = json.loads(js_to_json(_extract_literal(html, "let REVIEW_META = ", "{", "}")))
    return assets, review_meta


# ---------- 小工具 ----------

def uid(display_name: str) -> str:
    return USER_IDS.get(display_name, display_name)


def dt(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def asset_pk(code: str) -> int:
    """KA-016 -> 16。主键沿用原型编号，详情页展示的 code 由 id 反推，两边对得上。"""
    return int(code.split("-")[1])


def to_markdown(body: list[dict]) -> str:
    """原型正文是 [{h, p}] 且 p 内嵌 <code>；转成 markdown 小节 + 行内代码。"""
    parts = []
    for sec in body:
        p = re.sub(r"<code>(.*?)</code>", r"`\1`", sec["p"], flags=re.DOTALL)
        p = re.sub(r"<[^>]+>", "", p)                   # 兜底去掉其它标签
        parts.append(f"## {sec['h']}\n\n{p}\n")
    return "\n".join(parts)


def split_issue_ref(ref: str) -> tuple[str, str]:
    """vllm-ascend#1523 -> ("vllm-ascend", "1523")。"""
    repo, _, num = ref.partition("#")
    return repo, num


# ---------- 导入 ----------

def _clear(db: Session) -> None:
    """按依赖顺序清空业务表（只在 --reset 时用）。"""
    for table in (StatusTransition, ReviewTask, ValidationRecord, ReuseEvent, UserFeedback,
                  CodeReference, AssetFramework, AssetModel):
        db.execute(delete(table))
    db.execute(delete(KnowledgeAsset).where(KnowledgeAsset.id.isnot(None)))
    for table in (AssetVersion, Framework, Model):
        db.execute(delete(table))
    db.commit()


def _framework(db: Session, name: str) -> Framework:
    fw = db.scalar(select(Framework).where(Framework.name == name))
    if fw is None:
        fw = Framework(name=name)
        db.add(fw)
        db.flush()
    return fw


def _model(db: Session, name: str) -> Model:
    m = db.scalar(select(Model).where(Model.name == name))
    if m is None:
        m = Model(name=name)
        db.add(m)
        db.flush()
    return m


def _versions(db: Session, raw: dict, asset: KnowledgeAsset) -> tuple[AssetVersion, AssetVersion]:
    """建版本链：v1 是首次发布，v2.. 来自 history；只有最新版本保留正文快照。返回 (v1, 最新版)。"""
    history = {int(h["ver"].lstrip("v")): h for h in raw["history"]}
    top = max([*history, 1])
    first: AssetVersion | None = None
    latest: AssetVersion | None = None
    for seq in range(1, top + 1):
        h = history.get(seq)
        version = AssetVersion(
            asset_id=asset.id,
            seq=seq,
            body_md=(
                to_markdown(raw["body"]) if seq == top
                else f"（原型示例数据未保留 v{seq} 的正文快照）"
            ),
            change_note=h["note"] if h else "首次发布",
            created_by=uid(h["by"]) if h else asset.author_id,
            created_from=VersionOrigin.author,
            created_at=dt(h["date"]) if h else dt(raw["created"]),
        )
        db.add(version)
        if seq == 1:
            first = version
        latest = version
    db.flush()
    return first, latest


def _code_refs(db: Session, raw: dict, asset_id: int) -> None:
    for c in raw["code"]:
        db.add(CodeReference(
            asset_id=asset_id, kind=RefKind.repo_path, repo=c["repo"],
            path_or_key=c["path"], note=c.get("note", ""), watch=True,
        ))
    for issue in raw["issues"]:
        repo, num = split_issue_ref(issue["id"])
        db.add(CodeReference(
            asset_id=asset_id, kind=RefKind.issue, repo=repo, ref_id=num,
            note=issue["t"], watch=False,
        ))


def _reuses(db: Session, raw: dict, asset: KnowledgeAsset, version_id: int) -> list[ReuseEvent]:
    events = []
    for r in raw["reuses"]:
        # 原型里注明「未回报」的那条不是成功证据，否则一条 DRAFT 资产会自相矛盾地
        # 带着「非作者成功复用」却停在 DRAFT。
        outcome = "partial" if "未回报" in r["task"] else "success"
        ev = ReuseEvent(
            asset_id=asset.id, version_id=version_id, user_id=uid(r["by"]),
            task_note=r["task"], outcome=outcome,
            fw_version_at_use=raw["fwVersion"], at=dt(r["date"]),
        )
        db.add(ev)
        events.append(ev)
    db.flush()
    return events


def _validations(db: Session, raw: dict, asset: KnowledgeAsset, version_id: int,
                 reuses: list[ReuseEvent]) -> list[ValidationRecord]:
    reuse_keys = {(e.user_id, e.at.date()) for e in reuses if e.outcome == "success"}
    records = []
    for v in raw["validations"]:
        validator = uid(v["by"])
        stale = v["result"] != "通过"
        kind = "manual_review" if stale or (validator, dt(v["date"]).date()) not in reuse_keys \
            else "reuse_success"
        rec = ValidationRecord(
            asset_id=asset.id, version_id=version_id, validator_id=validator,
            kind=kind, result="stale_confirm" if stale else "pass",
            note=v["note"], at=dt(v["date"]),
        )
        db.add(rec)
        records.append(rec)
    db.flush()
    return records


def _review_task(db: Session, asset: KnowledgeAsset, meta: dict) -> ReviewTask:
    task = ReviewTask(
        asset_id=asset.id,
        trigger=REVIEW_TRIGGERS[meta["trigger"]],
        trigger_detail=meta["triggerDetail"],
        diff_ref="\n".join(f"{d['t']}: {d['s']}" for d in meta.get("diff", [])),
        ai_impact_summary=meta.get("aiSummary", ""),
        priority=PRIORITIES.get(meta.get("priority", ""), 0),
        state="open",
        created_at=dt(meta["detectedAt"]),
    )
    db.add(task)
    db.flush()
    return task


def _promote_to_verified(db: Session, asset: KnowledgeAsset, validations: list[ValidationRecord],
                         reuses: list[ReuseEvent]) -> None:
    """用最早一条非作者验证把资产从 DRAFT 升到 VERIFIED（作者本人的记录会被状态机拒绝）。"""
    candidates = sorted(
        (v for v in validations if v.validator_id != asset.author_id and v.result == "pass"),
        key=lambda v: v.at,
    )
    if not candidates:
        raise SystemExit(f"{asset.title}: 目标状态需要 VERIFIED，但没有任何非作者验证记录")
    first = candidates[0]
    matching_reuse = next(
        (e for e in reuses if e.user_id == first.validator_id and e.at.date() == first.at.date()
         and e.outcome == "success"),
        None,
    )
    if matching_reuse is not None:
        state_machine.transition(
            db, asset, Status.VERIFIED, Trigger.nonauthor_reuse, actor=first.validator_id,
            evidence_type="reuse_event", evidence_id=matching_reuse.id, at=first.at,
            note=f"非作者（{first.validator_id}）成功复用并回报。",
        )
    else:
        state_machine.transition(
            db, asset, Status.VERIFIED, Trigger.manual_validation, actor=first.validator_id,
            evidence_type="validation", evidence_id=first.id, at=first.at,
            note=f"非作者（{first.validator_id}）人工验证通过。",
        )


def seed_asset(db: Session, raw: dict, review_meta: dict) -> KnowledgeAsset:
    target = STATUSES[raw["status"]]
    author = uid(raw["author"])
    asset = KnowledgeAsset(
        id=asset_pk(raw["id"]),
        title=raw["title"],
        direction=DIRECTIONS[raw["type"]],
        tier=TIERS[raw["tier"]],
        summary=raw["summary"],
        tags=raw["tags"],
        author_id=author,
        source="wiki" if raw["tier"] == "shared" and target == Status.ARCHIVED else "manual",
        source_ref="",
        env_note="" if raw["env"] in ("—", "") else raw["env"],
        reuse_count=raw["reuseCount"],
    )
    db.add(asset)
    db.flush()

    first, latest = _versions(db, raw, asset)
    asset.current_version_id = latest.id
    _code_refs(db, raw, asset.id)

    fw = _framework(db, raw["framework"])
    db.add(AssetFramework(
        asset_id=asset.id, framework_id=fw.id, verified_on=raw["fwVersion"][:40],
    ))
    for name in dict.fromkeys(raw["models"]):
        db.add(AssetModel(asset_id=asset.id, model_id=_model(db, name).id))

    reuses = _reuses(db, raw, asset, latest.id)
    validations = _validations(db, raw, asset, latest.id, reuses)

    # ---- 状态流转：一律走状态机，逐段补齐证据 ----
    state_machine.create_as_draft(
        db, asset, actor=author, evidence_type="asset_version", evidence_id=first.id,
        at=dt(raw["created"]), note="发布为 DRAFT",
    )
    meta = review_meta.get(raw["id"])

    if target in (Status.VERIFIED, Status.REVIEW_DUE):
        _promote_to_verified(db, asset, validations, reuses)

    if target is Status.REVIEW_DUE:
        task = _review_task(db, asset, meta)
        state_machine.transition(
            db, asset, Status.REVIEW_DUE, task.trigger,
            actor="system" if task.trigger is not Trigger.user_feedback else "sunxiaodong",
            evidence_type="review_task", evidence_id=task.id, at=task.created_at,
            note=raw["statusReason"],
        )
    elif target is Status.STALE:
        # 原型没给这条的复核任务，按其失效确认记录补一个，让流转有据可查
        confirm = next(v for v in validations if v.result == "stale_confirm")
        task = _review_task(db, asset, {
            "trigger": "版本变更", "triggerDetail": raw["statusReason"],
            "detectedAt": confirm.at.strftime("%Y-%m-%d"), "priority": "中",
            "aiSummary": "", "diff": [],
        })
        state_machine.transition(
            db, asset, Status.REVIEW_DUE, Trigger.version_change, actor="system",
            evidence_type="review_task", evidence_id=task.id, at=confirm.at,
            note="v1 引擎成为默认，本文基于 v0 引擎撰写，转入复核。",
        )
        state_machine.transition(
            db, asset, Status.STALE, Trigger.review_stale, actor=confirm.validator_id,
            evidence_type="validation", evidence_id=confirm.id, at=confirm.at,
            note=raw["statusReason"],
        )
    elif target is Status.ARCHIVED:
        # design.md §4：归档的证据就是流水的 note（填替代资产回链），没有独立证据表
        state_machine.transition(
            db, asset, Status.ARCHIVED, Trigger.review_replace, actor=author,
            at=dt(raw["updated"]), note=raw["statusReason"],
        )

    if asset.status is not target:
        raise SystemExit(f"{raw['id']}: 期望 {target.value}，实际 {asset.status.value}")

    # statusReason 与时间戳最后写：前面的流转会覆盖 status_reason，
    # 而 updated_at 带 onupdate，只有显式赋值才不会被刷成当前时间。
    asset.status_reason = raw["statusReason"]
    asset.created_at = dt(raw["created"])
    asset.updated_at = dt(raw["updated"])
    db.flush()
    return asset


def check_consistency(db: Session) -> None:
    """自检：DRAFT 资产不允许存在「非作者 + success」的复用事件（否则它早该是 VERIFIED）。"""
    rows = db.execute(
        select(KnowledgeAsset.id, ReuseEvent.user_id)
        .join(ReuseEvent, ReuseEvent.asset_id == KnowledgeAsset.id)
        .where(KnowledgeAsset.status == Status.DRAFT,
               ReuseEvent.outcome == "success",
               ReuseEvent.user_id != KnowledgeAsset.author_id)
    ).all()
    if rows:
        raise SystemExit(f"一致性自检失败：DRAFT 资产存在非作者成功复用 {rows}")


def sync_pk_sequence(db: Session) -> None:
    """种子写了显式主键；PostgreSQL 的序列要跟上，否则后续 POST /assets 会撞主键。"""
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(text(
        "SELECT setval(pg_get_serial_sequence('knowledge_asset', 'id'), "
        "COALESCE((SELECT MAX(id) FROM knowledge_asset), 1))"
    ))
    db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="导入原型示例数据")
    parser.add_argument("--reset", action="store_true", help="导入前清空业务表")
    parser.add_argument("--create-all", action="store_true",
                        help="直接按模型建表（本地快速起库用；正式路径是 alembic upgrade head）")
    args = parser.parse_args()

    if args.create_all:
        Base.metadata.create_all(get_engine())

    db = get_sessionmaker()()
    try:
        if args.reset:
            _clear(db)
        elif db.scalar(select(KnowledgeAsset.id).limit(1)) is not None:
            print("库里已有资产，加 --reset 覆盖导入。", file=sys.stderr)
            return 1

        assets, review_meta = load_prototype()
        for raw in assets:
            seed_asset(db, raw, review_meta)
        db.commit()

        check_consistency(db)
        sync_pk_sequence(db)

        by_status: dict[str, int] = {}
        for a in db.scalars(select(KnowledgeAsset)).all():
            by_status[a.status.value] = by_status.get(a.status.value, 0) + 1
        print(f"导入 {len(assets)} 条资产：" + "，".join(f"{k} {v}" for k, v in sorted(by_status.items())))
        print(f"版本 {len(db.scalars(select(AssetVersion.id)).all())} 条，"
              f"流水 {len(db.scalars(select(StatusTransition.id)).all())} 条，"
              f"验证 {len(db.scalars(select(ValidationRecord.id)).all())} 条，"
              f"复用 {len(db.scalars(select(ReuseEvent.id)).all())} 条，"
              f"代码引用 {len(db.scalars(select(CodeReference.id)).all())} 条，"
              f"复核任务 {len(db.scalars(select(ReviewTask.id)).all())} 条")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
