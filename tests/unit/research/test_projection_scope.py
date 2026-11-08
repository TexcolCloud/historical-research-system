"""Graph and identity projections authorize their own evidence, not only visible endpoints."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from hrs.library.errors import ServiceError
from hrs.research.api import Research
from hrs.research.workspace import ResearchWorkspace


def card(identity, **changes):
    return {
        "id": identity,
        "title": identity,
        "book_id": "book-" + identity,
        "run_id": "run-" + identity,
        "state": "verified",
        "version": 1,
        "source_current": True,
        "superseded": False,
        "needs_update": False,
        "source_status": "current",
        "publication_sha256": "a" * 64,
        "evidence": [
            {
                "id": "e1",
                "article_id": "article-" + identity,
                "start": 0,
                "end": 1,
                "quote": "甲",
                "text_sha256": "b" * 64,
                "reading_target": None,
            }
        ],
        "mentions": [],
        **changes,
    }


def member(identity, owner):
    return {
        "id": identity + "-" + owner,
        "canonical_id": identity,
        "name": identity,
        "kind": "person",
        "card_id": owner,
        "card_version": 1,
        "book_id": "book-" + owner,
        "run_id": "run-" + owner,
        "state": "verified",
        "evidence_ids": ["e1"],
    }


def projected(owners, *, stale_representative=False, refs=None):
    left_members = [member("left", "A"), member("left", "B")]
    if not stale_representative:
        left_members.reverse()
    right_members = [member("right", "C")]
    old_alias = {
        "name": "Old name",
        "source_current": True,
        "evidence_refs": [{"card_id": "A", "card_version": 1, "evidence_id": "e1"}],
    }
    current_alias = {
        "name": "Current name",
        "source_current": True,
        "evidence_refs": [{"card_id": "B", "card_version": 1, "evidence_id": "e1"}],
    }
    entities = {
        key: {
            **members[0],
            "id": key,
            "canonical_id": key,
            "version": 1,
            "name": key,
            "source_mentions": members,
            "mentions": members,
            "aliases": [old_alias, current_alias] if key == "left" else [],
            "identity_decisions": [{"id": "retained-user-decision", "action": "merge"}],
        }
        for key, members in (("left", left_members), ("right", right_members))
    }
    relation = {
        "id": "edge-A",
        "source_id": "left",
        "target_id": "right",
        "kind": "association",
        "predicate": "reported relationship",
        "card_id": "A",
        "card_version": 1,
        "approval_type": "native",
        "evidence_ids": ["e1"],
        "evidence_refs": refs or [{"card_id": "A", "card_version": 1, "evidence_id": "e1"}],
    }
    service = object.__new__(Research)
    service.workflow = SimpleNamespace(
        entity=lambda key: deepcopy(entities[key]),
        graph=lambda *args, **kwargs: {
            "entity_id": "left",
            "nodes": deepcopy(list(entities.values())),
            "edges": [deepcopy(relation)],
            "truncated": False,
        },
    )

    def read(identity, **options):
        if identity not in owners:
            raise ServiceError(404, "Removed source")
        return deepcopy(owners[identity])

    service.card = read
    service._test_entities = entities
    service.workflow.relation = lambda key: deepcopy(relation)
    service.workspace = ResearchWorkspace(service)
    return service, relation


@pytest.mark.parametrize(
    "changes",
    [{"source_current": False}, {"superseded": True}, {"version": 2}, {"state": "excluded"}, None],
)
def test_current_endpoints_do_not_authorize_an_old_edge_owner(changes):
    owners = {key: card(key) for key in ("B", "C")}
    if changes is not None:
        owners["A"] = card("A", **changes)
    service, _ = projected(owners)
    graph = service.workspace.graph("left")
    assert graph["edges"] == []
    for node in graph["nodes"]:
        assert all(item["card_id"] != "A" for item in node.get("source_mentions", []))


def test_stale_representative_does_not_hide_an_entity_with_other_current_mentions():
    service, _ = projected(
        {"A": card("A", source_current=False), "B": card("B"), "C": card("C")},
        stale_representative=True,
    )
    graph = service.workspace.graph("left")
    left = next(node for node in graph["nodes"] if node["id"] == "left")
    assert [item["card_id"] for item in left["source_mentions"]] == ["B"]


def test_aliases_are_current_only_when_their_versioned_evidence_survives_projection():
    service, _ = projected({"A": card("A", source_current=False), "B": card("B"), "C": card("C")})
    entity = service.entity("left")
    assert [alias["name"] for alias in entity["aliases"]] == ["Current name"]
    assert [alias["name"] for alias in entity["historical_aliases"]] == ["Old name"]
    assert not entity["historical_aliases"][0]["source_current"]
    assert entity["canonical"]["aliases"] == entity["aliases"]
    assert entity["canonical"]["historical_aliases"] == entity["historical_aliases"]
    assert entity["identity_decisions"][0]["id"] == "retained-user-decision"


def test_reference_cache_cannot_turn_a_stale_card_into_current_evidence():
    owners = {key: card(key) for key in ("A", "B", "C")}
    service, _ = projected(owners)
    with pytest.raises(ServiceError):
        service.workspace.evidence_refs(
            [{"card_id": "A", "card_version": 1, "evidence_id": "e1"}],
            {"A": card("A", source_current=False)},
        )


def test_current_edge_owner_does_not_authorize_a_stale_secondary_reference():
    owners = {key: card(key) for key in ("A", "B", "C")}
    owners["X"] = card("X", source_current=False)
    service, _ = projected(
        owners,
        refs=[
            {"card_id": "A", "card_version": 1, "evidence_id": "e1"},
            {"card_id": "X", "card_version": 1, "evidence_id": "e1"},
        ],
    )
    assert service.workspace.graph("left")["edges"] == []


def test_current_relationship_returns_located_authoritative_evidence():
    service, _ = projected({key: card(key) for key in ("A", "B", "C")})
    edge = service.workspace.graph("left")["edges"][0]
    assert edge["evidence"][0]["article_id"] == "article-A"
    assert edge["evidence"][0]["quote"] == "甲"


@pytest.mark.parametrize(
    "target",
    [card("T", version=2), card("T", publication_sha256="c" * 64), card("T", source_current=False)],
)
def test_cross_card_relation_checks_the_frozen_target_version_and_publication(target):
    service, _ = projected({"A": card("A"), "B": card("B"), "C": card("C"), "T": target})
    relation = {
        "id": "cross",
        "source_id": "A",
        "target_id": "T",
        "card_id": "A",
        "card_version": 1,
        "target_card_id": "T",
        "target_card_version": 1,
        "target_publication_sha256": "a" * 64,
        "source_evidence_ids": ["e1"],
        "target_evidence_ids": ["e1"],
    }
    with pytest.raises(ServiceError):
        service.workspace.current_relation(relation)


def notebook_service(tmp_path, owners):
    from hrs.processing.store import Store

    service, relation = projected(owners)
    service.bridge = SimpleNamespace(store=Store(tmp_path / "native"))
    service.workspace._source_metadata = lambda identity: {
        "source_run_id": "run-" + identity.removeprefix("article-"),
        "publication_sha256": "a" * 64,
        "content_sha256": "c" * 64,
    }
    return service, relation


@pytest.mark.parametrize("kind", ["card", "entity", "relation"])
def test_derived_selection_versions_change_without_falsely_invalidating_original_text(
    tmp_path, kind
):
    from hrs.research.workspace import QuestionEvidence

    owners = {key: card(key) for key in ("A", "B", "C")}
    service, relation = notebook_service(tmp_path, owners)
    notebook = service.workspace.notebook
    question = notebook.create("Keep the adopted source text and its derived version", [])
    identity = {"card": "A", "entity": "left", "relation": "edge-A"}[kind]
    saved = service.workspace.select_evidence(
        question["id"],
        QuestionEvidence(
            kind=kind, target_id=identity, decision="adopted", expected_version=question["version"]
        ),
    )
    selection = saved["selections"][0]
    assert (
        selection["record_current"]
        and selection["record_ref"]["kind"] == kind
        and selection["version_refs"]
    )
    if kind == "card":
        owners["A"]["state"] = "excluded"
    elif kind == "entity":
        service._test_entities["left"]["version"] += 1
    else:
        relation["predicate"] = "Updated interpretation with the same original quote"
    reread = service.workspace.question(question["id"])["selections"][0]
    assert reread["record_current"] is False
    assert all(span["source_current"] for span in reread["evidence"])
    assert reread["decision"] == "adopted" and reread["evidence"][0]["quote"] == "甲"


def test_old_unbound_derived_selection_is_unknown_while_source_excerpt_stays_current(tmp_path):
    service, _ = notebook_service(tmp_path, {key: card(key) for key in ("A", "B", "C")})
    notebook = service.workspace.notebook
    question = notebook.create("Retain old saved selections", [])
    evidence = service.workspace._snapshot_evidence([card("A")["evidence"][0]])
    notebook.select(
        question["id"],
        question["version"],
        {
            "kind": "card",
            "target_id": "A",
            "title": "Legacy",
            "decision": "adopted",
            "evidence": evidence,
        },
    )
    retained = service.workspace.question(question["id"])
    assert not retained["selections"][0]["record_current"]
    assert retained["selections"][0]["evidence"][0]["source_current"]
    notebook.select(
        question["id"],
        retained["version"],
        {"kind": "source", "target_id": "article-A", "title": "Original", "evidence": evidence},
    )
    excerpt = next(
        item
        for item in service.workspace.question(question["id"])["selections"]
        if item["kind"] == "source"
    )
    assert "record_current" not in excerpt and excerpt["evidence"][0]["source_current"]


def test_complete_server_owned_card_selection_keeps_more_than_one_hundred_evidence_spans(tmp_path):
    from hrs.research.workspace import QuestionEvidence

    owners = {key: card(key) for key in ("A", "B", "C")}
    owners["A"]["evidence"] = [
        {**owners["A"]["evidence"][0], "id": f"e{index}"} for index in range(133)
    ]
    service, _ = notebook_service(tmp_path, owners)
    question = service.workspace.notebook.create("Complete card", [])
    selected = service.workspace.select_evidence(
        question["id"],
        QuestionEvidence(kind="card", target_id="A", expected_version=question["version"]),
    )
    assert len(selected["selections"][0]["evidence"]) == 133
    with pytest.raises(ValueError):
        QuestionEvidence(
            kind="card",
            target_id="A",
            expected_version=selected["version"],
            evidence_ids=[f"e{index}" for index in range(101)],
        )
