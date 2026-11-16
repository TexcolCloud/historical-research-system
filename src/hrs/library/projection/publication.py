"""Publish validated article ranges and index the committed publication."""

import json
import re
from uuid import UUID, uuid5

from sqlalchemy import select

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
        from hrs.search.indexing import BookIndexer, current_index_matches

        result = run["result"] or {}
        projection = result.get("codex_reading_projection") or {}
        publication = (doc.get("publication") or {}).get("markdown_sha256")
        navigation = doc.get("navigation", {}).get("sha256")
        if (
            doc["state"] == "published"
            and result.get("published")
            and projection
            and result.get("codex_publication_sha256") == publication
            and result.get("codex_projection_policy") == PROJECTION_POLICY
            and result.get("codex_projected_navigation_sha256") == navigation
        ):
            with self.engine.connect() as connection:
                rows = list(
                    connection.execute(
                        select(db.chapters).where(db.chapters.c.book_id == run["book_id"])
                    ).mappings()
                )
                title = connection.scalar(
                    select(db.books.c.title).where(db.books.c.id == run["book_id"])
                )
            if current_index_matches(self.settings, run, rows, title):
                return run
            # Index activation also commits indexed-source markers and the deduplicated
            # card outbox event, so a later source change cannot certify an old index.
            BookIndexer(self.settings, self.engine).index(run["id"])
            return get_run(self.engine, run["id"])
        return run
