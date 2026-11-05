"""Original, detail, candidate and decision validation over retained snapshots."""

import json

from hrs_codex.errors import EvidenceMismatch
from hrs_codex.hashing import digest, file_digest


class EvidenceFiles:
    """Per-pass receipts index: cache work, never trust a previous validation pass."""

    def __init__(self, directory):
        self.directory = directory
        self.hashes, self.metadata = {}, {}
        self.index = None

    def sha(self, path):
        if path not in self.hashes:
            self.hashes[path] = file_digest(path)
        return self.hashes[path]

    def json(self, path):
        if path not in self.metadata:
            self.metadata[path] = json.loads(path.read_text(encoding="utf-8"))
        return self.metadata[path]

    def matching(self, sha):
        if self.index is None:
            self.index = {}
            for path in (self.directory / "evidence/details").glob("*.json"):
                self.index.setdefault(self.sha(path), []).append(path)
        return self.index.get(sha, [])


class Evidence:
    def __init__(self, repository):
        self.repository = repository

    def verify(self, doc: dict) -> None:
        files = EvidenceFiles(self.repository.directory(doc["id"]))
        tasks = {t["task_id"]: t for t in doc["tasks"]}
        if (
            files.sha(self.repository.evidence_path(doc, doc["source"]["path"]))
            != doc["source"]["sha256"]
        ):
            raise EvidenceMismatch("Retained original source hash mismatch")
        for page in doc["pages"]:
            if files.sha(self.repository.evidence_path(doc, page["image"])) != page["image_sha256"]:
                raise EvidenceMismatch(f"Original page {page['page']} image hash mismatch")
            for field in ("ocr", "candidate", "approved"):
                text = page.get(field)
                if text and digest(text["markdown"]) != text["sha256"]:
                    raise EvidenceMismatch(f"Page {page['page']} {field} text hash mismatch")
                if text:
                    task = tasks[text["task_id"]]
                    retained = (
                        task["inputs"]["markdown"]
                        if field == "approved" and task["stage"] != "human_review"
                        else task["result"]["markdown"]
                    )
                    if text["markdown"] != retained:
                        raise EvidenceMismatch(
                            f"Page {page['page']} {field} differs from its retained machine result"
                        )
        for task in doc["tasks"]:
            readings = task.get("source_reading_history", []) + (
                [task["source_reading"]] if task.get("source_reading") else []
            )
            for reading in readings:
                if digest({k: v for k, v in reading.items() if k != "sha256"}) != reading["sha256"]:
                    raise EvidenceMismatch("Original reading evidence hash mismatch")
                if (
                    reading["reading"]["image_sha256"]
                    != doc["pages"][task["page"] - 1]["image_sha256"]
                ):
                    raise EvidenceMismatch("Original reading page hash mismatch")
                self.verify_details(doc, task, reading["reading"], files)
            if task.get("source_first_required") and task.get("result") is not None:
                if (
                    not task.get("source_reading")
                    or task["receipt"].get("source_reading_sha256")
                    != task["source_reading"]["sha256"]
                ):
                    raise EvidenceMismatch("Machine decision lacks its original-first reading")
            if task.get("inputs") and digest(task["inputs"]) != task["claim"]["input_sha256"]:
                raise EvidenceMismatch("Retained task input hash mismatch")
            if (
                task.get("result") is not None
                and digest(task["result"]) != task["receipt"]["result_sha256"]
            ):
                raise EvidenceMismatch("Retained task result hash mismatch")
            if task.get("result") is not None:
                self.verify_details(doc, task, task["result"], files)
        organization = doc.get("organization")
        if organization:
            task = next(t for t in doc["tasks"] if t["task_id"] == organization["task_id"])
            if organization["articles"] != task["result"]["articles"]:
                raise EvidenceMismatch("Organization differs from its retained machine result")

    def verify_details(self, doc: dict, task: dict, result: dict, files=None) -> None:
        files = files or EvidenceFiles(self.repository.directory(doc["id"]))
        evidence = result.get("original_details", [])
        if not isinstance(evidence, list):
            raise EvidenceMismatch("Detail evidence must be a list")
        for detail in evidence:
            if isinstance(detail, dict) and set(detail) == {"image_sha256", "metadata_sha256"}:
                matches = files.matching(detail["metadata_sha256"])
                if len(matches) != 1:
                    raise EvidenceMismatch(
                        "Unknown compact detail receipt; use hashes returned by original_detail"
                    )
                detail = {
                    **files.json(matches[0]),
                    **detail,
                    "metadata": matches[0]
                    .relative_to(self.repository.directory(doc["id"]))
                    .as_posix(),
                }
            if (
                not isinstance(detail, dict)
                or detail.get("document_id") != doc["id"]
                or detail.get("source_sha256") != doc["source"]["sha256"]
                or detail.get("page") not in [p["page"] for p in doc["pages"]]
                or (task["page"] is not None and detail.get("page") != task["page"])
            ):
                raise EvidenceMismatch("Detail evidence source/page mismatch")
            for field in ("image", "metadata"):
                path = detail.get(field)
                if not isinstance(path, str) or files.sha(
                    self.repository.evidence_path(doc, path)
                ) != detail.get(field + "_sha256"):
                    raise EvidenceMismatch("Detail evidence file hash mismatch")
            retained = files.json(self.repository.evidence_path(doc, detail["metadata"]))
            if {
                k: v for k, v in detail.items() if k not in {"metadata", "metadata_sha256"}
            } != retained:
                raise EvidenceMismatch("Detail evidence metadata mismatch")
