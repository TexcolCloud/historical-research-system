"""Synthetic navigation checks; these never approve source content."""

import json

import pytest

from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from hrs.research.catalogue import ResearchCatalogue
from hrs.research.network import DateWindow, NetworkQuery, date_position
from hrs.research.views import ResearchViews


def test_partial_uncertain_dates_use_overlap_without_inventing_precision():
    year = {"text": "約一九三六年", "normalized": "1936", "certainty": "inferred"}
    position = date_position(year)
    assert position.start == position.end == "1936"
    assert position.precision == "year"
    assert DateWindow("1936-05", "1936-05").includes(position)
    assert not DateWindow("1937", None).includes(position)
    assert date_position({**year, "normalized": "1936-02-30"}).undated
    assert date_position({**year, "certainty": "unknown"}).undated
    with pytest.raises(ValueError, match="日期"):
        DateWindow("1936-02-30", None)


def recorded_card(
    workflow,
    identity,
    *,
    date="1936",
    certainty="stated",
    event=True,
    event_dates=None,
    participant_role=None,
):
    date_value = {
        "text": date,
        "normalized": date,
        "certainty": certainty,
        "evidence_ids": ["proof"],
    }
    card = {
        "id": identity,
        "version": 1,
        "state": "verified",
        "book_id": "book-" + identity,
        "run_id": "run-" + identity,
        "article_id": "article-" + identity,
        "article_sha256": "text-" + identity,
        "publication_sha256": "pub-" + identity,
        "approval_type": "native",
        "title": identity,
        "summary": "Synthetic fixture only",
        "formation_date": date_value,
        "research_created_at": 1,
        "evidence": [
            {
                "id": "proof",
                "article_id": "article-" + identity,
                "start": 0,
                "end": 1,
                "quote": "甲",
            }
        ],
        "mentions": [
            {"id": "person", "kind": "person", "name": "甲", "evidence_ids": ["proof"]},
            {"id": "place", "kind": "place", "name": "乙", "evidence_ids": ["proof"]},
        ],
        "events": [
            {
                "id": f"event-{index}",
                "kind": "event",
                "name": f"Event {index:03} {identity}",
                "description": "Synthetic event",
                "participant_ids": ["person"],
                "participant_roles": [
                    {
                        "mention_id": "person",
                        "role": participant_role,
                        "description": "Synthetic role",
                        "evidence_ids": ["proof"],
                    }
                ]
                if participant_role
                else [],
                "date": {**date_value, "text": event_date, "normalized": event_date},
                "evidence_ids": ["proof"],
            }
            for index, event_date in enumerate(event_dates or [date])
        ]
        if event
        else [],
        "relations": [
            {
                "id": "relation",
                "subject_id": "person",
                "object_id": "place",
                "predicate": "association",
                "evidence_ids": ["proof"],
            }
        ],
    }
    with workflow.ledger.connect(write=True) as connection:
        workflow._schema(connection)
        connection.execute(
            "INSERT INTO research_runs VALUES (?,?,?,?,?,?)",
            (card["run_id"], card["book_id"], identity, identity, "completed", "{}"),
        )
        connection.execute(
            "INSERT INTO research_cards VALUES (?,?,?,?,?,?)",
            (identity, 1, card["run_id"], card["book_id"], "verified", json.dumps(card)),
        )
        workflow.catalogue.record(card, connection=connection)
    return card


def gate(*cards):
    return [
        {key: card[key] for key in ("id", "version", "book_id", "publication_sha256")}
        for card in cards
    ]


def test_timeline_participants_retain_the_source_name_and_role(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    card = recorded_card(workflow, "participants", participant_role="拟议行动者")
    page = NetworkQuery(workflow.ledger, current_cards=gate(card)).timeline()
    assert page["items"][0]["participants"] == [
        {
            "entity_id": workflow.catalogue.entities(book_id=card["book_id"], kind="person")[
                "items"
            ][0]["id"],
            "name": "甲",
            "role": "拟议行动者",
        }
    ]
    assert page["items"][0]["participant_count"] == 1


def test_timeline_participant_preview_keeps_source_order_roles_and_version_bound_proofs(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    card = recorded_card(workflow, "participants")
    card["version"] = 2
    card["mentions"] = [
        {
            "id": f"actor-{index}",
            "kind": "person",
            "name": f"Source actor {index}",
            "evidence_ids": [f"actor-proof-{index}"],
        }
        for index in range(25)
    ]
    card["evidence"] += [
        {**card["evidence"][0], "id": f"{kind}-proof-{index}"}
        for index in range(25)
        for kind in ("actor", "role")
    ]
    event = card["events"][0]
    event["participant_ids"] = [f"actor-{index}" for index in reversed(range(25))]
    event["participant_roles"] = [
        {
            "mention_id": f"actor-{index}",
            "role": f"planned actor {index}",
            "evidence_ids": [f"role-proof-{index}"],
        }
        for index in range(25)
    ]
    with workflow.ledger.connect(write=True) as connection:
        connection.execute(
            "INSERT INTO research_cards VALUES (?,?,?,?,?,?)",
            (
                card["id"],
                card["version"],
                card["run_id"],
                card["book_id"],
                card["state"],
                json.dumps(card),
            ),
        )
        workflow.catalogue.record(card, connection=connection)
    query = NetworkQuery(workflow.ledger, current_cards=gate(card))
    first = query.timeline()["items"][0]
    assert first["participant_count"] == 25 and len(first["participants"]) == 20
    assert [participant["name"] for participant in first["participants"]] == [
        f"Source actor {index}" for index in range(24, 4, -1)
    ]
    assert [participant["role"] for participant in first["participants"]] == [
        f"planned actor {index}" for index in range(24, 4, -1)
    ]
    assert first["evidence_ids"] == [
        "proof",
        *[f"{kind}-proof-{index}" for index in range(24, 4, -1) for kind in ("actor", "role")],
    ]
    with workflow.ledger.connect(write=True) as connection:
        connection.execute(
            "UPDATE research_catalogue_members SET card_version=1 WHERE card_id=? AND json_extract(payload,'$.local_id')='actor-24'",
            (card["id"],),
        )
    current = query.timeline()["items"][0]
    assert current["participant_count"] == 24
    assert current["participants"][0]["name"] == "Source actor 23"
    assert "actor-proof-24" not in current["evidence_ids"]
    assert "role-proof-24" not in current["evidence_ids"]


def test_network_authorizes_before_limit_and_batches_nodes(tmp_path, monkeypatch):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    stale, current = recorded_card(workflow, "stale"), recorded_card(workflow, "current")
    entities = workflow.catalogue.entities(book_id=current["book_id"])["items"]
    root = next(item["id"] for item in entities if item["kind"] == "person")
    target = next(item["id"] for item in entities if item["kind"] == "place")
    with workflow.ledger.connect(write=True) as connection:
        connection.execute("DELETE FROM research_catalogue_edges")
        for index in range(60):
            owner = current if index == 59 else stale
            edge = {
                "id": f"edge-{index:02}",
                "source_id": root,
                "target_id": target,
                "kind": "association",
                "card_id": owner["id"],
                "card_version": 1,
                "book_id": owner["book_id"],
                "evidence": owner["evidence"],
                "evidence_refs": [
                    {"card_id": owner["id"], "card_version": 1, "evidence_id": "proof"}
                ],
            }
            connection.execute(
                "INSERT INTO research_catalogue_edges VALUES (?,?,?,?,?,?,?,?)",
                (edge["id"], 1, root, target, owner["id"], "association", 1, json.dumps(edge)),
            )
    calls = []
    original = NetworkQuery._nodes
    monkeypatch.setattr(
        NetworkQuery, "_nodes", staticmethod(lambda *args: (calls.append(1), original(*args))[1])
    )
    monkeypatch.setattr(
        ResearchCatalogue,
        "_detail",
        lambda *args: pytest.fail("A summary must not load full entity detail"),
    )
    result = NetworkQuery(workflow.ledger, current_cards=gate(current)).graph(
        root, limit=1, node_kinds=["place"]
    )
    assert [edge["id"] for edge in result["edges"]] == ["edge-59"]
    assert len(calls) == 2 and len(result["nodes"]) == 2
    assert not result["truncated"]
    assert all(
        "evidence" not in item and "source_mentions" not in item
        for item in result["nodes"] + result["edges"]
    )
    assert (
        NetworkQuery(workflow.ledger, current_cards=gate(current)).graph(root, predicate="absent")[
            "edges"
        ]
        == []
    )


def test_timeline_filters_and_sorts_before_pagination_and_retains_unknowns(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    late = recorded_card(workflow, "a-late", date="1940-03")
    early = recorded_card(workflow, "z-early", date="1936", certainty="inferred")
    unknown = recorded_card(workflow, "unknown", date="1936", certainty="unknown")
    stale = recorded_card(workflow, "old", date="1900")
    query = NetworkQuery(workflow.ledger, current_cards=gate(late, early, unknown))
    first = query.timeline(limit=1)
    assert first["items"][0]["card_id"] == early["id"]
    assert first["total"] == 3 and first["dated_total"] == 2 and first["undated_total"] == 1
    assert first["truncated"] and first["items"][0]["date_start"] == "1936"
    assert first["items"][0]["date"]["certainty"] == "inferred"
    assert query.timeline(limit=1, offset=1)["items"][0]["card_id"] == late["id"]
    last = query.timeline(limit=1, offset=2)
    assert last["items"][0]["undated"] and not last["truncated"]
    filtered = query.timeline(date_from="1936-05", date_to="1936-05", include_undated=False)
    assert [item["card_id"] for item in filtered["items"]] == [early["id"]]
    assert all(item["card_id"] != stale["id"] for item in first["items"])
    assert query.timeline(book_ids=[])["total"] == 0
    root = next(
        item["id"]
        for item in workflow.catalogue.entities(book_id=early["book_id"])["items"]
        if item["kind"] == "person"
    )
    documents = query.timeline(entity_id=root, date_basis="formation")
    assert documents["total"] == 1 and documents["items"][0]["card_id"] == early["id"]
    assert documents["items"][0]["item_kind"] == "document"
    assert "evidence" not in documents["items"][0]


def test_timeline_reads_more_than_two_hundred_participations_before_date_sort(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    current = recorded_card(
        workflow, "large", event_dates=[str(year) for year in range(2000, 1770, -1)]
    )
    with workflow.ledger.connect() as connection:
        root = connection.execute(
            "SELECT entity_id FROM research_catalogue_members WHERE card_id=? AND json_extract(payload,'$.local_id')='person'",
            (current["id"],),
        ).fetchone()[0]
    query = NetworkQuery(workflow.ledger, current_cards=gate(current))
    first = query.timeline(entity_id=root, limit=100)
    assert first["total"] == 230 and first["items"][0]["date_start"] == "1771"
    assert first["items"][0]["name"].startswith("Event 229")
    assert (
        query.timeline(entity_id=root, offset=200, limit=100)["items"][-1]["date_start"] == "2000"
    )
    graph = query.graph(root, limit=200, predicate="participant")
    assert len(graph["edges"]) == 200 and graph["truncated"]
    assert all(edge["evidence_count"] == 1 for edge in graph["edges"])


def test_network_exact_limit_and_version_bound_evidence(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    current = recorded_card(workflow, "current")
    root = next(
        item["id"]
        for item in workflow.catalogue.entities(book_id=current["book_id"])["items"]
        if item["kind"] == "person"
    )
    query = NetworkQuery(workflow.ledger, current_cards=gate(current))
    exact = query.graph(root, limit=2)
    assert len(exact["edges"]) == 2 and not exact["truncated"]
    assert (
        next(edge for edge in exact["edges"] if edge["kind"] == "association")["date_basis"]
        == "formation"
    )
    assert (
        next(edge for edge in exact["edges"] if edge["kind"] == "participant")["date_basis"]
        == "event"
    )
    assert any(
        node["kind"] == "card" for node in query.graph(root, include_source_cards=True)["nodes"]
    )
    assert query.graph(root, limit=1)["truncated"]
    with workflow.ledger.connect(write=True) as connection:
        edges = connection.execute(
            "SELECT id,payload FROM research_catalogue_edges WHERE kind='association'"
        ).fetchall()
        for edge_id, payload in edges:
            edge = json.loads(payload)
            edge["evidence_refs"][0]["card_version"] = 2
            connection.execute(
                "UPDATE research_catalogue_edges SET payload=? WHERE id=?",
                (json.dumps(edge), edge_id),
            )
    assert query.graph(root, predicate="association")["edges"] == []
    assert len(query.graph(root, limit=1)["edges"]) == 1
    assert (
        NetworkQuery(
            workflow.ledger, current_cards=[{**gate(current)[0], "version": 2}]
        ).timeline()["total"]
        == 0
    )


def test_timeline_retains_the_dates_own_evidence_and_uncapped_merged_source(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    current = recorded_card(workflow, "merged", event_dates=["1936"] * 230)
    with workflow.ledger.connect(write=True) as connection:
        members = connection.execute(
            "SELECT id,entity_id,payload FROM research_catalogue_members "
            "WHERE json_extract(payload,'$.kind')='event' ORDER BY id"
        ).fetchall()
        canonical = members[0][1]
        connection.execute(
            "UPDATE research_catalogue_members SET entity_id=? WHERE json_extract(payload,'$.kind')='event'",
            (canonical,),
        )
        identity, _, payload = members[-1]
        source = json.loads(payload)
        source["date"] = {
            "text": "1700年",
            "normalized": "1700",
            "certainty": "disputed",
            "evidence_ids": ["date-proof"],
        }
        current["evidence"].append({**current["evidence"][0], "id": "date-proof"})
        connection.execute(
            "UPDATE research_cards SET payload=? WHERE id=?", (json.dumps(current), current["id"])
        )
        connection.execute(
            "UPDATE research_catalogue_members SET payload=? WHERE id=?",
            (json.dumps(source), identity),
        )
    summary = NetworkQuery(workflow.ledger, current_cards=gate(current)).timeline(limit=1)
    assert summary["total"] == 230
    assert summary["items"][0]["id"] == identity
    assert summary["items"][0]["date"]["certainty"] == "disputed"
    assert summary["items"][0]["evidence_ids"] == ["proof", "date-proof"]
    legacy = ResearchViews(workflow).timeline(limit=1)["items"][0]
    assert legacy["id"] == canonical and legacy["date"]["normalized"] == "1700"
    assert legacy["identity_truncated"]
    assert [span["id"] for span in legacy["evidence"]] == ["proof", "date-proof"]


def test_entity_index_filters_current_provenance_before_offset_and_literal_alias_search(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    stale = recorded_card(workflow, "a-stale")
    current = recorded_card(workflow, "z-current")
    query = NetworkQuery(workflow.ledger, current_cards=gate(current))
    first = query.entity_index(limit=1)
    assert first["total"] == 3 and len(first["items"]) == 1
    assert all(stale["id"] not in item["card_ids"] for item in first["items"])
    second = query.entity_index(offset=1, limit=1)
    assert len(second["items"]) == 1 and second["items"][0]["id"] != first["items"][0]["id"]
    root = query.entity_index(kind="person")["items"][0]["id"]
    with workflow.ledger.connect(write=True) as connection:
        entity = json.loads(
            connection.execute(
                "SELECT payload FROM research_catalogue_entities WHERE id=?", (root,)
            ).fetchone()[0]
        )
        entity["aliases"] = [
            {
                "name": "甲%_%",
                "evidence_refs": [
                    {"card_id": current["id"], "card_version": 1, "evidence_id": "proof"}
                ],
            },
            {
                "name": "withdrawn",
                "evidence_refs": [
                    {"card_id": stale["id"], "card_version": 1, "evidence_id": "proof"}
                ],
            },
        ]
        connection.execute(
            "UPDATE research_catalogue_entities SET payload=? WHERE id=?",
            (json.dumps(entity), root),
        )
    assert query.entity_index(q="%_%")["items"][0]["id"] == root
    assert query.entity_index(q="withdrawn")["total"] == 0
    assert query.entity_index(book_ids=[])["total"] == 0
    with pytest.raises(ValueError, match="开始日期"):
        query.timeline(date_from="1940", date_to="1936")


def test_same_names_stay_distinct_until_explicit_merge_and_redirects_resolve(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    left, right = recorded_card(workflow, "left"), recorded_card(workflow, "right")
    query = NetworkQuery(workflow.ledger, current_cards=gate(left, right))
    people = query.entity_index(kind="person")["items"]
    assert len(people) == 2 and people[0]["name"] == people[1]["name"]
    refs = [
        {"card_id": owner["id"], "card_version": 1, "evidence_id": "proof"}
        for owner in (left, right)
    ]
    workflow.catalogue.decision(
        people[0]["id"],
        "merge",
        people[0]["version"],
        target_id=people[1]["id"],
        target_version=people[1]["version"],
        evidence_refs=refs,
    )
    assert query.entity_index(kind="person")["total"] == 1
    graph = query.graph(people[1]["id"])
    assert graph["entity_id"] == people[0]["id"]
    with workflow.ledger.connect(write=True) as connection:
        for edge_id, payload in connection.execute(
            "SELECT id,payload FROM research_catalogue_edges WHERE kind='participant'"
        ).fetchall():
            edge = json.loads(payload)
            edge.update(event_status="negated", status_note="Synthetic negation")
            connection.execute(
                "UPDATE research_catalogue_edges SET payload=? WHERE id=?",
                (json.dumps(edge), edge_id),
            )
    participations = query.graph(people[0]["id"], predicate="participant")["edges"]
    assert len(participations) == 2 and all(
        edge["event_status"] == "negated" for edge in participations
    )
