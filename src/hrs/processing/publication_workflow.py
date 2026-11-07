"""Immutable publication versions, revisions and approved library lookup."""

from __future__ import annotations

import json
import shutil
import time
from uuid import uuid4

from hrs.processing.acceptance import publication_acceptance, workspace_scope
from hrs.processing.hashing import digest, file_digest
from hrs.processing.snapshots import atomic_json
from hrs.processing.workflow_rules import (
    new_task,
    require_active,
)


class PublicationWorkflow:
    def __init__(self, context, runs):
        self.context = context
        self.runs = runs

    def snapshot_publication(self, doc: dict) -> dict:
        publication = doc["publication"]
        version = doc.get("revision", 1)
        directory = self.context.repository.directory(doc["id"])
        if file_digest(directory / "document.md") != publication["markdown_sha256"]:
            raise ValueError("Published markdown hash mismatch")
        if json.loads((directory / "publication.json").read_text(encoding="utf-8")) != publication:
            raise ValueError("Published metadata mismatch")
        archive = directory / "revisions" / f"{version:04d}-{digest(publication)[:12]}"
        archive.mkdir(parents=True, exist_ok=True)
        files = {}
        for name in ("document.md", "publication.json"):
            source, target = directory / name, archive / name
            if target.exists():
                if file_digest(target) != file_digest(source):
                    raise ValueError("Archived publication cannot be overwritten")
            else:
                shutil.copyfile(source, target)
            files[name] = {
                "path": target.relative_to(directory).as_posix(),
                "sha256": file_digest(target),
            }
        snapshot = archive / "document.json"
        if not snapshot.exists():
            atomic_json(snapshot, doc)
        files["document.json"] = {
            "path": snapshot.relative_to(directory).as_posix(),
            "sha256": file_digest(snapshot),
        }
        return {"revision": version, "publication": publication, "files": files}

    def published_snapshot(self, doc: dict, record: dict) -> dict:
        for reference in record["files"].values():
            if (
                file_digest(self.context.repository.evidence_path(doc, reference["path"]))
                != reference["sha256"]
            ):
                raise ValueError("Archived publication evidence hash mismatch")
        snapshot = json.loads(
            self.context.repository.evidence_path(
                doc, record["files"]["document.json"]["path"]
            ).read_text(encoding="utf-8")
        )
        if snapshot["publication"] != record["publication"]:
            raise ValueError("Archived publication metadata mismatch")
        if record["files"]["document.md"]["sha256"] != record["publication"]["markdown_sha256"]:
            raise ValueError("Archived markdown hash mismatch")
        if (
            json.loads(
                self.context.repository.evidence_path(
                    doc, record["files"]["publication.json"]["path"]
                ).read_text(encoding="utf-8")
            )
            != record["publication"]
        ):
            raise ValueError("Archived publication receipt mismatch")
        self.context.evidence.verify(snapshot)
        if digest(snapshot["organization"]) != record["publication"]["organization_sha256"]:
            raise ValueError("Archived organization hash mismatch")
        return snapshot

    def begin_revision(
        self,
        document_id: str,
        reason: str,
        pages: list[int] | None = None,
        *,
        user_errata: bool = False,
    ) -> dict:
        """Reopen machine-reviewed pages while the last published version remains readable."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Revision requires a reason")
        with self.context.verified(document_id) as doc:
            if doc["state"] != "published" or not doc.get("publication"):
                raise ValueError("Only a published document can begin a new revision")
            if doc.get("run_request", {}).get("state") == "running":
                raise ValueError("Wait for the active coordinator before starting a revision")
            selected = (
                pages
                if pages is not None
                else [
                    p["page"] for p in doc["pages"] if p["approved"].get("approval_type") != "human"
                ]
            )
            if (
                not selected
                or len(set(selected)) != len(selected)
                or any(type(n) is not int or not 1 <= n <= len(doc["pages"]) for n in selected)
            ):
                raise ValueError("Revision requires valid distinct physical pages")
            for number in selected:
                if not user_errata and any(
                    t["page"] == number
                    and t["stage"] == "human_review"
                    and t.get("receipt", {}).get("approval_type") != "workflow_test"
                    for t in doc["tasks"]
                ):
                    raise ValueError("Human approvals and drafts are preserved across revisions")
            active = doc.get("active_publication") or self.snapshot_publication(doc)
            self.published_snapshot(doc, active)
            doc["active_publication"] = active
            doc.setdefault("published_versions", [])
            if not any(r["revision"] == active["revision"] for r in doc["published_versions"]):
                doc["published_versions"].append(active)
            doc["revision"] = doc.get("revision", 1) + 1
            doc.setdefault("revision_history", []).append(
                {
                    "revision": doc["revision"],
                    "reason": reason.strip(),
                    "pages": selected,
                    "created_at": time.time(),
                }
            )
            for task in doc["tasks"]:
                if task["stage"] == "organization" or (
                    task["page"] in selected and task["stage"] in {"review", "verify"}
                ):
                    task["state"] = "superseded"
            for number in selected:
                page = doc["pages"][number - 1]
                approved = page.pop("approved")
                task = new_task("extraction", number)
                inputs = {
                    "document_id": document_id,
                    "revision": doc["revision"],
                    "previous_approval": approved,
                }
                result = {"markdown": approved["markdown"]}
                receipt = {
                    "task_id": task["task_id"],
                    "worker_id": "local:revision",
                    "approval_type": "extraction",
                    "human_approval": False,
                    "input_sha256": digest(inputs),
                    "created_at": time.time(),
                }
                task.update(
                    state="completed",
                    inputs=inputs,
                    claim={"input_sha256": digest(inputs)},
                    result=result,
                    receipt={**receipt, "result_sha256": digest(result)},
                )
                page.update(
                    ocr={**receipt, "markdown": approved["markdown"], "sha256": approved["sha256"]},
                    state="awaiting_review",
                )
                page.pop("candidate", None)
                page.pop("review", None)
                page.pop("automatic_review_attempts", None)
                doc["tasks"].extend([task, new_task("review", number)])
            doc.update(state="processing", publication=None, organization=None)
            self.context.repository.write(doc)
            return {
                "document_id": document_id,
                "revision": doc["revision"],
                "pages": selected,
                "state": doc["state"],
                "active_publication": active,
            }

    def propose_revision(self, document_id: str, changes: list[dict]) -> dict:
        """Retain explicitly located errata as candidates for independent Sol review."""
        with self.context.verified(document_id) as doc:
            if doc["state"] != "published" or not changes:
                raise ValueError("Errata require a published source and explicit changes")
            by_page = {}
            for change in changes:
                number, start, before, after = (
                    change.get(k) for k in ("page", "start", "before", "after")
                )
                if (
                    type(number) is not int
                    or not 1 <= number <= len(doc["pages"])
                    or type(start) is not int
                ):
                    raise ValueError("Errata require an exact physical page and character offset")
                text = doc["pages"][number - 1]["approved"]["markdown"]
                if (
                    not isinstance(before, str)
                    or not before
                    or not isinstance(after, str)
                    or start < 0
                    or text[start : start + len(before)] != before
                ):
                    raise ValueError("Erratum does not match current approved source")
                by_page.setdefault(number, []).append(change)
            replacements = {}
            for number, edits in by_page.items():
                edits.sort(key=lambda e: e["start"])
                if any(
                    a["start"] + len(a["before"]) > b["start"] for a, b in zip(edits, edits[1:])
                ):
                    raise ValueError("Errata overlap")
                value = doc["pages"][number - 1]["approved"]["markdown"]
                for edit in reversed(edits):
                    value = (
                        value[: edit["start"]]
                        + edit["after"]
                        + value[edit["start"] + len(edit["before"]) :]
                    )
                replacements[number] = value
            self.begin_revision(
                document_id,
                "用户提交定位勘误，需重新对照原图审核",
                pages=sorted(by_page),
                user_errata=True,
            )
            doc = self.context.repository.read(document_id)
            for number, value in replacements.items():
                page = doc["pages"][number - 1]
                task = new_task("extraction", number)
                inputs = {
                    "document_id": document_id,
                    "revision": doc["revision"],
                    "source_sha256": doc["source"]["sha256"],
                    "image_sha256": page["image_sha256"],
                    "previous_candidate": page["ocr"],
                    "changes": by_page[number],
                }
                result = {"markdown": value}
                receipt = {
                    "task_id": task["task_id"],
                    "worker_id": "local:errata-proposal",
                    "input_sha256": digest(inputs),
                    "result_sha256": digest(result),
                    "approval_type": "extraction",
                    "human_approval": False,
                    "origin": "explicit_local_errata_proposal",
                    "created_at": time.time(),
                }
                task.update(
                    state="completed",
                    inputs=inputs,
                    claim={"input_sha256": digest(inputs)},
                    result=result,
                    receipt=receipt,
                )
                doc["tasks"].append(task)
                page["ocr"] = {**receipt, "markdown": value, "sha256": digest(value)}
            self.context.repository.write(doc)
            self.runs.request_run(document_id, resume=True)
            return {
                "document_id": document_id,
                "revision": doc["revision"],
                "pages": sorted(by_page),
                "state": "queued_for_independent_review",
                "content_approved": False,
            }

    def publish(self, document_id: str) -> dict:
        with self.context.verified(document_id) as doc:
            require_active(doc)
            if not all(p["state"] == "approved" for p in doc["pages"]) or not doc["organization"]:
                raise ValueError(
                    "Whole document must have approved pages and completed organization"
                )
            if any(
                t["stage"] == "navigation" and t["state"] in {"ready", "leased"}
                for t in doc["tasks"]
            ):
                raise ValueError(
                    "Complete directory parsing before publication; mapping conflicts may remain"
                )
            if doc["state"] == "published":
                if doc.get("active_publication"):
                    self.published_snapshot(doc, doc["active_publication"])
                publication = doc["publication"]
                directory = self.context.repository.directory(document_id)
                if file_digest(directory / "document.md") != publication["markdown_sha256"]:
                    raise ValueError("Published markdown hash mismatch")
                retained = json.loads((directory / "publication.json").read_text(encoding="utf-8"))
                if (
                    retained != publication
                    or digest(doc["organization"]) != publication["organization_sha256"]
                ):
                    raise ValueError("Publication evidence hash mismatch")
                return doc["publication"]
            from hrs.processing.navigation_coverage import coverage_report

            if not coverage_report(doc)["coverage_complete"]:
                raise ValueError("Complete directory page coverage before publication")
            content = "\n\n".join(
                f"<!-- physical-page: {p['page']} image-sha256: {p['image_sha256']} -->\n\n{p['approved']['markdown']}"
                for p in doc["pages"]
            )
            directory = self.context.repository.directory(document_id)
            markdown_path = directory / "document.md"
            temporary = directory / f".document.{uuid4().hex}.tmp"
            temporary.write_text(content, encoding="utf-8", newline="\n")
            temporary.replace(markdown_path)
            publication = {
                "document_id": document_id,
                "markdown_path": str(markdown_path),
                "markdown_sha256": digest(content),
                "source_sha256": doc["source"]["sha256"],
                "organization_sha256": digest(doc["organization"]),
                "approval_type": "mixed"
                if any(p["approved"]["approval_type"] == "human" for p in doc["pages"])
                else "machine",
                "human_approval": False,
                "human_reviewed_pages": [
                    p["page"] for p in doc["pages"] if p["approved"]["approval_type"] == "human"
                ],
                "published_at": time.time(),
            }
            test_pages = [
                p["page"] for p in doc["pages"] if p["approved"]["approval_type"] == "workflow_test"
            ]
            if test_pages:
                publication.update(
                    approval_type="workflow_test",
                    content_verified=False,
                    workflow_test_pages=test_pages,
                )
            if (
                publication_acceptance(doc, include_active=False) == "workflow_test"
                or workspace_scope(self.context.repository.root) == "workflow_only"
            ):
                publication.update(validation_scope="workflow_only", content_verified=False)
            atomic_json(directory / "publication.json", publication)
            doc.update(state="published", publication=publication)
            if "revision" in doc:
                active = self.snapshot_publication(doc)
                doc["active_publication"] = active
                doc.setdefault("published_versions", []).append(active)
            self.context.repository.write(doc)
            return publication

    def search(self, query: str, limit: int = 20) -> list[dict]:
        if not query.strip() or not 1 <= limit <= 100:
            raise ValueError("Nonempty query and limit between 1 and 100 are required")
        results = []
        for document_id in self.context.repository.ids():
            doc = self.context.repository.read(document_id)
            revision_pending = bool(doc.get("active_publication") and doc["state"] != "published")
            revision_reason = (
                doc.get("revision_history", [{}])[-1].get("reason") if revision_pending else None
            )
            if doc.get("active_publication"):
                doc = self.published_snapshot(doc, doc["active_publication"])
            elif doc["state"] != "published":
                continue
            self.context.evidence.verify(doc)
            for page in doc["pages"]:
                markdown = page["approved"]["markdown"]
                at = markdown.casefold().find(query.casefold())
                if at < 0:
                    continue
                results.append(
                    {
                        "document_id": doc["id"],
                        "title": doc["title"],
                        "page": page["page"],
                        "excerpt": markdown[max(0, at - 80) : at + len(query) + 160],
                        "image": str(self.context.repository.evidence_path(doc, page["image"])),
                        "image_sha256": page["image_sha256"],
                        "markdown_sha256": page["approved"]["sha256"],
                        "revision_pending": revision_pending,
                        "revision_reason": revision_reason,
                    }
                )
                if len(results) >= limit:
                    return results
        return results
