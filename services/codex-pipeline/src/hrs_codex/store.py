"""Durable local work queue and evidence library for Codex subagents."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from uuid import uuid4

from filelock import FileLock

STAGES = {"ocr", "review", "verify", "organization"}


def digest(value: bytes | str | dict | list) -> str:
    if not isinstance(value, bytes):
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        # ponytail: one process lock serializes local document mutations; split by
        # document only if measured local queue contention warrants it.
        self.lock = FileLock(self.root / ".store.lock", timeout=30)

    def _directory(self, document_id: str) -> Path:
        if len(document_id) != 24 or any(c not in "0123456789abcdef" for c in document_id):
            raise ValueError("Invalid document ID")
        return self.root / "documents" / document_id

    def _load(self, document_id: str) -> dict:
        return json.loads(
            (self._directory(document_id) / "document.json").read_text(encoding="utf-8")
        )

    def _save(self, doc: dict) -> None:
        atomic_json(self._directory(doc["id"]) / "document.json", doc)

    def _path(self, doc: dict, relative: str) -> Path:
        directory = self._directory(doc["id"]).resolve()
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("Evidence path escapes document directory")
        return path

    @staticmethod
    def _new_task(stage: str, page: int | None = None) -> dict:
        return {"task_id": uuid4().hex, "stage": stage, "page": page, "state": "ready"}

    def register_manifest(self, manifest: dict, title: str = "") -> dict:
        return self.register(
            Path(manifest["source"]["path"]),
            manifest["pages"],
            title=title,
            expected_source_sha256=manifest["source"]["sha256"],
            original_name=manifest["source"].get("original_name"),
            media_type=manifest["source"].get("media_type"),
        )

    def register(
        self,
        source: Path,
        pages: list[dict],
        title: str = "",
        *,
        expected_source_sha256: str | None = None,
        original_name: str | None = None,
        media_type: str | None = None,
    ) -> dict:
        source = Path(source).resolve()
        source_hash = file_digest(source)
        if expected_source_sha256 is not None and source_hash != expected_source_sha256:
            raise ValueError("Source hash mismatch")
        document_id = source_hash[:24]
        if not pages or [p["page"] for p in pages] != list(range(1, len(pages) + 1)):
            raise ValueError("Pages must be a nonempty consecutive physical page sequence")
        with self.lock:
            directory = self._directory(document_id)
            if (directory / "document.json").exists():
                existing = self._load(document_id)
                self._verify_evidence(existing)
                return existing
            directory.mkdir(parents=True, exist_ok=True)
            retained_source = directory / ("source" + source.suffix.lower())
            if source != retained_source.resolve():
                shutil.copyfile(source, retained_source)
            retained_pages = []
            for page in pages:
                image = Path(page["image"]).resolve()
                image_hash = file_digest(image)
                if page.get("image_sha256", page.get("sha256", image_hash)) != image_hash:
                    raise ValueError("Page image hash mismatch")
                target = directory / "pages" / f"{page['page']:05d}{image.suffix.lower()}"
                target.parent.mkdir(parents=True, exist_ok=True)
                if image != target.resolve():
                    shutil.copyfile(image, target)
                retained_pages.append(
                    {
                        "page": page["page"],
                        "image": target.relative_to(directory).as_posix(),
                        "image_sha256": image_hash,
                        "state": "awaiting_ocr",
                    }
                )
            doc = {
                "schema_version": 1,
                "id": document_id,
                "document_id": document_id,
                "title": title or Path(original_name or source.name).stem,
                "created_at": time.time(),
                "state": "processing",
                "source": {
                    "path": retained_source.name,
                    "sha256": source_hash,
                    "original_name": original_name or source.name,
                    "media_type": media_type,
                },
                "pages": retained_pages,
                "tasks": [self._new_task("ocr", p["page"]) for p in retained_pages],
                "organization": None,
                "publication": None,
            }
            self._save(doc)
            return doc

    def _verify_evidence(self, doc: dict) -> None:
        tasks = {t["task_id"]: t for t in doc["tasks"]}
        if file_digest(self._path(doc, doc["source"]["path"])) != doc["source"]["sha256"]:
            raise ValueError("Retained original source hash mismatch")
        for page in doc["pages"]:
            if file_digest(self._path(doc, page["image"])) != page["image_sha256"]:
                raise ValueError(f"Original page {page['page']} image hash mismatch")
            for field in ("ocr", "candidate", "approved"):
                text = page.get(field)
                if text and digest(text["markdown"]) != text["sha256"]:
                    raise ValueError(f"Page {page['page']} {field} text hash mismatch")
                if text:
                    task = tasks[text["task_id"]]
                    retained = (
                        task["inputs"]["markdown"]
                        if field == "approved" and task["stage"] != "human_review"
                        else task["result"]["markdown"]
                    )
                    if text["markdown"] != retained:
                        raise ValueError(
                            f"Page {page['page']} {field} differs from its retained machine result"
                        )
        for task in doc["tasks"]:
            if task.get("inputs") and digest(task["inputs"]) != task["claim"]["input_sha256"]:
                raise ValueError("Retained task input hash mismatch")
            if (
                task.get("result") is not None
                and digest(task["result"]) != task["receipt"]["result_sha256"]
            ):
                raise ValueError("Retained task result hash mismatch")
            if task.get("result") is not None:
                self._verify_details(doc, task, task["result"])
        organization = doc.get("organization")
        if organization:
            task = next(t for t in doc["tasks"] if t["task_id"] == organization["task_id"])
            if organization["articles"] != task["result"]["articles"]:
                raise ValueError("Organization differs from its retained machine result")

    def _verify_details(self, doc: dict, task: dict, result: dict) -> None:
        evidence = result.get("original_details", [])
        if not isinstance(evidence, list):
            raise ValueError("Detail evidence must be a list")
        for detail in evidence:
            if (
                not isinstance(detail, dict)
                or detail.get("document_id") != doc["id"]
                or detail.get("source_sha256") != doc["source"]["sha256"]
                or detail.get("page") not in [p["page"] for p in doc["pages"]]
                or (task["page"] is not None and detail.get("page") != task["page"])
            ):
                raise ValueError("Detail evidence source/page mismatch")
            for field in ("image", "metadata"):
                path = detail.get(field)
                if not isinstance(path, str) or file_digest(self._path(doc, path)) != detail.get(
                    field + "_sha256"
                ):
                    raise ValueError("Detail evidence file hash mismatch")
            retained = json.loads(self._path(doc, detail["metadata"]).read_text(encoding="utf-8"))
            if {
                k: v for k, v in detail.items() if k not in {"metadata", "metadata_sha256"}
            } != retained:
                raise ValueError("Detail evidence metadata mismatch")

    def status(self, document_id: str) -> dict:
        with self.lock:
            doc = self._load(document_id)
            doc["directory"] = str(self._directory(document_id))
            doc["counts"] = {
                state: sum(t["state"] == state for t in doc["tasks"])
                for state in ("ready", "leased", "completed", "pending", "superseded")
            }
            return doc

    def list_documents(self) -> list[dict]:
        with self.lock:
            documents = []
            for path in sorted((self.root / "documents").glob("*/document.json")):
                doc = json.loads(path.read_text(encoding="utf-8"))
                documents.append({key: doc[key] for key in ("id", "title", "state", "created_at")})
            return documents

    def _inputs(self, doc: dict, task: dict) -> dict:
        inputs = {
            "document_id": doc["id"],
            "source_sha256": doc["source"]["sha256"],
            "stage": task["stage"],
        }
        if task["page"] is not None:
            page = doc["pages"][task["page"] - 1]
            inputs.update(
                page=page["page"],
                image=str(self._path(doc, page["image"])),
                image_sha256=page["image_sha256"],
            )
            if task["stage"] in {"review", "verify"}:
                text = page["candidate" if task["stage"] == "verify" else "ocr"]
                inputs.update(
                    markdown=text["markdown"],
                    markdown_sha256=text["sha256"],
                    prior_worker_id=text["worker_id"],
                )
                if task["stage"] == "verify":
                    inputs["original_markdown"] = page["ocr"]["markdown"]
                    inputs["corrections"] = page["candidate"]["corrections"]
        else:
            inputs["pages"] = [
                {
                    "page": p["page"],
                    "image": str(self._path(doc, p["image"])),
                    "image_sha256": p["image_sha256"],
                    "markdown": p["approved"]["markdown"],
                    "markdown_sha256": p["approved"]["sha256"],
                }
                for p in doc["pages"]
            ]
        return inputs

    def get_task(self, document_id: str, task_id: str) -> dict:
        with self.lock:
            doc = self._load(document_id)
            task = next(t for t in doc["tasks"] if t["task_id"] == task_id)
            inputs = task["inputs"] if "inputs" in task else self._inputs(doc, task)
            return {**task, "inputs": inputs}

    def claim_task(
        self, document_id: str, stage: str, worker_id: str, lease_seconds: int = 900
    ) -> dict | None:
        if stage not in STAGES or not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("A valid stage and nonempty worker ID are required")
        if not 1 <= lease_seconds <= 86400:
            raise ValueError("Lease must be between 1 and 86400 seconds")
        with self.lock:
            doc = self._load(document_id)
            self._verify_evidence(doc)
            for task in doc["tasks"]:
                if task["stage"] != stage or task["state"] not in {"ready", "leased"}:
                    continue
                if task["state"] == "leased" and task["claim"]["expires_at"] > time.time():
                    continue
                inputs = self._inputs(doc, task)
                if inputs.get("prior_worker_id") == worker_id:
                    raise ValueError(
                        "Review and correction verification require a separate subagent"
                    )
                claim = {
                    "worker_id": worker_id,
                    "lease_token": uuid4().hex,
                    "expires_at": time.time() + lease_seconds,
                    "input_sha256": digest(inputs),
                }
                task.update(state="leased", claim=claim, inputs=inputs)
                self._save(doc)
                return {**task, **claim}
            return None

    @staticmethod
    def _review_result(result: dict) -> str:
        verdict = result.get("verdict")
        if verdict not in {"approved", "pending", "corrections"}:
            raise ValueError("Review verdict must be approved, pending or corrections")
        if not isinstance(result.get("issues", []), list):
            raise ValueError("Review issues must be a list")
        if not isinstance(result.get("normalizations", []), list):
            raise ValueError("Review normalizations must be a list")
        if verdict == "approved" and result.get("issues"):
            raise ValueError("Unresolved issues cannot be approved")
        if verdict == "pending" and not result.get("issues"):
            raise ValueError("Pending review requires an issue explaining the unresolved content")
        return verdict

    def submit_task(self, document_id: str, task_id: str, lease_token: str, result: dict) -> dict:
        with self.lock:
            doc = self._load(document_id)
            self._verify_evidence(doc)
            task = next(t for t in doc["tasks"] if t["task_id"] == task_id)
            claim = task.get("claim", {})
            if task["state"] != "leased" or claim.get("lease_token") != lease_token:
                raise ValueError("Task does not have this active lease")
            if claim["expires_at"] <= time.time():
                raise ValueError("Task lease expired; reclaim before submitting")
            if (
                result.get("input_sha256") != claim["input_sha256"]
                or digest(self._inputs(doc, task)) != claim["input_sha256"]
            ):
                raise ValueError("Task input hash mismatch")
            self._verify_details(doc, task, result)
            stage = task["stage"]
            page = doc["pages"][task["page"] - 1] if task["page"] is not None else None
            receipt = {
                "task_id": task_id,
                "worker_id": claim["worker_id"],
                "input_sha256": claim["input_sha256"],
                "approval_type": "machine",
                "human_approval": False,
                "created_at": time.time(),
            }
            if stage == "ocr":
                markdown = result.get("markdown")
                if not isinstance(markdown, str):
                    raise ValueError(
                        "OCR result requires markdown (empty is valid for a blank page)"
                    )
                page["ocr"] = {**receipt, "markdown": markdown, "sha256": digest(markdown)}
                page["state"] = "awaiting_review"
                doc["tasks"].append(self._new_task("review", page["page"]))
            elif stage in {"review", "verify"}:
                verdict = self._review_result(result)
                if stage == "verify" and verdict == "corrections":
                    raise ValueError("Failed correction verification must remain pending")
                text = page["candidate" if stage == "verify" else "ocr"]
                if verdict == "approved":
                    if result.get("markdown", text["markdown"]) != text["markdown"]:
                        raise ValueError(
                            "Changed text requires corrections and separate verification"
                        )
                    page["approved"] = {**text, **receipt}
                    page["state"] = "approved"
                elif verdict == "pending":
                    page["state"] = "pending"
                else:
                    markdown, corrections = result.get("markdown"), result.get("corrections")
                    if not isinstance(markdown, str) or markdown == text["markdown"]:
                        raise ValueError("Corrections require changed markdown")
                    if not isinstance(corrections, list) or not corrections:
                        raise ValueError("Corrections require uniquely located change records")
                    spans = []
                    for correction in corrections:
                        if (
                            not isinstance(correction, dict)
                            or not all(
                                isinstance(correction.get(k), str) and correction[k]
                                for k in ("before", "reason")
                            )
                            or not isinstance(correction.get("after"), str)
                        ):
                            raise ValueError("Each correction requires before, after and reason")
                        start = text["markdown"].find(correction["before"])
                        if start < 0 or text["markdown"].find(correction["before"], start + 1) >= 0:
                            raise ValueError("Correction is not uniquely located in original OCR")
                        spans.append(
                            (start, start + len(correction["before"]), correction["after"])
                        )
                    spans.sort()
                    if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
                        raise ValueError("Correction spans overlap in original OCR")
                    patched = text["markdown"]
                    for start, end, replacement in reversed(spans):
                        patched = patched[:start] + replacement + patched[end:]
                    if patched != markdown:
                        raise ValueError(
                            "Change records must explain the entire corrected markdown"
                        )
                    page["candidate"] = {
                        **receipt,
                        "markdown": markdown,
                        "sha256": digest(markdown),
                        "corrections": corrections,
                    }
                    page["state"] = "awaiting_verification"
                    doc["tasks"].append(self._new_task("verify", page["page"]))
                page["review"] = {
                    **receipt,
                    "verdict": verdict,
                    "issues": result.get("issues", []),
                    "normalizations": result.get("normalizations", []),
                }
            else:
                if result.get("verdict") == "pending":
                    self._review_result(result)
                    doc["state"] = "pending"
                else:
                    if result.get("verdict", "approved") != "approved" or result.get("issues"):
                        raise ValueError("Organization with unresolved issues must remain pending")
                    articles = result.get("articles")
                    if not isinstance(articles, list) or not articles:
                        raise ValueError("Organization requires nonempty articles")
                    covered = set()
                    for article in articles:
                        if (
                            not isinstance(article.get("title"), str)
                            or not article["title"].strip()
                        ):
                            raise ValueError("Article title is required")
                        numbers = article.get("pages")
                        if (
                            not isinstance(numbers, list)
                            or not numbers
                            or any(
                                type(p) is not int or not 1 <= p <= len(doc["pages"])
                                for p in numbers
                            )
                        ):
                            raise ValueError("Article page mapping is invalid")
                        covered.update(numbers)
                        article["bibliography"] = {
                            **article.get("bibliography", {}),
                            "status": "unclaimed",
                        }
                    if covered != set(range(1, len(doc["pages"]) + 1)):
                        raise ValueError("Organization must account for every physical page")
                    doc["organization"] = {**receipt, "articles": articles}
            task.update(
                state="pending" if result.get("verdict") == "pending" else "completed",
                result=result,
                receipt={**receipt, "result_sha256": digest(result)},
            )
            if all(p["state"] == "approved" for p in doc["pages"]):
                if not any(t["stage"] == "organization" for t in doc["tasks"]):
                    doc["tasks"].append(self._new_task("organization"))
                doc["state"] = (
                    "ready"
                    if doc["organization"]
                    else ("pending" if task["state"] == "pending" else "processing")
                )
            else:
                doc["state"] = (
                    "pending"
                    if any(p["state"] == "pending" for p in doc["pages"])
                    else "processing"
                )
            self._save(doc)
            return {
                "document_id": doc["id"],
                "task_id": task_id,
                "task_state": task["state"],
                "state": doc["state"],
            }

    def retry_task(self, document_id: str, task_id: str) -> dict:
        with self.lock:
            doc = self._load(document_id)
            task = next(t for t in doc["tasks"] if t["task_id"] == task_id)
            if task["state"] != "pending":
                raise ValueError(
                    "Only pending tasks can be retried; active leases expire automatically"
                )
            if task["stage"] == "human_review":
                raise ValueError("A human draft must be resolved in the human review workspace")
            task["state"] = "superseded"
            # A rejected correction needs a new proposal against retained OCR,
            # followed by another independent verification of that proposal.
            stage = "review" if task["stage"] == "verify" else task["stage"]
            replacement = self._new_task(stage, task["page"])
            replacement["retry_of"] = task_id
            doc["tasks"].append(replacement)
            if task["page"] is not None:
                doc["pages"][task["page"] - 1]["state"] = "awaiting_" + stage
            doc["state"] = "processing"
            self._save(doc)
            return replacement

    def human_queue(self) -> list[dict]:
        """All unresolved page reviews are available to people, including repeated failures."""
        with self.lock:
            rows = []
            for path in sorted((self.root / "documents").glob("*/document.json")):
                doc = json.loads(path.read_text(encoding="utf-8"))
                for page in doc["pages"]:
                    if page["state"] != "pending":
                        continue
                    attempts = [
                        t
                        for t in doc["tasks"]
                        if t["page"] == page["page"]
                        and t.get("result", {}).get("verdict") == "pending"
                    ]
                    rows.append(
                        {
                            "document_id": doc["id"],
                            "title": doc["title"],
                            "page": page["page"],
                            "unresolved_attempts": len(attempts),
                            "issues": attempts[-1]["result"]["issues"],
                        }
                    )
            return rows

    def _human_packet(self, doc: dict, number: int) -> dict:
        if type(number) is not int or not 1 <= number <= len(doc["pages"]):
            raise ValueError("Unknown physical page")
        page = doc["pages"][number - 1]
        if page["state"] != "pending" or doc["publication"]:
            raise ValueError("Only unpublished pending pages can receive human review")
        history = [t for t in doc["tasks"] if t["page"] == number]
        pending = next(t for t in reversed(history) if t["state"] == "pending")
        text = (
            pending["result"]["markdown"]
            if pending["stage"] == "human_review"
            else pending["inputs"]["markdown"]
        )
        return {
            "document_id": doc["id"],
            "title": doc["title"],
            "page": number,
            "source_sha256": doc["source"]["sha256"],
            "image_sha256": page["image_sha256"],
            "pending_task_id": pending["task_id"],
            "markdown": text,
            "ocr_markdown": page["ocr"]["markdown"],
            "history": [
                {
                    "task_id": t["task_id"],
                    "stage": t["stage"],
                    "state": t["state"],
                    "result": t.get("result"),
                    "receipt": t.get("receipt"),
                }
                for t in history
            ],
        }

    def human_packet(self, document_id: str, page: int) -> dict:
        with self.lock:
            doc = self._load(document_id)
            self._verify_evidence(doc)
            packet = self._human_packet(doc, page)
            return {**packet, "input_sha256": digest(packet)}

    def record_human_review(
        self,
        document_id: str,
        page: int,
        *,
        input_sha256: str,
        reviewer: str,
        markdown: str,
        note: str,
        verdict: str,
    ) -> dict:
        """Record an explicit local form submission. Deliberately not exposed through MCP."""
        if verdict not in {"approved", "pending"} or not reviewer.strip() or not note.strip():
            raise ValueError(
                "Human review requires a reviewer, reason and approved/pending decision"
            )
        if not isinstance(markdown, str):
            raise ValueError("Human review requires full page Markdown")
        with self.lock:
            doc = self._load(document_id)
            self._verify_evidence(doc)
            packet = self._human_packet(doc, page)
            if digest(packet) != input_sha256:
                raise ValueError("Review input changed; reload before deciding")
            task = self._new_task("human_review", page)
            receipt = {
                "task_id": task["task_id"],
                "worker_id": "human:" + reviewer.strip(),
                "reviewer": reviewer.strip(),
                "input_sha256": input_sha256,
                "approval_type": "human",
                "human_approval": verdict == "approved",
                "sealed_gold_approval": False,
                "origin": "local_human_review_form",
                "created_at": time.time(),
            }
            result = {
                "input_sha256": input_sha256,
                "markdown": markdown,
                "verdict": verdict,
                "note": note.strip(),
                "issues": [note.strip()] if verdict == "pending" else [],
            }
            task.update(
                state="completed" if verdict == "approved" else "pending",
                inputs=packet,
                claim={"input_sha256": input_sha256},
                result=result,
                receipt={**receipt, "result_sha256": digest(result)},
            )
            previous = next(t for t in doc["tasks"] if t["task_id"] == packet["pending_task_id"])
            previous["state"] = "superseded"
            task["review_of"] = previous["task_id"]
            doc["tasks"].append(task)
            target = doc["pages"][page - 1]
            target["review"] = {**receipt, "verdict": verdict, "issues": result["issues"]}
            target["state"] = "approved" if verdict == "approved" else "pending"
            if verdict == "approved":
                target["approved"] = {**receipt, "markdown": markdown, "sha256": digest(markdown)}
            if all(p["state"] == "approved" for p in doc["pages"]):
                doc["tasks"].append(self._new_task("organization"))
                doc["state"] = "processing"
            else:
                doc["state"] = "pending"
            self._save(doc)
            return {
                "document_id": document_id,
                "page": page,
                "state": doc["state"],
                "page_state": target["state"],
                "receipt": task["receipt"],
            }

    def publish(self, document_id: str) -> dict:
        with self.lock:
            doc = self._load(document_id)
            self._verify_evidence(doc)
            if not all(p["state"] == "approved" for p in doc["pages"]) or not doc["organization"]:
                raise ValueError(
                    "Whole document must have approved pages and completed organization"
                )
            if doc["state"] == "published":
                publication = doc["publication"]
                directory = self._directory(document_id)
                if file_digest(directory / "document.md") != publication["markdown_sha256"]:
                    raise ValueError("Published markdown hash mismatch")
                retained = json.loads((directory / "publication.json").read_text(encoding="utf-8"))
                if (
                    retained != publication
                    or digest(doc["organization"]) != publication["organization_sha256"]
                ):
                    raise ValueError("Publication evidence hash mismatch")
                return doc["publication"]
            content = "\n\n".join(
                f"<!-- physical-page: {p['page']} image-sha256: {p['image_sha256']} -->\n\n{p['approved']['markdown']}"
                for p in doc["pages"]
            )
            directory = self._directory(document_id)
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
            atomic_json(directory / "publication.json", publication)
            doc.update(state="published", publication=publication)
            self._save(doc)
            return publication

    def search(self, query: str, limit: int = 20) -> list[dict]:
        if not query.strip() or not 1 <= limit <= 100:
            raise ValueError("Nonempty query and limit between 1 and 100 are required")
        results = []
        with self.lock:
            for path in sorted((self.root / "documents").glob("*/document.json")):
                doc = json.loads(path.read_text(encoding="utf-8"))
                if doc["state"] != "published":
                    continue
                self._verify_evidence(doc)
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
                            "image": str(self._path(doc, page["image"])),
                            "image_sha256": page["image_sha256"],
                            "markdown_sha256": page["approved"]["sha256"],
                        }
                    )
                    if len(results) >= limit:
                        return results
        return results
