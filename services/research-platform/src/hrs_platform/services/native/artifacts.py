"""Prepare immutable page fragments and review evidence before SQL commit."""

import hashlib
import json
import re
from uuid import UUID, uuid5

from hrs_codex.hashing import digest
from hrs_codex.review_locations import locate_issues
from sqlalchemy import select

from hrs_platform import models as db


class ProjectionArtifacts:
    def __init__(self, store, objects, review, engine):
        self.store, self.objects, self.review, self.engine = store, objects, review, engine

    def bundle(self, run, doc):
        previous_files = (
            self.review.read_json(run["conversion"]).get("files", {}) if run.get("conversion") else {}
        )
        files, boundaries, carrier, completion, texts = {}, [], [], [], []
        shared = {(ref["sha256"], ref.get("media_type")): ref for ref in previous_files.values()}

        def saved(name, value, media="application/json"):
            data = (
                value.encode("utf-8")
                if isinstance(value, str)
                else json.dumps(value, ensure_ascii=False).encode("utf-8")
            )
            previous = previous_files.get(name, {})
            sha = hashlib.sha256(data).hexdigest()
            files[name] = previous if previous.get("sha256") == sha else shared.get((sha, media))
            if not files[name]:
                files[name] = self.objects.put_bytes(data, media, run_id=run["id"])
            shared[(sha, media)] = files[name]

        offset = 0
        for page in doc["pages"]:
            selected = page.get("approved") or page.get("candidate") or page.get("ocr")
            text = (selected or {}).get("markdown", "") + "\n\n"
            texts.append(text)
            page_markdown = f"markdown/{page['page']:05d}.md"
            saved(page_markdown, text, "text/markdown; charset=utf-8")
            boundaries.append({"page": page["page"], "start": offset, "end": offset + len(text)})
            offset += len(text)
            name = page["image"]
            previous = previous_files.get(name, {})
            files[name] = (
                previous
                if previous.get("sha256") == page["image_sha256"]
                else self.objects.put_file(self.store.artifact_path(doc, name), "image/png", run_id=run["id"])
            )
            status = (
                "release-accepted"
                if page["state"] == "approved"
                else "unreviewed-source"
                if selected
                else "awaiting-ocr"
            )
            carrier.append(
                {
                    "page": page["page"],
                    "image": name,
                    "markdown": page_markdown,
                    "status": status,
                    "extraction_method": page.get("route", "ocr"),
                    "acceptance_basis": (selected or {}).get("approval_type"),
                }
            )
            reviews = [
                t
                for t in doc["tasks"]
                if t["page"] == page["page"] and t["stage"] == "review" and t.get("result")
            ]
            completion.append(
                {
                    "page": page["page"],
                    "structure": reviews[-1]["result"].get("structure", {}) if reviews else {},
                }
            )

        if doc["state"] == "published":
            saved("document.md", "".join(texts), "text/markdown; charset=utf-8")
        for relative in re.findall(
            r"!\[[^\]]*\]\(((?:pages|evidence/details)/[\w./-]+\.png)\)", "".join(texts)
        ):
            asset = self.store.artifact_path(doc, relative)
            if not asset.is_file():
                raise ValueError("Markdown references a missing retained original image")
            files[relative] = previous_files.get(relative) or self.objects.put_file(
                asset, "image/png", run_id=run["id"]
            )
        saved("pages.json", {"pages": boundaries})
        saved("navigation.json", doc.get("navigation", {}))
        saved("review-cards.json", {"cards": []})
        saved("completion.json", {"pages": completion})
        if (doc.get("organization") or {}).get("reading_structure"):
            saved("paddle-restructure.json", doc["organization"]["reading_structure"])
        task_files = {}
        for task in doc["tasks"]:
            name = f"codex/tasks/{task['task_id']}.json"
            saved(name, task)
            task_files[task["task_id"]] = name
        saved(
            "codex-evidence.json",
            {
                "source": doc["source"],
                "policy": doc.get("processing_policy"),
                "publication": doc["publication"],
                "task_files": task_files,
                **({"tasks": doc["tasks"]} if doc["state"] == "published" else {}),
            },
        )
        manifest = {
            "page_count": len(carrier),
            "pages": carrier,
            "candidate_markdown": "document.md",
            "page_markdown": True,
            "page_boundaries": "pages.json",
            "review_cards": "review-cards.json",
        }
        bundle = self.objects.put_bytes(
            json.dumps({"files": files, "manifest": manifest}, ensure_ascii=False).encode(), run_id=run["id"]
        )
        return bundle, boundaries

    def issues(self, run, doc, pending, boundaries):
        with self.engine.connect() as connection:
            previous_issues = {
                r["id"]: r
                for r in connection.execute(
                    select(db.review_issues).where(db.review_issues.c.run_id == run["id"])
                ).mappings()
            }
        prepared_issues = []
        for page in pending:
            packet = self.store.review_packet(doc, page["page"])
            packet = {**packet, "input_sha256": digest(packet)}
            scope = (
                "codex-page:"
                + (str(doc["revision"]) + ":" if doc.get("revision", 1) > 1 else "")
                + str(page["page"])
            )
            issue_id = str(uuid5(UUID(run["id"]), scope))
            content = {
                "text": packet["markdown"],
                "start": boundaries[page["page"] - 1]["start"],
                "end": boundaries[page["page"] - 1]["end"],
                "pages": [page["page"]],
                "images": [page["image"]],
                "kind": "page",
                "reasons": page["review"]["issues"],
                "codex_input_sha256": packet["input_sha256"],
                "codex_revision": doc.get("revision", 1),
                "proposed_text": packet.get("suggested_markdown"),
                "locations": locate_issues(
                    packet["markdown"],
                    page["review"]["issues"],
                    page=page["page"],
                    image_sha256=page["image_sha256"],
                ),
            }
            data = json.dumps(content, ensure_ascii=False).encode()
            old = previous_issues.get(issue_id)
            if old and (old["state"] != "pending" or old["draft"] is not None):
                continue
            if old and old["content"]["sha256"] == hashlib.sha256(data).hexdigest():
                continue
            ref = self.objects.put_bytes(data, run_id=run["id"])
            prepared_issues.append(
                dict(
                    id=issue_id,
                    run_id=run["id"],
                    scope_key=scope,
                    page=page["page"],
                    state="pending",
                    content=ref,
                    text_sha256=digest(packet["markdown"]),
                )
            )
        return prepared_issues
