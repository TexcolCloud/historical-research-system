import json

from sqlalchemy import select, update
from test_review import seed

from hrs_platform import schema as db
from hrs_platform.contracts import ReviewDraft
from hrs_platform.review import Review, digest
from hrs_platform.review_repair import (
    apply_verified_page,
    machine_decision,
    refresh_unresolved,
    repair_scopes,
)


def prepared(platform, text, parts, concerns):
    settings, engine = platform
    run, ids = seed(engine)
    review = Review(settings, engine)

    def saved(value):
        return review.objects.put_bytes(json.dumps(value, ensure_ascii=False).encode())

    bundle = {
        "manifest": {
            "candidate_markdown": "document.md",
            "page_boundaries": "boundaries.json",
            "pages": [{"page": 1, "image": "pages/1.png"}],
        },
        "files": {
            "document.md": review.objects.put_bytes(text.encode()),
            "boundaries.json": saved({"pages": [{"page": 1, "start": 0, "end": len(text)}]}),
            "completion.json": saved(
                {
                    "pages": [
                        {
                            "page": 1,
                            "concerns": concerns,
                            "receipts": [{"verdict": {"review_state": "completed"}}],
                        }
                    ]
                }
            ),
        },
    }
    with engine.begin() as conn:
        conn.execute(update(db.runs).where(db.runs.c.id == run).values(conversion=saved(bundle)))
        for identity, part in zip(ids, parts, strict=True):
            body = {
                "text": part,
                "start": text.index(part),
                "end": text.index(part) + len(part),
                "pages": [1],
                "images": ["pages/1.png"],
                "kind": "paragraph",
                "reasons": ["legacy"],
            }
            conn.execute(
                update(db.review_issues)
                .where(db.review_issues.c.id == identity)
                .values(page=1, content=saved(body), text_sha256=digest(part))
            )
    return review, run, ids


def test_scope_repair_closes_only_unrelated_paragraph_with_machine_audit(platform):
    review, run, ids = prepared(
        platform,
        "正文甲无误。\n\n乙--丙有疑问。",
        ["正文甲无误。", "乙--丙有疑问。"],
        [{"kind": "claim", "excerpt": "乙——丙有疑问。"}],
    )
    assert repair_scopes(review, run)["unrelated_closed"] == 1
    assert review.get(ids[0])["state"] == "approved"
    assert review.get(ids[1])["state"] == "pending"
    with review.engine.connect() as conn:
        receipt = conn.scalar(
            select(db.review_decisions.c.receipt).where(db.review_decisions.c.issue_id == ids[0])
        )
    assert receipt["machine_review"] and receipt["human_review"] is False
    assert receipt["reviewer"] == "local-qwen-machine"
    assert repair_scopes(review, run)["unrelated_closed"] == 0


def test_figure_consolidation_keeps_original_scopes_and_one_pending_item(platform):
    text = "Image\n\n![Image](map.png)\n图1 行动图"
    review, run, ids = prepared(platform, text, ["Image", "图1 行动图"], [])
    assert repair_scopes(review, run)["figure_items_consolidated"] == 1
    pending = review.list(run)
    assert len(pending) == review.status(run)["pending_count"] == 1
    issue = review.get(pending[0]["id"])
    assert issue["kind"] == "figure" and issue["text"] == text
    assert len(issue["previous_scopes"]) == 2
    assert {r["state"] for r in review.list(run, pending=False)} == {"pending", "superseded"}


def test_machine_cannot_overwrite_saved_draft_or_stale_human_decision(platform):
    review, run, ids = prepared(platform, "甲段。\n乙段。", ["甲段。", "乙段。"], [])
    stale = review.get(ids[0])
    review.save_draft(ids[0], ReviewDraft(expected_revision=1, text="人工草稿。"))
    assert not machine_decision(review, stale, "机器修正。", {"kind": "test"})
    assert review.get(ids[0])["draft_text"] == "人工草稿。"
    assert machine_decision(review, review.get(ids[1]), "乙段。", {"kind": "test"})
    assert not machine_decision(review, review.get(ids[1]), "机器覆盖。", {"kind": "test"})


def test_unlocated_page_is_one_pending_task_without_approving_its_content(platform):
    review, run, _ = prepared(
        platform,
        "甲段。\n乙段。",
        ["甲段。", "乙段。"],
        [{"kind": "unreviewed", "excerpt": "", "explanation": "review-failed"}],
    )
    assert repair_scopes(review, run)["page_items_consolidated"] == 1
    issue = review.get(review.list(run)[0]["id"])
    assert issue["kind"] == "page" and issue["state"] == "pending"
    assert issue["text"] == "甲段。\n乙段。"
    assert "不代表每段都有错误" in issue["reasons"][0]


def test_verified_patch_cannot_spill_into_other_review_scope(platform):
    review, run, ids = prepared(platform, "甲段。\n乙段。", ["甲段。", "乙段。"], [])
    issues = [review.get(identity) for identity in ids]
    page = {
        "verified": True,
        "changes": [{"start_before": 0, "end_before": 7, "before": "甲段。\n乙段。", "after": "替换整页。"}],
    }
    assert apply_verified_page(review, issues, page, {"start": 0}, {"kind": "test"}) == 0
    page["changes"] = [{"start_before": 0, "end_before": 3, "before": "甲段。", "after": "甲段修正。"}]
    assert apply_verified_page(review, issues, page, {"start": 0}, {"kind": "test"}) == 2
    assert review.objects.read_bytes(review.get(ids[0])["replacement"]).decode() == "甲段修正。"


def test_new_concern_updates_only_affected_paragraph_and_unknown_location_stays_pending(platform):
    review, run, ids = prepared(platform, "甲段。\n乙段。", ["甲段。", "乙段。"], [])
    page = {
        "text": "甲段。\n乙段。",
        "receipts": [{"verdict": {"review_state": "completed"}}],
        "concerns": [{"kind": "source_unclear", "excerpt": "不存在", "explanation": "原图有疑问"}],
    }
    issues = [review.get(identity) for identity in ids]
    assert refresh_unresolved(review, issues, page, {"start": 0}, {"kind": "test"}) == 0
    page["concerns"][0].update(excerpt="乙段。", explanation="乙段数字无法辨认")
    assert refresh_unresolved(review, issues, page, {"start": 0}, {"kind": "test"}) == 1
    assert review.get(ids[1])["reasons"] == ["乙段数字无法辨认"]
    assert review.get(ids[1])["state"] == "pending"


def test_verified_broad_proposal_applies_only_actual_local_edits(platform):
    original = "敌方损失：\n\n遗产10具。"
    review, run, ids = prepared(platform, original, ["敌方损失：", "遗产10具。"], [])
    page = {
        "verified": True,
        "changes": [
            {
                "start_before": 0,
                "end_before": len(original),
                "before": original,
                "after": original.replace("遗产", "遗尸"),
            }
        ],
    }
    assert (
        apply_verified_page(review, [review.get(i) for i in ids], page, {"start": 0}, {"kind": "test"}) == 2
    )
    assert review.get(ids[0])["replacement"] is None
    assert review.objects.read_bytes(review.get(ids[1])["replacement"]).decode() == "遗尸10具。"


def test_structural_proposal_refresh_keeps_affected_scope_pending(platform):
    before = "甲地\n运输120吨。"
    original = before + "\n\n后段无误。"
    review, run, ids = prepared(platform, original, [before, "后段无误。"], [])
    page = {
        "text": original,
        "receipts": [{"verdict": {"review_state": "completed"}}],
        "concerns": [{
            "kind": "organization", "excerpt": before, "explanation": "段内换行需要确认",
            "proposed_change": {"before": before, "after": before.replace("\n", "")},
        }],
    }
    assert refresh_unresolved(review, [review.get(i) for i in ids], page, {"start": 0}, {"kind": "test"}) == 1
    assert review.get(ids[0])["state"] == "pending"
    assert review.get(ids[0])["reasons"] == ["段内换行需要确认"]
    assert review.get(ids[1])["state"] == "approved"


def test_verified_structural_patch_derives_bounds_without_cross_scope_adoption(platform):
    before = "甲地\n运输120吨。"
    original = before + "\n\n后段无误。"
    review, run, ids = prepared(platform, original, [before, "后段无误。"], [])
    changes = [{"before": original, "after": original.replace("\n", ""), "start_before": 0}]
    page = {"verified": True, "changes": changes}
    assert apply_verified_page(review, [review.get(i) for i in ids], page, {"start": 0}, {"kind": "test"}) == 0
    page["changes"] = [{"before": before, "after": before.replace("\n", ""), "start_before": 0}]
    assert apply_verified_page(review, [review.get(i) for i in ids], page, {"start": 0}, {"kind": "test"}) == 2
    assert review.objects.read_bytes(review.get(ids[0])["replacement"]).decode() == before.replace("\n", "")
    assert review.get(ids[1])["replacement"] is None
