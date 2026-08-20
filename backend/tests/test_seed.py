"""种子脚本测试 — 原型 18 条资产能被解析并按状态机规则完整落库。

这组用例同时在守两件事：原型 JS 字面量解析没漂，以及种子数据本身不违反五态规则。
"""
import pytest
from sqlalchemy import select

from app.models import (
    AssetVersion,
    CodeReference,
    KnowledgeAsset,
    RefKind,
    ReuseEvent,
    ReviewTask,
    Status,
    StatusTransition,
    Trigger,
    ValidationRecord,
)
from scripts.seed import (
    _clear,
    check_consistency,
    js_to_json,
    load_prototype,
    seed_asset,
    to_markdown,
)

EXPECTED_BY_STATUS = {
    Status.VERIFIED: 8,
    Status.DRAFT: 5,
    Status.REVIEW_DUE: 3,
    Status.STALE: 1,
    Status.ARCHIVED: 1,
}


@pytest.fixture(scope="module")
def prototype():
    return load_prototype()


@pytest.fixture()
def seeded(session_factory, prototype):
    assets, review_meta = prototype
    db = session_factory()
    for raw in assets:
        seed_asset(db, raw, review_meta)
    db.commit()
    yield db
    db.close()


def test_prototype_yields_18_assets(prototype):
    assets, review_meta = prototype
    assert len(assets) == 18
    assert len({a["id"] for a in assets}) == 18
    assert set(review_meta) == {"KA-003", "KA-010", "KA-018"}


def test_js_to_json_keeps_colons_inside_strings():
    """正文里有 "ValueError: No available memory" 这种带冒号的串，不能被当成键。"""
    src = '[{id:"x", p:"报 ValueError: No available memory", n:1}]'
    assert js_to_json(src) == '[{"id":"x", "p":"报 ValueError: No available memory", "n":1}]'


def test_body_html_becomes_markdown():
    md = to_markdown([{"h": "结论", "p": "初始化 HCCL 时报 <code>EI0002</code>。"}])
    assert md == "## 结论\n\n初始化 HCCL 时报 `EI0002`。\n"


def test_status_distribution_matches_prototype(seeded):
    counts = {}
    for a in seeded.scalars(select(KnowledgeAsset)).all():
        counts[a.status] = counts.get(a.status, 0) + 1
    assert counts == EXPECTED_BY_STATUS


def test_primary_key_follows_prototype_code(seeded):
    ka016 = seeded.get(KnowledgeAsset, 16)
    assert ka016.title.startswith("vLLM Ascend 0.9.x 环境矩阵")
    assert ka016.author_id == "zhangqiyuan"          # 中文名映射成 ASCII 账号
    assert ka016.reuse_count == 23
    assert ka016.env_note == "CANN 8.2.RC1 · torch_npu 2.5.1.post1"
    assert ka016.created_at.date().isoformat() == "2026-04-03"
    assert ka016.updated_at.date().isoformat() == "2026-08-11"   # 未被 onupdate 刷成当前时间


def test_every_asset_starts_with_a_draft_transition_pointing_at_v1(seeded):
    for asset in seeded.scalars(select(KnowledgeAsset)).all():
        rows = seeded.scalars(
            select(StatusTransition).where(StatusTransition.asset_id == asset.id)
            .order_by(StatusTransition.at, StatusTransition.id)
        ).all()
        assert rows, f"{asset.id} 没有任何流水"
        first = rows[0]
        assert first.from_status is None and first.to_status is Status.DRAFT
        assert first.trigger is Trigger.auto_create
        v1 = seeded.scalar(
            select(AssetVersion).where(AssetVersion.asset_id == asset.id, AssetVersion.seq == 1)
        )
        assert first.evidence_type == "asset_version" and first.evidence_id == v1.id


def test_promotion_to_verified_always_has_nonauthor_evidence(seeded):
    rows = seeded.scalars(
        select(StatusTransition).where(StatusTransition.to_status == Status.VERIFIED)
    ).all()
    assert len(rows) == 11          # 8 个 VERIFIED + 3 个先升 VERIFIED 再转 REVIEW_DUE
    for row in rows:
        asset = seeded.get(KnowledgeAsset, row.asset_id)
        assert row.actor != asset.author_id, f"{asset.id} 的升级证据来自作者本人"
        assert row.evidence_type in {"reuse_event", "validation"}
        assert row.evidence_id is not None


def test_review_due_and_stale_transitions_have_review_task_evidence(seeded):
    for status in (Status.REVIEW_DUE, Status.STALE):
        for asset in seeded.scalars(select(KnowledgeAsset).where(KnowledgeAsset.status == status)).all():
            rows = seeded.scalars(
                select(StatusTransition).where(StatusTransition.asset_id == asset.id)
            ).all()
            review = next(r for r in rows if r.to_status is Status.REVIEW_DUE)
            assert review.evidence_type == "review_task"
            assert seeded.get(ReviewTask, review.evidence_id) is not None


def test_archived_transition_carries_replacement_note(seeded):
    asset = seeded.get(KnowledgeAsset, 12)
    assert asset.status is Status.ARCHIVED
    row = seeded.scalar(
        select(StatusTransition).where(StatusTransition.asset_id == 12,
                                       StatusTransition.to_status == Status.ARCHIVED)
    )
    assert row.trigger is Trigger.review_replace
    assert "KA-016" in row.note          # design.md §4：归档的证据就是 note 里的替代回链


def test_versions_and_current_version_are_linked(seeded):
    ka016 = seeded.get(KnowledgeAsset, 16)
    versions = seeded.scalars(
        select(AssetVersion).where(AssetVersion.asset_id == 16).order_by(AssetVersion.seq)
    ).all()
    assert [v.seq for v in versions] == [1, 2, 3, 4]
    assert ka016.current_version_id == versions[-1].id
    body = versions[-1].body_md
    assert body.startswith("## 结论（兼容矩阵）")
    assert "`EI0002`" in body and "<code>" not in body


def test_code_refs_split_repo_paths_from_issues(seeded):
    refs = seeded.scalars(select(CodeReference).where(CodeReference.asset_id == 16)).all()
    paths = [r for r in refs if r.kind is RefKind.repo_path]
    issues = [r for r in refs if r.kind is RefKind.issue]
    assert len(paths) == 2 and all(r.watch for r in paths)
    assert len(issues) == 1
    assert (issues[0].repo, issues[0].ref_id) == ("vllm-ascend", "1523")


def test_unreported_reuse_is_not_recorded_as_success(seeded):
    """KA-007 有一条非作者复用但注明「未回报」，若记成 success 就与它停在 DRAFT 自相矛盾。"""
    ka007 = seeded.get(KnowledgeAsset, 7)
    assert ka007.status is Status.DRAFT
    reuse = seeded.scalar(select(ReuseEvent).where(ReuseEvent.asset_id == 7))
    assert reuse.user_id != ka007.author_id
    assert reuse.outcome == "partial"


def test_consistency_check_passes(seeded):
    check_consistency(seeded)          # DRAFT 资产不得存在非作者成功复用


def test_validation_records_are_attached(seeded):
    total = len(seeded.scalars(select(ValidationRecord)).all())
    assert total == 15
    stale = seeded.scalars(select(ValidationRecord).where(ValidationRecord.result == "stale_confirm")).all()
    assert len(stale) == 1 and stale[0].asset_id == 8


def test_reset_clears_everything_under_foreign_key_enforcement(seeded):
    """--reset 的删除顺序必须扛得住外键强制（conftest 已开 PRAGMA foreign_keys=ON）。

    knowledge_asset 与 asset_version 互相引用，顺序错了在 PostgreSQL 上就是 ForeignKeyViolation。
    """
    _clear(seeded)
    assert seeded.scalars(select(KnowledgeAsset)).all() == []
    assert seeded.scalars(select(AssetVersion)).all() == []
    assert seeded.scalars(select(StatusTransition)).all() == []
    assert seeded.scalars(select(ReviewTask)).all() == []
