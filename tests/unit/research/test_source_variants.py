"""Synthetic provenance variants keep retained cards and exact source gates intact."""

import json
from copy import deepcopy

import pytest

from hrs.processing.errors import EvidenceMismatch
from hrs.processing.hashing import digest
from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from hrs.research.projection import located_card
from tests.unit.research.test_workflow import legacy_policy, verify

TEXT = "Memo[^1][^2].\n\n[^1]: Sent.\n[^2]: Copied.\n"


def publication(book, *, expanded=False):
    notes = [
        {
            "id": "note-" + number,
            "label": number,
            "note": {
                "start": TEXT.index(f"[^{number}]:"),
                "end": TEXT.index("\n", TEXT.index(f"[^{number}]:")) + 1,
                "marker_end": TEXT.index(f"[^{number}]:") + 5,
            },
            "references": [
                {"start": TEXT.index(f"[^{number}]"), "end": TEXT.index(f"[^{number}]") + 4}
            ],
        }
        for number in (["1", "2"] if expanded else ["1"])
    ]
    return {
        "book_id": book,
        "source_run_id": "publication-" + book,
        "document_id": "a" * 24,
        "title": "Synthetic memo",
        "publication_sha256": digest(TEXT),
        "content_verified": True,
        "validation_scope": "content",
        "articles": [
            {
                "id": "article-" + book,
                "title": "Memo",
                "text": TEXT,
                "text_sha256": digest(TEXT),
                "pages": [3],
                "footnotes": notes,
                "parts": [
                    {
                        "id": "body",
                        "start": 0,
                        "end": len(TEXT),
                        "text": TEXT,
                        "source": {"pages": [3], "image_sha256": "1" * 64},
                    }
                ],
            }
        ],
    }


def prepare(workflow, snapshot, *, revision_of=None):
    # Explicitly retain the v4 wire contract; v5 source-first/target regressions
    # run alongside this fixture, which only varies source provenance metadata.
    run = workflow.enqueue(
        snapshot,
        revision_of=revision_of,
        reason="Synthetic retained-source revision" if revision_of else None,
        _policy=legacy_policy(4),
    )
    packet = workflow.claim(run["run_id"], "author-" + snapshot["book_id"], stage="author")
    workflow.read_source(packet["task_token"])
    card = {
        "id": "memo",
        "article_id": snapshot["articles"][0]["id"],
        "title": "Memo",
        "summary": "Synthetic memo text.",
        "no_argument_reason": "Technical fixture has no historical argument.",
        "evidence": [
            {
                "id": "e1",
                "article_id": snapshot["articles"][0]["id"],
                "start": 0,
                "end": len(TEXT),
                "quote": TEXT,
            }
        ],
    }
    workflow.submit(
        packet["task_token"],
        packet["worker_id"],
        {"input_hash": packet["input_hash"], "cards": [card]},
    )
    packet = workflow.claim(run["run_id"], "enrich-" + snapshot["book_id"], stage="enrich")
    workflow.read_source(packet["task_token"])
    workflow.record_search(
        packet["task_token"],
        {
            "query": "Synthetic memo",
            "backend": "hybrid",
            "available": True,
            "hits": [],
            "metrics": {},
        },
    )
    return run, packet, card


def complete_target(workflow, *, person=False):
    run, packet, card = prepare(workflow, publication("target"))
    if person:
        card["mentions"] = [
            {
                "id": "person",
                "kind": "person",
                "name": "Memo",
                "identity_basis": "Synthetic named entity",
                "evidence_ids": ["e1"],
            }
        ]
    workflow.submit(
        packet["task_token"],
        packet["worker_id"],
        {"input_hash": packet["input_hash"], "cards": [card]},
    )
    final = verify(workflow, run["run_id"], worker="target-verifier")
    return workflow.card(final["card_ids"][0])


def target_text(workflow, packet, manifest):
    text, offset = "", 0
    while offset < manifest["total"]:
        window = workflow.target_card_text(
            packet["task_token"], packet["worker_id"], manifest["card_ref"], offset, 2000
        )
        text += window["text"]
        offset = window["end"]
    return json.loads(text)


def seed_legacy_source(workflow, packet, snapshot):
    """Retain an exact pre-canonical record, as loaded by a resumed old lease."""
    snapshot = workflow._snapshot(snapshot)
    external = deepcopy(snapshot["articles"][0])
    external.update(
        original_article_id=external["id"],
        id="source-"
        + digest([snapshot["book_id"], snapshot["publication_sha256"], external["id"]])[:32],
        book_id=snapshot["book_id"],
        publication_sha256=snapshot["publication_sha256"],
        source_run_id=snapshot["source_run_id"],
        document_id=snapshot["document_id"],
    )
    with workflow.ledger.connect(write=True) as connection:
        task, _ = workflow._lease(connection, packet["task_token"])
        reference = workflow._freeze_source(connection, external, "external_article")
        task["external_sources"].append(
            {
                "id": external["id"],
                "source_ref": reference,
                "text_sha256": external["text_sha256"],
            }
        )
        workflow._save_task(connection, task)
    return [workflow._source_index(external)]


@pytest.mark.parametrize("current_first", [True, False])
def test_current_source_and_retained_counterpart_keep_distinct_note_versions(
    tmp_path, current_first
):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    target = complete_target(workflow)
    run, packet, _ = prepare(workflow, publication("primary"))
    current = publication("target", expanded=True)
    original_input = deepcopy(current)

    def freeze_target():
        return workflow.target_card(packet["task_token"], packet["worker_id"], target["id"], 1)

    if current_first:
        current_index = workflow.add_source(packet["task_token"], current)[0]
        manifest = freeze_target()
    else:
        manifest = freeze_target()
        current_index = workflow.add_source(packet["task_token"], current)[0]
    target_id = manifest["source_ids"][0]
    assert current_index["id"] != target_id
    assert workflow.add_source(packet["task_token"], current) == [current_index]
    assert freeze_target() == manifest
    assert current == original_input
    assert workflow.card(target["id"]) == target
    assert (
        workflow.read_source(packet["task_token"], article_id=target_id)["footnotes"]
        == publication("target")["articles"][0]["footnotes"]
    )
    assert (
        workflow.read_source(packet["task_token"], article_id=current_index["id"])["footnotes"]
        == current["articles"][0]["footnotes"]
    )
    descriptor = target_text(workflow, packet, manifest)
    assert descriptor["frozen_source_index"][0]["id"] == target_id
    assert descriptor["evidence"][0]["footnote_ids"] == ["note-1"]
    assert {source["id"] for source in workflow.sources(run["run_id"])} == {
        target_id,
        current_index["id"],
    }


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("field", ["text", "pages", "pixels", "content", "run", "title", "extra"])
def test_non_note_changes_still_fail_without_changing_retained_sources(tmp_path, field, legacy):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run, packet, _ = prepare(workflow, publication("primary"))
    original = publication("target")
    first = (
        seed_legacy_source(workflow, packet, original)
        if legacy
        else workflow.add_source(packet["task_token"], original)
    )
    assert len(first[0]["id"]) == (39 if legacy else 104)
    assert workflow.add_source(packet["task_token"], original) == first
    expanded = publication("target", expanded=True)
    workflow.add_source(packet["task_token"], expanded)
    retained = workflow.sources(run["run_id"])
    changed = deepcopy(expanded)
    article = changed["articles"][0]
    if field == "text":
        article["text"] = article["text"].replace("Memo", "Note")
        article["text_sha256"] = digest(article["text"])
        article["parts"][0]["text"] = article["text"]
    elif field == "pages":
        article["pages"] = article["parts"][0]["source"]["pages"] = [4]
    elif field == "pixels":
        article["parts"][0]["source"]["image_sha256"] = "2" * 64
    elif field == "content":
        article["content"] = {"key": "different-object", "sha256": "3" * 64}
    elif field == "run":
        changed["source_run_id"] = "different-publication"
    elif field == "title":
        article["title"] = "Different title"
    else:
        article["unexpected_metadata"] = {}
    untouched_input = deepcopy(changed)
    with pytest.raises(EvidenceMismatch, match="Previously frozen external source changed"):
        workflow.add_source(packet["task_token"], changed)
    assert changed == untouched_input
    assert workflow.sources(run["run_id"]) == retained
    assert workflow.add_source(packet["task_token"], original) == first


def test_note_variants_require_separate_source_reads_and_exact_target_ownership(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    target = complete_target(workflow)
    run, packet, card = prepare(workflow, publication("primary"))
    token, worker = packet["task_token"], packet["worker_id"]
    current_id = workflow.add_source(token, publication("target", expanded=True))[0]["id"]
    manifest = workflow.target_card(token, worker, target["id"], 1)
    target_id = manifest["source_ids"][0]
    card["evidence"].extend(
        [
            {
                "id": "current-note",
                "article_id": current_id,
                "start": 0,
                "end": len(TEXT),
                "quote": TEXT,
                "footnote_ids": ["note-2"],
            },
            {
                "id": "target-note",
                "article_id": target_id,
                "start": 0,
                "end": len(TEXT),
                "quote": TEXT,
                "footnote_ids": ["note-1"],
            },
        ]
    )
    card["cross_relations"] = [
        {
            "id": "counterpart",
            "target_card_id": target["id"],
            "target_card_version": 1,
            "target_publication_sha256": target["publication_sha256"],
            "kind": "same_source",
            "reason": "Synthetic copies have identical original text.",
            "certainty": "stated",
            "source_evidence_ids": ["e1"],
            "target_evidence_ids": ["target-note"],
        }
    ]
    result = {"input_hash": packet["input_hash"], "cards": [card]}
    workflow.read_source(token, article_id=current_id)
    with pytest.raises(EvidenceMismatch, match="complete original"):
        workflow.submit(token, worker, result)
    workflow.read_source(token, article_id=target_id)
    target_text(workflow, packet, manifest)
    card["evidence"][2]["footnote_ids"] = ["note-2"]
    with pytest.raises(EvidenceMismatch, match="Footnote belongs"):
        workflow.submit(token, worker, result)
    card["evidence"][2]["footnote_ids"] = ["note-1"]
    card["evidence"][2]["article_id"] = current_id
    with pytest.raises(EvidenceMismatch, match="frozen target document source"):
        workflow.submit(token, worker, result)
    card["evidence"][2]["article_id"] = target_id
    workflow.submit(token, worker, result)

    review = workflow.claim(run["run_id"], "independent-variant-review", stage="verify")
    assert {entry["id"] for entry in review["source_index"]} == {
        "article-primary",
        current_id,
        target_id,
    }
    workflow.read_source(review["task_token"])
    workflow.read_source(review["task_token"], article_id=current_id)
    observations = {
        "article_id": "article-primary",
        "text_sha256": digest(TEXT),
        "summary": "Read the synthetic source independently.",
    }
    with pytest.raises(EvidenceMismatch, match="complete original"):
        workflow.candidate(review["task_token"], review["worker_id"], observations)
    workflow.read_source(review["task_token"], article_id=target_id)
    candidate = workflow.candidate(review["task_token"], review["worker_id"], observations)
    retained_target = workflow.target_card(
        review["task_token"],
        review["worker_id"],
        target["id"],
        1,
        authorize=lambda _: pytest.fail("Verification must not load a new live counterpart"),
    )
    assert retained_target == manifest
    target_text(workflow, review, manifest)
    final = workflow.submit(
        review["task_token"],
        review["worker_id"],
        {
            "input_hash": review["input_hash"],
            "decision": "approve",
            **candidate["required_checks"],
        },
    )
    assert final["state"] == "completed"
    assert workflow.card(target["id"]) == target


def test_resumed_lease_reuses_legacy_source_and_binds_only_retained_target_variant(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    target = complete_target(workflow)
    run, packet, _ = prepare(workflow, publication("primary"))
    current = publication("target", expanded=True)
    legacy_index = seed_legacy_source(workflow, packet, current)
    assert len(legacy_index[0]["id"]) == 39
    frozen_current = workflow.sources(run["run_id"])[0]
    frozen_hash = digest(frozen_current)
    frozen_policy = workflow.status(run["run_id"])["policy"]
    workflow.pause(run["run_id"])
    workflow.resume(run["run_id"])

    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    resumed = workflow.claim(run["run_id"], "resumed-enricher", stage="enrich")
    assert resumed["task_token"] != packet["task_token"]
    workflow.read_source(resumed["task_token"])
    assert workflow.add_source(resumed["task_token"], current) == legacy_index
    manifest = workflow.target_card(resumed["task_token"], resumed["worker_id"], target["id"], 1)
    assert len(manifest["source_ids"]) == len(manifest["source_index"]) == 1
    assert legacy_index[0]["id"] not in manifest["source_ids"]
    assert manifest["source_ids"] == [manifest["source_index"][0]["id"]]
    sources = {source["id"]: source for source in workflow.sources(run["run_id"])}
    assert sources[legacy_index[0]["id"]] == frozen_current
    assert digest(sources[legacy_index[0]["id"]]) == frozen_hash
    assert (
        sources[manifest["source_ids"][0]]["footnotes"]
        == publication("target")["articles"][0]["footnotes"]
    )
    assert workflow.status(run["run_id"])["policy"] == frozen_policy


@pytest.mark.parametrize("legacy_first", [False, True])
def test_two_tasks_freeze_opposite_orders_without_run_source_projection_collision(
    tmp_path, legacy_first
):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    target = complete_target(workflow)
    primary = publication("primary")
    secondary = deepcopy(primary["articles"][0])
    secondary["id"] = "article-secondary"
    primary["articles"].append(secondary)
    run, first, card = prepare(workflow, primary)
    author = workflow.claim(run["run_id"], "second-author", stage="author")
    workflow.read_source(author["task_token"])
    second_card = deepcopy(card)
    second_card.update(id="second-memo", article_id=secondary["id"])
    second_card["evidence"][0]["article_id"] = secondary["id"]
    workflow.submit(
        author["task_token"],
        author["worker_id"],
        {
            "input_hash": author["input_hash"],
            "cards": [second_card],
        },
    )
    second = workflow.claim(run["run_id"], "second-enricher", stage="enrich")
    workflow.read_source(second["task_token"])
    current = publication("target", expanded=True)
    first_current = (
        seed_legacy_source(workflow, first, current)[0]
        if legacy_first
        else workflow.add_source(first["task_token"], current)[0]
    )
    first_target = workflow.target_card(first["task_token"], first["worker_id"], target["id"], 1)
    second_target = workflow.target_card(second["task_token"], second["worker_id"], target["id"], 1)
    second_current = workflow.add_source(second["task_token"], current)[0]

    assert {k: v for k, v in first_current.items() if k != "id"} == {
        k: v for k, v in second_current.items() if k != "id"
    }
    assert (first_current["id"] == second_current["id"]) is (not legacy_first)
    assert first_target == second_target
    assert len(second_current["id"]) == len(first_target["source_ids"][0]) == 104
    expected = {
        first_current["id"]: current["articles"][0]["footnotes"],
        second_current["id"]: current["articles"][0]["footnotes"],
        first_target["source_ids"][0]: publication("target")["articles"][0]["footnotes"],
    }
    for reopened in (workflow, ResearchWorkflow(Store(tmp_path / "native"))):
        sources = reopened.sources(run["run_id"])
        assert len(sources) == (3 if legacy_first else 2)
        assert {source["id"]: source["footnotes"] for source in sources} == expected
        for packet, index in ((first, first_current), (second, second_current)):
            assert reopened.add_source(packet["task_token"], current) == [index]
            assert (
                reopened.target_card(packet["task_token"], packet["worker_id"], target["id"], 1)
                == first_target
            )
        for identity, notes in expected.items():
            span = {
                "id": "e",
                "article_id": identity,
                "start": 0,
                "end": len(TEXT),
                "quote": TEXT,
                "footnotes": deepcopy(notes),
            }
            projected = located_card({"id": "projected", "evidence": [span]}, primary, sources)
            assert [
                {key: value for key, value in note.items() if key not in {"text", "reading_target"}}
                for note in projected["evidence"][0]["footnotes"]
            ] == notes
            for note in projected["evidence"][0]["footnotes"]:
                assert note["text"] == TEXT[note["note"]["start"] : note["note"]["end"]]
                assert note["reading_target"]["chapter_id"] == "article-target"
            assert projected["evidence"][0]["original_article_id"] == "article-target"


@pytest.mark.parametrize("case", ["target_variants", "primary_alias", "foreign_only", "proper"])
def test_same_as_requires_primary_and_a_different_target_material_not_transport_ids(tmp_path, case):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    target = complete_target(workflow, person=True)
    entity = workflow.list_entities(run_id=target["run_id"])[0]
    same_primary = case == "primary_alias"
    run, packet, card = prepare(
        workflow,
        publication("target" if same_primary else "primary"),
        revision_of=target["run_id"] if same_primary else None,
    )
    token = packet["task_token"]
    external_id = workflow.add_source(token, publication("target"))[0]["id"]
    card["evidence"].append(
        {"id": "target-e", "article_id": external_id, "start": 0, "end": len(TEXT), "quote": TEXT}
    )
    linked = ["e1", "target-e"]
    if case in {"target_variants", "foreign_only"}:
        extra = (
            publication("target", expanded=True)
            if case == "target_variants"
            else publication("third")
        )
        extra_id = workflow.add_source(token, extra)[0]["id"]
        card["evidence"].append(
            {"id": "other-e", "article_id": extra_id, "start": 0, "end": len(TEXT), "quote": TEXT}
        )
        workflow.read_source(token, article_id=extra_id)
        linked = ["target-e", "other-e"]
    workflow.read_source(token, article_id=external_id)
    card["mentions"] = [
        {
            "id": "local-person",
            "kind": "person",
            "name": "Memo",
            "identity_basis": "Synthetic named entity",
            "evidence_ids": ["e1"],
        }
    ]
    card["identity_links"] = [
        {
            "local_mention_id": "local-person",
            "entity_id": entity["id"],
            "relation": "same_as",
            "evidence_ids": linked,
            "reason": "Synthetic identity comparison.",
        }
    ]
    result = {"input_hash": packet["input_hash"], "cards": [card]}
    if case == "proper":
        assert workflow.submit(token, packet["worker_id"], result)["state"] == "running"
    else:
        with pytest.raises(EvidenceMismatch, match="both source articles"):
            workflow.submit(token, packet["worker_id"], result)


def test_complete_v5_same_as_keeps_real_local_and_target_material_pair(tmp_path):
    from tests.unit.research.test_complete_workflow import (
        completed,
        draft,
        enrich,
        source,
        submit_author,
    )
    from tests.unit.research.test_complete_workflow import (
        verify as complete_verify,
    )

    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    target = completed(workflow, "v5-target")
    entity = next(
        item for item in workflow.list_entities(run_id=target["run_id"]) if item["name"] == "张三"
    )
    run = workflow.enqueue(source("v5-primary"), _policy=legacy_policy(5))
    card = draft("v5-primary")
    submit_author(workflow, run, card)
    card["identity_links"] = [
        {
            "local_mention_id": "sender",
            "entity_id": entity["id"],
            "relation": "same_as",
            "evidence_ids": ["e1", "target-e1"],
            "reason": "Synthetic matching sender, recipient, date and letter text.",
        }
    ]
    enrich(workflow, run, card, target=target)
    assert complete_verify(workflow, run, book="v5-primary", target=target)["state"] == "completed"
