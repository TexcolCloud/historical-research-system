"""Index generations expose complete frozen inputs and reuse unchanged model work."""

import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hrs.library.runs.outputs import fingerprint
from hrs.search import indexing as module


class Rows:
    def __init__(self, values):
        self.values = values

    def mappings(self):
        return self

    def one(self):
        assert len(self.values) == 1
        return deepcopy(self.values[0])

    def all(self):
        return deepcopy(self.values)

    def __iter__(self):
        return iter(deepcopy(self.values))


class Ledger:
    def __init__(self, run, chapters):
        self.run, self.chapters, self.events = run, chapters, []
        self.outputs = None

    def connect(self):
        return self

    begin = connect

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def scalar(self, statement):
        column = str(next(iter(statement.selected_columns)))
        return {
            "books.state": "ready",
            "books.title": "Source title",
            "runs.result": self.run["result"],
        }[column]

    def scalars(self, statement):
        return Rows(
            [
                row["reference"]
                for step, row in self.outputs.rows.items()
                if step.startswith("retrieval-embeddings:")
            ]
        )

    def execute(self, statement):
        if statement.is_select:
            table = statement.get_final_froms()[0].name
            return Rows([self.run] if table == "runs" else self.chapters)
        values = statement.compile().params
        if statement.is_update:
            self.run["result"] = deepcopy(values["result"])
        elif statement.is_insert:
            self.events.append(values)


class Outputs:
    def __init__(self):
        self.rows, self.bytes = {}, {}
        self.objects = SimpleNamespace(read_bytes=lambda ref: self.bytes[ref["sha256"]])

    def get(self, run_id, step, dependency=None):
        row = self.rows.get(step)
        if row and dependency is not None:
            assert row["input_sha256"] == fingerprint(dependency)
        return deepcopy(row["value"]) if row else None

    def put(self, run_id, step, value, dependency):
        value = deepcopy(value)
        key = fingerprint(value)
        self.bytes[key] = json.dumps(value).encode()
        record = {
            "value": value,
            "input_sha256": fingerprint(dependency),
            "reference": {"sha256": key},
        }
        if step in self.rows:
            assert self.rows[step] == record
        self.rows[step] = record
        return deepcopy(value)


@pytest.fixture
def indexer(monkeypatch):
    book, run_id, chapter_id = [str(uuid4()) for _ in range(3)]
    run = {
        "id": run_id,
        "book_id": book,
        "kind": "book",
        "conversion": None,
        "result": {
            "published": True,
            "codex_projected_navigation_sha256": "nav-one",
            "codex_reading_projection": {"structure_sha256": "facts-one"},
        },
    }
    chapters = [
        {
            "id": chapter_id,
            "run_id": run_id,
            "book_id": book,
            "title": "Chapter",
            "content": {"sha256": "canonical"},
            "pages": [1],
        }
    ]
    ledger = Ledger(run, chapters)
    outputs = Outputs()
    ledger.outputs = outputs
    calls = {"chunking": 0, "tokenizer": 0, "embed": []}
    indexer = object.__new__(module.BookIndexer)
    indexer.settings = SimpleNamespace(opensearch_index="synthetic", retrieval_device="cpu")
    indexer.engine, indexer.outputs = ledger, outputs
    indexer.library = SimpleNamespace(chapters=lambda _: deepcopy(chapters))
    docs = {}

    def count(**options):
        filters = options["body"]["query"]["bool"]["filter"]
        generation = filters[1]["term"]["generation"]
        return {"count": sum(row["generation"] == generation for row in docs.values())}

    indexer.client = SimpleNamespace(count=count, docs=docs)
    monkeypatch.setattr(indexer, "ensure_index", lambda _: None)
    monkeypatch.setattr(module, "get_run", lambda *_: deepcopy(run))
    monkeypatch.setattr(
        module,
        "book_source_structure",
        lambda settings, r, rows: {
            "pages": [],
            "snapshot_sha256": r["result"]["codex_reading_projection"]["structure_sha256"],
        },
    )
    monkeypatch.setattr(
        module,
        "book_navigation",
        lambda settings, r, rows: {"sha256": r["result"]["codex_projected_navigation_sha256"]},
    )
    body = {
        "text": "headerbody",
        "parts": [
            {"span_id": "source", "start": 0, "text": "headerbody", "source": {"pages": [1]}}
        ],
    }
    monkeypatch.setattr(module, "chapter_content", lambda *_: deepcopy(body))
    monkeypatch.setattr(
        module, "book_locators", lambda *_: [{**body, "id": chapter_id, "run_id": run_id}]
    )
    monkeypatch.setattr(
        module,
        "navigation_fields",
        lambda chapters, row: {"navigation_version": chapters[chapter_id]["navigation"]["sha256"]},
    )
    monkeypatch.setattr(
        module, "normalizer", lambda: SimpleNamespace(convert=lambda text: "alias:" + text)
    )
    monkeypatch.setattr(module, "bounded_chunks", lambda chapter, chunks, tokenizer: chunks)

    def chunks(chapter, title):
        calls["chunking"] += 1
        return [
            {
                "id": identity,
                "chapter_id": chapter_id,
                "run_id": run_id,
                "book_id": book,
                "title": "Chapter",
                "text": value,
                "raw_text": value,
                "retrieval_text": value if eligible else "",
                "model_eligible": eligible,
                "start": start,
                "end": start + len(value),
            }
            for identity, start, value, eligible in [
                ("header", 0, "header", False),
                ("body", 6, "body", True),
            ]
        ]

    def tokenizer(_):
        calls["tokenizer"] += 1
        return SimpleNamespace(encode=lambda text: SimpleNamespace(ids=list(text)))

    def compute(operation, texts):
        calls["embed"].append(texts)
        return {"vectors": [[1.0] for _ in texts], "identity": {"synthetic": True}}

    def bulk(client, actions, **options):
        actions = list(actions)
        for action in actions:
            docs[action["_id"]] = deepcopy(action["_source"])
        return len(actions), []

    indexer.runtime = SimpleNamespace(tokenizer=tokenizer, compute=compute)
    monkeypatch.setattr(module, "indexable_chunks", chunks)
    monkeypatch.setattr(module.helpers, "bulk", bulk)
    return indexer, ledger, calls, bulk


def test_navigation_reuses_plan_and_vectors_but_commits_an_immutable_generation(indexer):
    service, ledger, calls, _ = indexer
    first = service._index(ledger.run["id"], {})
    before = deepcopy(calls)
    assert service._index(ledger.run["id"], {}) == first
    ledger.run["result"]["codex_projected_navigation_sha256"] = "nav-two"
    second = service.refresh_navigation(ledger.run["id"])
    assert second["generation"] != first["generation"] and calls == before
    assert ledger.run["result"]["retrieval_previous_generation"] == first["generation"]
    assert len(service.client.docs) == 4
    for row in service.client.docs.values():
        assert ("vector" in row) == row["model_eligible"]
        assert row["raw_text_search"] == row["text"]
        assert row["text_aliases"].startswith("alias:")
        assert row["navigation_version"] == (
            "nav-one" if row["generation"] == first["generation"] else "nav-two"
        )
    rolled = service.rollback_generation(ledger.run["id"], second["generation"])
    assert rolled["generation"] == first["generation"]
    with pytest.raises(ValueError, match="Active generation"):
        service.rollback_generation(ledger.run["id"], second["generation"])


def test_freshness_compares_complete_source_and_model_dependencies(indexer):
    service, ledger, _, _ = indexer
    service._index(ledger.run["id"], {})
    assert module.current_index_matches(
        service.settings, ledger.run, ledger.chapters, "Source title"
    )
    assert not module.current_index_matches(
        service.settings, ledger.run, ledger.chapters, "New title"
    )
    service.settings.retrieval_device = "different-device"
    assert not module.current_index_matches(
        service.settings, ledger.run, ledger.chapters, "Source title"
    )


def test_lost_bulk_response_retries_cached_work_and_partial_bulk_stays_invisible(
    indexer, monkeypatch
):
    service, ledger, calls, bulk = indexer
    first = service._index(ledger.run["id"], {})
    ledger.run["result"]["codex_projected_navigation_sha256"] = "nav-two"

    def lost(*args, **options):
        bulk(*args, **options)
        raise OSError("lost response")

    monkeypatch.setattr(module.helpers, "bulk", lost)
    with pytest.raises(OSError, match="lost response"):
        service._index(ledger.run["id"], {})
    assert ledger.run["result"]["retrieval_generation"] == first["generation"]
    monkeypatch.setattr(module.helpers, "bulk", bulk)
    second = service._index(ledger.run["id"], {})
    assert len(calls["embed"]) == 1 and second["generation"] != first["generation"]
    ledger.run["result"]["codex_projected_navigation_sha256"] = "nav-three"
    monkeypatch.setattr(
        module.helpers, "bulk", lambda *args, **options: (1, [{"error": "partial"}])
    )
    with pytest.raises(ValueError, match="complete generation"):
        service._index(ledger.run["id"], {})
    assert ledger.run["result"]["retrieval_generation"] == second["generation"]


@pytest.mark.parametrize("change", ["navigation", "structure", "chapters", "active"])
def test_source_or_active_change_rejects_old_build_at_sql_activation(indexer, monkeypatch, change):
    service, ledger, calls, bulk = indexer
    first = service._index(ledger.run["id"], {})
    ledger.run["result"]["codex_projected_navigation_sha256"] = "nav-two"

    def raced(*args, **options):
        result = bulk(*args, **options)
        if change == "navigation":
            ledger.run["result"]["codex_projected_navigation_sha256"] = "nav-three"
        elif change == "structure":
            ledger.run["result"]["codex_reading_projection"]["structure_sha256"] = "facts-two"
        elif change == "chapters":
            ledger.chapters[0]["content"]["sha256"] = "new-canonical"
        else:
            ledger.run["result"]["retrieval_generation"] = "another-complete-generation"
        return result

    monkeypatch.setattr(module.helpers, "bulk", raced)
    with pytest.raises(ValueError, match="changed"):
        service._index(ledger.run["id"], {})
    assert ledger.run["result"]["retrieval_generation"] == (
        "another-complete-generation" if change == "active" else first["generation"]
    )
    assert len(calls["embed"]) == 1


def test_rollback_refuses_changed_publication_or_incomplete_retained_index(indexer):
    service, ledger, _, _ = indexer
    first = service._index(ledger.run["id"], {})
    ledger.run["result"]["codex_projected_navigation_sha256"] = "nav-two"
    second = service._index(ledger.run["id"], {})
    ledger.chapters[0]["content"]["sha256"] = "changed-publication"
    with pytest.raises(ValueError, match="Source publication"):
        service.rollback_generation(ledger.run["id"], second["generation"])
    ledger.chapters[0]["content"]["sha256"] = "canonical"
    del service.client.docs[first["generation"] + ":body"]
    with pytest.raises(ValueError, match="incomplete"):
        service.rollback_generation(ledger.run["id"], second["generation"])


def test_bridge_does_not_skip_a_projection_rule_upgrade_on_an_unchanged_publication(monkeypatch):
    import time
    from types import SimpleNamespace
    from unittest.mock import Mock

    from hrs.library import processing_bridge as module

    class ReachedIndexUpdate(Exception):
        pass

    bridge = object.__new__(module.CodexBridge)
    bridge.settings = SimpleNamespace(retrieval_device="cpu")
    bridge.engine = None
    document = {
        "id": "document",
        "state": "published",
        "publication": {"markdown_sha256": "pub"},
        "navigation": {"sha256": "nav"},
        "pages": [],
        "tasks": [],
        "organization": None,
    }
    bridge.store = SimpleNamespace(
        status=lambda _: document,
        runtime_control=SimpleNamespace(snapshot=lambda: {}),
        checkpoints=SimpleNamespace(ledger=True),
        verify_evidence=Mock(),
        scheduling_snapshot=lambda _: {"automatic_work_remaining": False},
    )
    bridge.verified_at = {"document": time.monotonic()}
    bridge.publication = SimpleNamespace(index_if_needed=Mock(side_effect=ReachedIndexUpdate))
    bridge.apply_decisions = lambda *_: False
    monkeypatch.setattr(module, "require_workspace", lambda _engine, run, _store: run)
    monkeypatch.setattr(module, "projection_input", lambda *_: "same-input")
    monkeypatch.setattr(module, "fingerprint", lambda *_: "same-sync")
    run = {
        "revision": 1,
        "result": {
            "codex_document_id": "document",
            "codex_projection_input": "same-input",
            "codex_sync_sha256": "same-sync",
            "codex_projected_sql_revision": 1,
            "codex_indexed_publication_sha256": "pub",
            "codex_indexed_navigation_sha256": "nav",
            "codex_indexed_projection_policy": module.PROJECTION_POLICY,
            "retrieval_manifest": {"rules": {"projection_rule": "older-rule"}},
        },
    }
    with pytest.raises(ReachedIndexUpdate):
        bridge.sync(run)
    bridge.store.verify_evidence.assert_called_once_with(document, None)


@pytest.mark.parametrize("projected_revision, expected", [(7, 8), (6, 6)])
def test_native_activation_advances_only_a_current_projection_marker(
    indexer, projected_revision, expected
):
    service, ledger, _, _ = indexer
    ledger.run["revision"] = 7
    ledger.run["result"].update(
        codex_publication_sha256="publication",
        codex_projected_sql_revision=projected_revision,
        content_acceptance="workflow_test",
    )
    service._index(ledger.run["id"], {})
    assert ledger.run["result"]["codex_projected_sql_revision"] == expected


@pytest.mark.parametrize("change", [None, "structure", "chapters", "rules"])
def test_projection_state_uses_the_prepared_reading_snapshot_and_all_index_rules(change):
    from hrs.library.projection.facts import PROJECTION_POLICY, projection_state

    result = {
        "codex_indexed_publication_sha256": "publication",
        "codex_indexed_projection_policy": PROJECTION_POLICY,
        "codex_indexed_navigation_sha256": "navigation",
        "codex_indexed_chapters_sha256": "chapters",
        "codex_indexed_structure_sha256": "structure",
        "codex_reading_projection": {"structure_sha256": "structure"},
        "retrieval_manifest": {"rules": {"all_rules": "current"}},
    }
    projection = {"chapters_sha256": "chapters", "structure_sha256": "structure"}
    rules = {"all_rules": "current"}
    if change == "rules":
        rules["all_rules"] = "next"
    elif change:
        projection[change + "_sha256"] = "next"
    state, stage = projection_state(
        {"result": result},
        {"state": "published", "pages": [], "tasks": []},
        {"automatic_work_remaining": False},
        [],
        "publication",
        "navigation",
        reading_projection=projection,
        index_rules=rules,
    )
    assert (state, stage) == (("ready", "indexing") if change else ("completed", "complete"))
