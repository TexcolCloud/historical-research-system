"""Book navigation reads a compact publication index, not every rendered chapter."""

import json
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

from pydantic import SecretStr

from hrs.library import catalog, chapter_content
from hrs.library.reading_locations import target_in_chapter
from hrs.library.runs.outputs import fingerprint
from hrs.storage.objects import object_reference


def test_many_chapter_targets_read_one_small_locator_and_preserve_exact_results(monkeypatch):
    run_id = str(uuid4())
    objects, reads, rows, bodies, entries = {}, [], [], [], []

    def save(value):
        content = json.dumps(value, ensure_ascii=False).encode()
        ref = object_reference(sha256(content).hexdigest(), len(content), "application/json")
        objects[ref["key"]] = content
        return ref

    for number in range(1, 25):
        text = f"## Title {number}\n\nBody {number}."
        page_sha = sha256(text.encode()).hexdigest()
        body = {
            "id": str(uuid4()),
            "run_id": run_id,
            "text": text,
            "parts": [
                {
                    "span_id": str(number),
                    "start": 0,
                    "end": len(text),
                    "text": text,
                    "source": {
                        "page": number,
                        "pages": [number],
                        "original_start": 0,
                        "original_end": len(text),
                        "markdown_sha256": page_sha,
                        "image_sha256": "a" * 64,
                    },
                }
            ],
            "navigation": {"unrelated_audit": "x" * 100_000},
        }
        bodies.append(body)
        ref = save(body)
        rows.append({"id": body["id"], "run_id": run_id, "pages": [number], "content": ref})
        entries.append(
            {
                "id": str(number),
                "status": "matched",
                "target": {
                    "physical_page": number,
                    "start": 0,
                    "anchor": f"## Title {number}",
                    "markdown_sha256": page_sha,
                    "image_sha256": "a" * 64,
                },
            }
        )
    locators = save(
        {"chapters": [{"id": b["id"], "run_id": run_id, "parts": b["parts"]} for b in bodies]}
    )
    run = {
        "id": run_id,
        "result": {
            "codex_publication_sha256": "published",
            "codex_projected_navigation_sha256": "navigation",
            "codex_reading_projection": {
                "policy": "book-navigation-v1",
                "publication_sha256": "published",
                "navigation_sha256": "navigation",
                "chapters_sha256": fingerprint(
                    [
                        {"id": row["id"], "content": row["content"], "pages": row["pages"]}
                        for row in sorted(rows, key=lambda row: str(row["id"]))
                    ]
                ),
                "locators": locators,
            },
        },
    }

    class Objects:
        def __init__(self, **_):
            pass

        def read_bytes(self, ref):
            reads.append(ref)
            return objects[ref["key"]]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, _):
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))

    library = object.__new__(catalog.Library)
    library.engine = SimpleNamespace(connect=Connection)
    library.settings = SimpleNamespace(
        s3_endpoint="https://test.invalid",
        s3_bucket="test",
        s3_region="test",
        s3_access_key=SecretStr("test"),
        s3_secret_key=SecretStr("secret"),
    )
    monkeypatch.setattr(catalog, "get_run", lambda *_: run)
    monkeypatch.setattr(chapter_content, "S3Objects", Objects)
    chapter_content.cached_content.cache_clear()
    expected = {
        e["id"]: target_in_chapter(b, e["target"]) for e, b in zip(entries, bodies, strict=True)
    }
    actual = library.navigation_targets(run_id, {"entries": entries})
    assert actual == expected
    assert [r["key"] for r in reads] == [locators["key"]], "Loaded full chapters for navigation"
    assert sum(r["byte_length"] for r in reads) < 50_000


def test_navigation_only_projection_keeps_chapter_references_and_writes_one_navigation(monkeypatch):
    from hrs.library.projection import publication
    from hrs.library.projection.facts import PROJECTION_POLICY

    run_id, book_id, chapter_id = [str(uuid4()) for _ in range(3)]
    row = {
        "id": chapter_id,
        "run_id": run_id,
        "book_id": book_id,
        "pages": [7],
        "content": {"sha256": "body", "byte_length": 7},
    }
    previous = {
        "policy": "book-navigation-v1",
        "publication_sha256": "published",
        "navigation_sha256": "old",
        "locators": {"sha256": "locators"},
        "navigation": {"sha256": "old-navigation"},
        "chapters_sha256": fingerprint(
            [{"id": chapter_id, "content": row["content"], "pages": row["pages"]}]
        ),
    }
    run = {
        "id": run_id,
        "book_id": book_id,
        "revision": 4,
        "result": {
            "codex_publication_sha256": "published",
            "codex_projection_policy": PROJECTION_POLICY,
            "codex_projected_navigation_sha256": "old",
            "codex_reading_projection": previous,
        },
    }

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def scalar(self, _):
            return 1

        def execute(self, _):
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [row]))

    writes = []

    class Objects:
        def put_bytes(self, value, **_):
            writes.append(json.loads(value))
            return {"sha256": sha256(value).hexdigest(), "byte_length": len(value)}

    monkeypatch.setattr(
        publication,
        "article_groups",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("Rebuilding canonical chapters for a navigation-only change")
        ),
    )
    service = publication.NativePublication(None, SimpleNamespace(connect=Connection), Objects())
    prepared = service.publish_chapters(
        run,
        {
            "state": "published",
            "publication": {"markdown_sha256": "published"},
            "navigation": {"sha256": "new", "entries": []},
        },
    )
    assert prepared["chapter_rows"] is None
    assert prepared["reading_projection"]["locators"] == previous["locators"]
    assert len(writes) == 1 and writes[0]["navigation_sha256"] == "new"


def test_directory_alias_changes_metadata_without_changing_embedding_source():
    from hrs.search.chunks import retrieval_chunks

    text = "# Canonical article\n\nSource claim and original note."
    chapter = {
        "id": str(uuid4()),
        "book_id": str(uuid4()),
        "run_id": str(uuid4()),
        "title": "Canonical article",
        "text": text,
        "parts": [
            {
                "span_id": "source",
                "start": 0,
                "text": text,
                "source": {"page": 7, "pages": [7], "original_start": 0},
            }
        ],
    }

    def chunks(alias):
        chapter["navigation"] = {
            "entries": [
                {
                    "id": "directory",
                    "level": 1,
                    "path": [alias],
                    "target": {"physical_page": 7, "start": 0},
                }
            ],
            "page_kinds": {"7": "text"},
        }
        return list(retrieval_chunks(chapter, "Source book"))

    first, second = chunks("Mutable alias A"), chunks("Mutable alias B")
    assert first[0]["toc_path"] != second[0]["toc_path"]
    assert [row["retrieval_text"] for row in first] == [row["retrieval_text"] for row in second]
    assert "Canonical article" in first[0]["retrieval_text"]
    assert "Mutable alias" not in first[0]["retrieval_text"]


def test_rerank_calibration_is_invalidated_when_navigation_metadata_changes():
    from hrs.search.search import EVIDENCE_RULE, calibrated_policy
    from hrs.search.settings import MODEL_REVISIONS

    policy = {
        "rule": EVIDENCE_RULE,
        "generation": "body-generation",
        "reranker_revision": MODEL_REVISIONS["BAAI/bge-reranker-v2-m3"],
        "navigation_sha256": "old-directory",
        "min_top_score": 1.0,
        "candidate_limit": 30,
        "rerank_limit": 20,
    }
    saved = {
        "retrieval_generation": "body-generation",
        "retrieval_policy": policy,
        "codex_projected_navigation_sha256": "old-directory",
    }
    assert calibrated_policy(saved) == policy
    assert calibrated_policy({**saved, "codex_projected_navigation_sha256": "new-directory"}) == {}
