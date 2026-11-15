"""Source structure is a revisioned sidecar, never a rewrite of article evidence."""

import json
from copy import deepcopy
from types import SimpleNamespace

from hrs.library import navigation_projection as module
from hrs.library.structure import project_source_structure
from hrs.processing.hashing import digest
from hrs.processing.source_structure import ROLES, structure_snapshot


def source_document():
    text = "刊名\n正文甲\n注文"
    fragments = [
        {
            "fragment_id": "p1:header",
            "role": "header",
            "layer": "source",
            "start": 0,
            "end": 2,
            "text_sha256": digest(text[:2]),
        },
        {
            "fragment_id": "p1:body",
            "role": "body",
            "layer": "source",
            "start": 3,
            "end": 6,
            "text_sha256": digest(text[3:6]),
        },
        {
            "fragment_id": "p1:note",
            "role": "note",
            "layer": "editor_note",
            "start": 7,
            "end": 9,
            "text_sha256": digest(text[7:9]),
        },
    ]
    page = {
        "page": 1,
        "image_sha256": "pixels",
        "approved": {"markdown": text, "sha256": digest(text)},
    }
    page["source_structure"] = {
        "schema_version": 1,
        "image_sha256": "pixels",
        "markdown_sha256": digest(text),
        "coverage": [
            {"role": role, "status": "present" if role in {"header", "body", "note"} else "absent"}
            for role in sorted(ROLES)
        ],
        "fragments": fragments,
        "relations": [
            {
                "kind": "note_of",
                "from_fragment_id": "p1:note",
                "to_fragment_id": "p1:body",
                "status": "verified",
                "evidence": "原件注码",
            }
        ],
        "source_task_id": "review",
        "source_result_sha256": "result",
        "source_reading_sha256": "reading",
    }
    return {
        "id": "doc",
        "source": {"sha256": "source"},
        "structure_version": 3,
        "publication": {"markdown_sha256": "publication"},
        "pages": [
            page,
            {
                "page": 2,
                "image_sha256": "unlabelled-pixels",
                "approved": {"markdown": "无页码正文", "sha256": digest("无页码正文")},
            },
        ],
    }


def parts(document, start=0):
    page = document["pages"][0]
    text = page["approved"]["markdown"][start:]
    return [
        {
            "text": text,
            "source": {
                "pages": [1],
                "page": 1,
                "original_start": start,
                "image_sha256": page["image_sha256"],
                "markdown_sha256": page["approved"]["sha256"],
            },
        }
    ]


def test_exact_page_facts_project_into_chapter_without_printed_page_labels():
    document = source_document()
    snapshot = structure_snapshot(document)
    result = project_source_structure(snapshot, parts(document, 3))
    assert [(b["id"], b["start"], b["end"], b["status"]) for b in result["blocks"]] == [
        ("p1:body", 0, 3, "ready"),
        ("p1:note", 4, 6, "ready"),
    ]
    assert result["relations"] == [
        {
            "kind": "note_of",
            "status": "ready",
            "members": [
                {"start": 4, "end": 6, "fragment_id": "p1:note", "endpoint": "from"},
                {"start": 0, "end": 3, "fragment_id": "p1:body", "endpoint": "to"},
            ],
        }
    ]
    assert [p["page"] for p in snapshot["pages"]] == [1, 2]
    assert snapshot["pages"][1]["status"] == "unreviewed"
    changed = parts(document)
    changed[0]["source"]["image_sha256"] = "different-pixels"
    assert {b["status"] for b in project_source_structure(snapshot, changed)["blocks"]} == {
        "unknown"
    }
    assert (
        project_source_structure({**snapshot, "stale": True}, parts(document))["relations"][0][
            "status"
        ]
        == "unknown"
    )


def test_fragment_hash_and_cross_chapter_relations_are_conservative():
    document = source_document()
    snapshot = structure_snapshot(document)
    chapter = parts(document)
    chapter[0]["text"] = chapter[0]["text"].replace("刊名", "异文")
    result = project_source_structure(snapshot, chapter)
    assert result["blocks"][0]["status"] == "unknown"
    clipped = parts(document, 5)
    result = project_source_structure(snapshot, clipped)
    assert result["relations"][0]["status"] == "unknown"


def test_sidecar_revisions_reuse_canonical_locators_and_bind_source_hashes(monkeypatch):
    document = source_document()
    source_parts = parts(document)
    row = {"id": "chapter", "pages": [1], "content": {"sha256": "canonical"}}
    writes = []

    class Objects:
        def put_bytes(self, value, **_):
            body = json.loads(value)
            writes.append(body)
            return {"sha256": digest(value), "byte_length": len(value), "body": body}

    run = {"id": "run", "source": document["source"], "result": {}}
    objects = Objects()
    first = module.prepare_projection(
        objects, run, document, [row], groups=[{"parts": source_parts}]
    )
    original = deepcopy(row)
    document["structure_version"] += 1
    document["pages"][0]["source_structure"]["relations"][0]["status"] = "unknown"
    writes.clear()
    second = module.prepare_projection(objects, run, document, [row], previous=first)
    assert row == original and second["locators"] == first["locators"]
    assert second["structure_sha256"] != first["structure_sha256"]
    assert len(writes) == 1 and set(writes[0]) == {
        "publication_sha256",
        "chapters_sha256",
        "snapshot",
    }
    assert "parts" not in writes[0] and len(writes[0]["snapshot"]["pages"]) == 2
    run["result"] = {"codex_publication_sha256": "publication", "codex_reading_projection": second}
    monkeypatch.setattr(module, "projection_json", lambda settings, ref: deepcopy(ref["body"]))
    assert module.book_source_structure(SimpleNamespace(), run, [row])["stale"] is False
    run["source"] = {"sha256": "other-source"}
    assert module.book_source_structure(SimpleNamespace(), run, [row])["stale"] is True
