"""Publish validated article ranges and index the committed publication."""

import json
import re
from uuid import UUID, uuid5

from sqlalchemy import func, insert, select, update

from hrs.library.books import get_run
from hrs.library.navigation_projection import prepare_projection
from hrs.library.projection.facts import PROJECTION_POLICY
from hrs.library.structure import describe_structure, project_structure
from hrs.processing.acceptance import publication_acceptance
from hrs.storage import models as db


def article_groups(document, run_id):
    """Adapt validated native article ranges to the existing immutable chapter contract."""
    from hrs.processing.organization import article_parts

    articles = document["organization"]["articles"]
    groups = []
    for article, source_parts in zip(
        articles, article_parts(document["pages"], articles), strict=True
    ):
        parts = []
        for part in source_parts:
            page = document["pages"][part["page"] - 1]
            parts.append(
                {
                    "span_id": str(
                        uuid5(UUID(run_id), f"codex:{part['page']}:{part['start']}:{part['end']}")
                    ),
                    "start": part["start"],
                    "end": part["end"],
                    "text": part["text"],
                    "source": {
                        "pages": [part["page"]],
                        "page": part["page"],
                        "original_start": part["original_start"],
                        "original_end": part["original_end"],
                        "image_sha256": page["image_sha256"],
                        "markdown_sha256": page["approved"]["sha256"],
                        "approval_type": page["approved"]["approval_type"],
                    },
                }
            )
        groups.append(
            {
                "title": article["title"],
                "kind": article.get("kind", "article"),
                "parts": parts,
                "bibliography": article.get("bibliography", {"status": "unclaimed"}),
            }
        )
    return groups


class NativePublication:
    def __init__(self, settings, engine, objects):
        self.settings, self.engine, self.objects = settings, engine, objects

    def publish_chapters(self, run, doc):
        if doc["state"] != "published":
            return
        with self.engine.connect() as connection:
            current = list(
                connection.execute(
                    select(db.chapters)
                    .where(db.chapters.c.run_id == run["id"])
                    .order_by(db.chapters.c.position)
                )
                .mappings()
                .all()
            )
        organization_sha = doc["publication"].get("organization_sha256")
        previous_organization = run["result"].get("codex_organization_sha256")
        if current and organization_sha and previous_organization is None:
            retained = json.loads(self.objects.read_bytes(current[0]["content"]))
            previous_organization = (
                retained.get("acceptance", {}).get("publication", {}).get("organization_sha256")
            )
        if (
            current
            and run["result"].get("codex_publication_sha256")
            == doc["publication"]["markdown_sha256"]
            and run["result"].get("codex_projection_policy") == PROJECTION_POLICY
            and (not organization_sha or previous_organization == organization_sha)
        ):
            return {
                "chapter_rows": None,
                "reading_projection": prepare_projection(
                    self.objects,
                    run,
                    doc,
                    current,
                    previous=run["result"].get("codex_reading_projection"),
                ),
            }
        groups = article_groups(doc, run["id"])
        parts = sorted([p for g in groups for p in g["parts"]], key=lambda p: p["start"])
        text = "".join(p["text"] for p in parts)
        titles = [
            {"page": p["source"]["pages"][0], "title": m.group().strip(), "level": len(m[1])}
            for p in parts
            for m in re.finditer(r"(?m)^(#{1,6})\s+[^\n]+", p["text"])
        ]
        metadata = {**doc["organization"].get("reading_structure", {}), "titles": titles}
        completion = []
        for page in doc["pages"]:
            reviews = [
                t
                for t in doc["tasks"]
                if t["stage"] == "review" and t["page"] == page["page"] and t.get("result")
            ]
            completion.append(
                {
                    "page": page["page"],
                    "structure": reviews[-1]["result"].get("structure", {}) if reviews else {},
                }
            )
        structure = describe_structure(
            {"text": text, "parts": parts}, metadata, completion, {p["page"] for p in doc["pages"]}
        )
        rows = []
        for position, group in enumerate(groups):
            group["acceptance"] = {
                "content_acceptance": publication_acceptance(doc),
                "publication": doc["publication"],
            }
            group["structure"] = project_structure(structure, group["parts"])
            ref = self.objects.put_bytes(
                json.dumps(group, ensure_ascii=False).encode(), run_id=run["id"]
            )
            rows.append(
                {
                    "id": str(uuid5(UUID(run["id"]), f"chapter:{position}")),
                    "book_id": run["book_id"],
                    "run_id": run["id"],
                    "position": position,
                    "title": group["title"],
                    "kind": group["kind"],
                    "content": ref,
                    "pages": sorted(
                        {p for part in group["parts"] for p in part["source"]["pages"]}
                    ),
                    "codepoints": sum(len(p["text"]) for p in group["parts"]),
                }
            )
        return {
            "chapter_rows": rows,
            "reading_projection": prepare_projection(self.objects, run, doc, rows, groups=groups),
        }

    def index_if_needed(self, run, doc):
        from hrs.search.chunks import CHUNK_RULE
        from hrs.search.inputs import INPUT_RULE

        publication_sha = (doc.get("publication") or {}).get("markdown_sha256")
        navigation_sha = doc.get("navigation", {}).get("sha256")
        chapter_sha = (run["result"].get("codex_reading_projection") or {}).get("chapters_sha256")
        if (
            doc["state"] == "published"
            and run["result"].get("published")
            and run["result"].get("codex_publication_sha256") == publication_sha
            and run["result"].get("codex_projection_policy") == PROJECTION_POLICY
            and (
                run["result"].get("codex_indexed_publication_sha256") != publication_sha
                or run["result"].get("codex_indexed_projection_policy") != PROJECTION_POLICY
                or run["result"].get("codex_indexed_navigation_sha256") != navigation_sha
                or run["result"].get("codex_indexed_chunk_rule") != CHUNK_RULE
                or run["result"].get("codex_indexed_chapters_sha256") != chapter_sha
            )
            and run["result"].get("codex_projected_navigation_sha256") == navigation_sha
            and run["result"].get("codex_reading_projection")
        ):
            from hrs.search.indexing import BookIndexer

            indexer = BookIndexer(self.settings, self.engine)
            if (
                run["result"].get("codex_indexed_publication_sha256") == publication_sha
                and run["result"].get("codex_indexed_projection_policy") == PROJECTION_POLICY
                and run["result"].get("codex_indexed_chunk_rule") == CHUNK_RULE
                and run["result"].get("retrieval_input_rule") == INPUT_RULE
                and run["result"].get("codex_indexed_chapters_sha256") == chapter_sha
            ):
                indexer.refresh_navigation(run["id"])
            else:
                indexer.index(run["id"])
            indexed = get_run(self.engine, run["id"])["result"]
            with self.engine.begin() as connection:
                connection.execute(
                    update(db.runs)
                    .where(db.runs.c.id == run["id"])
                    .values(
                        state="completed",
                        stage="complete",
                        revision=db.runs.c.revision + 1,
                        updated_at=func.now(),
                        error=None,
                        result={
                            **indexed,
                            "codex_indexed_publication_sha256": publication_sha,
                            "codex_indexed_projection_policy": PROJECTION_POLICY,
                            "codex_indexed_navigation_sha256": navigation_sha,
                            "codex_indexed_chunk_rule": CHUNK_RULE,
                            "codex_indexed_chapters_sha256": chapter_sha,
                            "bridge_failures": 0,
                            "bridge_not_before": 0,
                        },
                    )
                )
                connection.execute(
                    insert(db.events).values(
                        book_id=run["book_id"],
                        run_id=run["id"],
                        kind="run.changed",
                        payload={
                            "state": "completed",
                            "stage": "complete",
                            "revision": run["revision"] + 1,
                        },
                    )
                )
            run = get_run(self.engine, run["id"])
        return run
