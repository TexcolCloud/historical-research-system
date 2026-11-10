"""SQLite authority for evidence-bound native research, independent of old card jobs.

New material cards go from authoring directly to independent source-first review,
with one directed repair when needed. Old policies retain their enrichment stage.
Immutable inputs, submissions and card revisions survive retries and revisions.
This module schedules and validates; it never invokes a model or invents approval.
"""

import json
import time
import uuid
from contextlib import nullcontext
from copy import deepcopy

from hrs.processing.contracts import SOL
from hrs.processing.errors import EvidenceMismatch, LeaseConflict, WorkflowStopped
from hrs.processing.hashing import digest
from hrs.processing.ledger import encode
from hrs.processing.runtime_control import FAILURE_CODES
from hrs.research import policy as research_policy
from hrs.research.card_validation import (
    require_checked_ids,
    validate_card_evidence,
    validate_identity_sources,
)
from hrs.research.catalogue import ResearchCatalogue
from hrs.research.complete_contracts import (
    AssessmentMismatch,
    complete_checks,
    validate_assessments,
    validate_complete_cards,
    validate_inventory,
)
from hrs.research.diagnostics import failure_details, provider_failure, redact_error
from hrs.research.identities import IdentityIndex, card_entities
from hrs.research.mechanics import decoded_evidence_receipts
from hrs.research.models import (
    CardOnlyDraftSubmission,
    CardOnlyIncrementalSubmission,
    CompleteEnrichedDraftSubmission,
    DossierDraftSubmission,
    DossierIncrementalSubmission,
    EntityMention,
    EventDraft,
    EvidenceSpan,
    IncrementalDraftSubmission,
    NativeDeltaSubmission,
    ResearchQuestion,
    card_model,
    is_card_only,
    is_dossier,
    submission_model,
)
from hrs.research.packets import build_task_packet, source_index
from hrs.research.policy import ENRICH_STAGES, STAGES, VERIFY_STAGES
from hrs.research.supervision import task_limits, task_timeout


class ResearchWorkflow:
    def __init__(self, store):
        self.store = store
        self.ledger = store.checkpoints.ledger
        if self.ledger is None:
            raise ValueError("Research requires an explicitly migrated writable ledger")
        self.catalogue = ResearchCatalogue(self.ledger)

    @staticmethod
    def _retrieval_stage(task, run):
        return task["stage"] in ENRICH_STAGES or (
            is_card_only(run["policy"]) and task["stage"] == "author"
        )

    @staticmethod
    def _exists(connection):
        return bool(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_runs'"
            ).fetchone()
        )

    def _schema(self, connection):
        self.store.checkpoints.require_writable()
        for sql in (
            "CREATE TABLE IF NOT EXISTS research_runs (id TEXT PRIMARY KEY, book_id TEXT NOT NULL, "
            "input_hash TEXT NOT NULL, revision_key TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL, "
            "UNIQUE(book_id,input_hash,revision_key))",
            "CREATE TABLE IF NOT EXISTS research_tasks (id TEXT PRIMARY KEY, run_id TEXT NOT NULL "
            "REFERENCES research_runs(id), article_id TEXT NOT NULL, stage TEXT NOT NULL, state TEXT NOT NULL, "
            "token TEXT UNIQUE, worker TEXT, expires REAL NOT NULL DEFAULT 0, model TEXT NOT NULL, "
            "input_hash TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL, "
            "UNIQUE(run_id,article_id,stage))",
            "CREATE INDEX IF NOT EXISTS research_ready ON research_tasks(run_id,state,stage)",
            "CREATE TABLE IF NOT EXISTS research_reads (token TEXT PRIMARY KEY, payload TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS research_workers (run_id TEXT NOT NULL, worker_id TEXT NOT NULL, "
            "native_agent_id TEXT NOT NULL, execution_id TEXT NOT NULL, PRIMARY KEY(run_id,worker_id))",
            "CREATE TABLE IF NOT EXISTS research_sources (id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS research_submissions (token TEXT PRIMARY KEY, task_id TEXT NOT NULL, "
            "worker TEXT NOT NULL, result_hash TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL)",
            "CREATE TABLE IF NOT EXISTS research_cards (id TEXT NOT NULL, version INTEGER NOT NULL, "
            "run_id TEXT NOT NULL, book_id TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL, "
            "PRIMARY KEY(id,version))",
            "CREATE TABLE IF NOT EXISTS research_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "run_id TEXT NOT NULL, event TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL)",
            "CREATE TABLE IF NOT EXISTS research_revision_requests (card_id TEXT NOT NULL, "
            "base_version INTEGER NOT NULL, request_hash TEXT NOT NULL,run_id TEXT NOT NULL,payload TEXT NOT NULL,"
            "PRIMARY KEY(card_id,base_version))",
            "CREATE INDEX IF NOT EXISTS research_card_lineage ON research_cards(json_extract(payload,'$.lineage_id'))",
        ):
            connection.execute(sql)
        IdentityIndex.ensure(connection)
        ResearchCatalogue.ensure(connection)

    @staticmethod
    def _event(connection, run_id, event, payload):
        return connection.execute(
            "INSERT INTO research_events(run_id,event,payload,created_at) VALUES (?,?,?,?)",
            (run_id, event, encode(payload), time.time()),
        ).lastrowid

    @staticmethod
    def _run(connection, run_id):
        if not ResearchWorkflow._exists(connection):
            raise FileNotFoundError(run_id)
        row = connection.execute(
            "SELECT payload FROM research_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not row:
            raise FileNotFoundError(run_id)
        return json.loads(row[0])

    @staticmethod
    def _tasks(connection, run_id):
        return [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT payload FROM research_tasks WHERE run_id=? ORDER BY article_id, "
                "CASE stage WHEN 'author' THEN 0 WHEN 'enrich' THEN 1 ELSE 2 END",
                (run_id,),
            )
        ]

    @staticmethod
    def _save_task(connection, task):
        connection.execute(
            "UPDATE research_tasks SET state=?,token=?,worker=?,expires=?,attempts=?,payload=? WHERE id=?",
            (
                task["state"],
                task.get("task_token"),
                task.get("worker_id"),
                task.get("expires_at", 0),
                task["attempts"],
                encode(task),
                task["task_id"],
            ),
        )

    @staticmethod
    def _save_run(connection, run):
        if "snapshot" in run:
            ResearchWorkflow._freeze_snapshot(connection, run, run.pop("snapshot"))
        run["version"] += 1
        run["updated_at"] = time.time()
        connection.execute(
            "UPDATE research_runs SET state=?,payload=? WHERE id=?",
            (run["state"], encode(run), run["run_id"]),
        )

    @staticmethod
    def _freeze_source(connection, source, kind):
        reference = digest(source)
        connection.execute(
            "INSERT OR IGNORE INTO research_sources VALUES (?,?,?)",
            (reference, kind, encode(source)),
        )
        return reference

    @staticmethod
    def _freeze_snapshot(connection, run, snapshot):
        run["snapshot_ref"] = ResearchWorkflow._freeze_source(connection, snapshot, "publication")
        run["article_index"] = [
            {
                "id": article["id"],
                "title": article.get("title", ""),
                "text_sha256": article["text_sha256"],
                "text_length": len(article["text"]),
                "source_ref": ResearchWorkflow._freeze_source(connection, article, "article"),
            }
            for article in snapshot["articles"]
        ]

    @staticmethod
    def _load_source(connection, reference):
        row = connection.execute(
            "SELECT payload FROM research_sources WHERE id=?", (reference,)
        ).fetchone()
        if row is None:
            raise EvidenceMismatch("Frozen research source is missing")
        source = json.loads(row[0])
        if digest(source) != reference:
            raise EvidenceMismatch("Frozen research source hash changed")
        return source

    def _new_task(
        self,
        connection,
        run,
        article_id,
        stage,
        input_hash,
        draft=None,
        external_sources=None,
        research_disposition=None,
        research_audit_ref=None,
        mechanical_evidence_ref=None,
    ):
        task = {
            "task_id": uuid.uuid4().hex,
            "run_id": run["run_id"],
            "article_id": article_id,
            "stage": stage,
            "state": "ready",
            "input_hash": input_hash,
            "attempts": 0,
            "technical_failures": 0,
            "model": run["policy"]["stages"][stage]["model"],
            "complete_research": bool(run["policy"].get("complete_research")),
            "card_only": is_card_only(run["policy"]),
            "dossier_cards": is_dossier(run["policy"]),
            "created_at": time.time(),
        }
        if draft is not None:
            task["cards_ref"] = self._freeze_source(connection, {"cards": draft}, "draft")
        task["external_sources"] = external_sources or []
        if research_disposition is not None:
            task["research_disposition"] = research_disposition
        if research_audit_ref is not None:
            task["research_audit_ref"] = research_audit_ref
        if mechanical_evidence_ref is not None:
            task["mechanical_evidence_ref"] = mechanical_evidence_ref
        connection.execute(
            "INSERT INTO research_tasks(id,run_id,article_id,stage,state,model,input_hash,payload) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                task["task_id"],
                run["run_id"],
                article_id,
                stage,
                "ready",
                task["model"],
                input_hash,
                encode(task),
            ),
        )
        return task

    def _task_cards(self, task):
        if "cards" in task:
            return task["cards"]
        if "cards_ref" not in task:
            return []
        with self.ledger.connect() as connection:
            return self._load_source(connection, task["cards_ref"])["cards"]

    @staticmethod
    def _snapshot(snapshot):
        value = deepcopy(snapshot)
        value["book_id"] = str(value["book_id"])
        value["source_run_id"] = str(value.get("source_run_id") or value.get("run_id") or "")
        value["title"] = value.get("title") or value.get("book_title") or ""
        if (
            value.get("content_verified") is not True
            or not value.get("validation_scope")
            or value["validation_scope"] in {"workflow_test", "workflow_only"}
        ):
            raise EvidenceMismatch(
                "Research requires a content-verified publication, not workflow approval"
            )
        for key in ("document_id", "publication_sha256", "source_run_id"):
            if not isinstance(value.get(key), str) or not value[key]:
                raise ValueError(f"Publication snapshot requires {key}")
        if len(value["publication_sha256"]) != 64 or any(
            character not in "0123456789abcdef" for character in value["publication_sha256"]
        ):
            raise ValueError("Publication SHA-256 is required")
        articles = value.get("articles")
        if not articles or len({str(article["id"]) for article in articles}) != len(articles):
            raise ValueError("Snapshot requires articles with unique IDs")
        for article in articles:
            article["id"] = str(article["id"])
            text = article.get("text")
            if not isinstance(text, str) or not text:
                raise ValueError("Research article text cannot be empty")
            if article.get("text_sha256") != digest(text):
                raise EvidenceMismatch("Article text does not match its frozen hash")
            parts = article.get("parts") or []
            cursor = 0
            for index, part in enumerate(parts):
                part.setdefault("id", f"part-{index}")
                start, end = part.get("start"), part.get("end")
                if (
                    type(start) is not int
                    or type(end) is not int
                    or start != cursor
                    or not start < end <= len(text)
                ):
                    raise EvidenceMismatch(
                        "Article parts must cover the complete text in source order"
                    )
                if part.get("text", text[start:end]) != text[start:end]:
                    raise EvidenceMismatch("Source part text does not match article text")
                part_hash = digest(text[start:end])
                if part.get("text_sha256", part_hash) != part_hash:
                    raise EvidenceMismatch("Source part hash does not match article text")
                part["text_sha256"] = part_hash
                source = part.get("source", {})
                pages = source.get("pages") or ([source["page"]] if source.get("page") else [])
                if not pages or any(type(page) is not int or page < 1 for page in pages):
                    raise EvidenceMismatch("Every article part requires physical source pages")
                source["pages"] = pages
                part["source"] = source
                cursor = end
            if cursor != len(text):
                raise EvidenceMismatch("Article source parts do not cover its complete text")
        return value

    def enqueue(
        self,
        source_snapshot,
        *,
        revision_of=None,
        reason=None,
        purpose=None,
        _policy=None,
        _connection=None,
    ):
        snapshot = self._snapshot(source_snapshot)
        policy = deepcopy(_policy or research_policy.POLICY)
        purpose = purpose or (snapshot.get("research_scope") or {}).get("kind", "book")
        input_hash = digest({"snapshot": snapshot, "policy": policy, "purpose": purpose})
        if revision_of and not (isinstance(reason, str) and reason.strip()):
            raise ValueError("A research revision must preserve its reason")
        revision_key = str(revision_of or "")
        with (
            nullcontext(_connection) if _connection is not None else self.ledger.connect(write=True)
        ) as connection:
            self._schema(connection)
            existing = connection.execute(
                "SELECT id FROM research_runs WHERE book_id=? AND input_hash=? AND revision_key=?",
                (snapshot["book_id"], input_hash, revision_key),
            ).fetchone()
            if existing:
                return self._status(connection, existing[0])
            if revision_of and self._run(connection, revision_of)["book_id"] != snapshot["book_id"]:
                raise ValueError("Revision must belong to the same book")
            now = time.time()
            run = {
                "run_id": uuid.uuid4().hex,
                "book_id": snapshot["book_id"],
                "title": snapshot["title"],
                "source_run_id": snapshot["source_run_id"],
                "document_id": snapshot["document_id"],
                "publication_sha256": snapshot["publication_sha256"],
                "input_hash": input_hash,
                "policy": policy,
                "purpose": purpose,
                "research_scope": deepcopy(snapshot.get("research_scope") or {}),
                "state": "ready",
                "version": 1,
                "revision_of": revision_of,
                "reason": reason,
                "created_at": now,
                "updated_at": now,
            }
            run.update(
                {
                    key: snapshot.get(key)
                    for key in (
                        "workspace_id",
                        "chapters_sha256",
                        "validation_scope",
                        "content_verified",
                    )
                }
            )
            self._freeze_snapshot(connection, run, snapshot)
            connection.execute(
                "INSERT INTO research_runs VALUES (?,?,?,?,?,?)",
                (run["run_id"], run["book_id"], input_hash, revision_key, "ready", encode(run)),
            )
            for article in snapshot["articles"]:
                self._new_task(
                    connection,
                    run,
                    article["id"],
                    "author",
                    digest({"publication": input_hash, "article": article["text_sha256"]}),
                )
            self._event(connection, run["run_id"], "enqueued", {"input_hash": input_hash})
            return self._status(connection, run["run_id"])

    def snapshot(self, run_id):
        with self.ledger.connect() as connection:
            run = self._run(connection, run_id)
            return run.get("snapshot") or self._load_source(connection, run["snapshot_ref"])

    def sources(self, run_id):
        """Frozen external article texts for server projections and reader links."""
        with self.ledger.connect() as connection:
            self._run(connection, run_id)
            sources = {
                source["id"]: source
                for task in self._tasks(connection, run_id)
                for source in task.get("external_sources", [])
            }
            return [
                self._load_source(connection, source["source_ref"]) for source in sources.values()
            ]

    def _article(self, run, article_id):
        if "snapshot" in run:
            return next(
                article for article in run["snapshot"]["articles"] if article["id"] == article_id
            )
        reference = next(
            article["source_ref"] for article in run["article_index"] if article["id"] == article_id
        )
        with self.ledger.connect() as connection:
            return self._load_source(connection, reference)

    def _expire(self, connection):
        now = time.time()
        changed_runs = set()
        for (payload,) in connection.execute(
            "SELECT payload FROM research_tasks WHERE state='leased' AND expires<=?", (now,)
        ).fetchall():
            task = json.loads(payload)
            self.store.runtime_control.release("research:" + task["task_token"])
            self._event(
                connection, task["run_id"], "lease_expired", {"task_token": task["task_token"]}
            )
            run = self._run(connection, task["run_id"])
            failures = task.get("technical_failures", 0) + 1
            task.update(
                state="failed" if failures >= run["policy"]["max_technical_attempts"] else "ready",
                technical_failures=failures,
                last_failure={"code": "timeout", "at": now},
                expires_at=0,
                task_token=None,
                worker_id=None,
            )
            self._save_task(connection, task)
            changed_runs.add(task["run_id"])
        for run_id in changed_runs:
            self._refresh(connection, self._run(connection, run_id))

    def claim(self, run_id, worker_id, lease_seconds=900, *, stage=None, paged=False):
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("A native worker identity is required")
        if type(lease_seconds) is not int or not 30 <= lease_seconds <= 86400:
            raise ValueError("Research lease must be between 30 and 86400 seconds")
        if stage is not None and stage not in STAGES:
            raise ValueError("Unsupported research stage")
        with self.ledger.connect(write=True) as connection:
            self._schema(connection)
            run = self._run(connection, run_id)
            if paged is None:
                paged = run["policy"].get("version", 1) >= 2
            if (
                run["state"]
                in {
                    "paused",
                    "completed",
                    "pending",
                    "failed",
                    "cancelled",
                    "technical_blocked",
                    "superseded",
                }
                or run.get("next_retry_at", 0) > time.time()
            ):
                return None
            self._expire(connection)
            run = self._run(connection, run_id)
            if run["state"] in {"failed", "completed", "pending", "superseded"}:
                return None
            tasks = self._tasks(connection, run_id)
            for task in tasks:
                if task["state"] != "ready" or (stage and task["stage"] != stage):
                    continue
                predecessors = [item for item in tasks if item["article_id"] == task["article_id"]]
                if task["stage"] in VERIFY_STAGES and worker_id in {
                    item.get("worker_id") for item in predecessors
                }:
                    continue
                if (
                    task["stage"] in VERIFY_STAGES
                    and connection.execute(
                        "SELECT 1 FROM research_submissions s JOIN research_tasks t ON t.id=s.task_id "
                        "WHERE s.worker=? AND t.stage IN ('author','enrich','repair') LIMIT 1",
                        (worker_id,),
                    ).fetchone()
                ):
                    continue
                token = uuid.uuid4().hex
                admission = self.store.runtime_control.admit(
                    task["model"],
                    "research:" + token,
                    occupied=self.ledger.model_capacity_used(task["model"]),
                    total_occupied=self.ledger.capacity_used(),
                    lease_seconds=lease_seconds,
                    priority="research",
                )
                if not admission["allowed"]:
                    run["last_admission_deferred"] = {
                        "at": time.time(),
                        "reason": admission["reason"],
                        "retry_at": admission["retry_at"],
                    }
                    self._save_run(connection, run)
                    return None
                task.update(
                    state="leased",
                    task_token=token,
                    worker_id=worker_id,
                    expires_at=time.time() + lease_seconds,
                    lease_seconds=lease_seconds,
                    attempts=task["attempts"] + 1,
                    last_progress_at=time.time(),
                    claimed_at=time.time(),
                    write_contract=submission_model(run["policy"], task["stage"]).__name__,
                )
                self._save_task(connection, task)
                connection.execute(
                    "INSERT INTO research_reads VALUES (?,?)",
                    (token, encode({"ranges": {}, "candidate_opened": False})),
                )
                run["state"] = "running"
                self._save_run(connection, run)
                self._event(
                    connection,
                    run_id,
                    "claimed",
                    {key: task[key] for key in ("task_id", "stage", "task_token", "worker_id")},
                )
                article = self._article(run, task["article_id"])
                packet = build_task_packet(
                    task,
                    run["policy"],
                    article,
                    self._required_sources(task, run),
                    snapshot=self.snapshot(run_id)
                    if run["policy"].get("complete_research")
                    else {},
                    cards=self._task_cards(task) if task["stage"] in ENRICH_STAGES else None,
                )
                if paged:
                    for source in packet["source_index"]:
                        source.pop("parts", None)
                    body = encode(packet)
                    task["packet_ref"] = self._freeze_source(
                        connection, {"packet": packet}, "packet"
                    )
                    self._save_task(connection, task)
                    reads = self._reads(connection, token)
                    reads["packet_paged"] = True
                    connection.execute(
                        "UPDATE research_reads SET payload=? WHERE token=?", (encode(reads), token)
                    )
                    return {
                        "task_token": token,
                        "run_id": run_id,
                        "stage": task["stage"],
                        "worker_id": worker_id,
                        "input_hash": task["input_hash"],
                        "packet": {
                            "format": "canonical_json",
                            "total": len(body),
                            "sha256": digest(body),
                            "read_tool": "research_packet_text",
                            "window_limit": 1600,
                        },
                        "instructions": "Read every packet JSON window before other research tools. "
                        "The packet contains complete instructions, schema, source IDs and enrichment drafts.",
                    }
                return packet
            return None

    def _lease(self, connection, token, worker_id=None):
        if not self._exists(connection):
            raise LeaseConflict("Unknown research lease")
        row = connection.execute(
            "SELECT payload FROM research_tasks WHERE token=?", (token,)
        ).fetchone()
        if not row:
            raise LeaseConflict("Unknown or superseded research lease")
        task = json.loads(row[0])
        if task["state"] != "leased" or task["expires_at"] <= time.time():
            raise LeaseConflict("Research lease expired or was already submitted")
        if worker_id is not None and task["worker_id"] != worker_id:
            raise LeaseConflict("Research lease belongs to another native worker")
        run = self._run(connection, task["run_id"])
        if run["state"] in {"paused", "cancelled", "superseded"}:
            raise WorkflowStopped("Research run is paused or cancelled")
        return task, run

    def start_execution(self, run_id, execution_id):
        """Host-only native execution attestation; no MCP tool grants this authority."""
        with self.ledger.connect(write=True) as connection:
            self._schema(connection)
            run = self._run(connection, run_id)
            if run.get("native_execution_id") and run["native_execution_id"] != execution_id:
                self.release_run_leases(run_id, reason="superseded_native_execution")
                run = self._run(connection, run_id)
            run["native_execution_id"] = execution_id
            self._save_run(connection, run)

    def bind_native_worker(self, run_id, worker_id, native_agent_id, execution_id):
        with self.ledger.connect(write=True) as connection:
            run = self._run(connection, run_id)
            if run.get("native_execution_id") != execution_id:
                raise LeaseConflict("Native worker belongs to a superseded execution")
            row = connection.execute(
                "SELECT native_agent_id FROM research_workers WHERE run_id=? AND worker_id=?",
                (run_id, worker_id),
            ).fetchone()
            if row and row[0] != native_agent_id:
                raise LeaseConflict(
                    "A worker identity cannot be reassigned to another native agent"
                )
            connection.execute(
                "INSERT OR IGNORE INTO research_workers VALUES (?,?,?,?)",
                (run_id, worker_id, native_agent_id, execution_id),
            )

    @staticmethod
    def _require_native_worker(connection, task, run):
        if not run.get("native_execution_id"):
            return
        row = connection.execute(
            "SELECT native_agent_id,execution_id FROM research_workers WHERE run_id=? AND worker_id=?",
            (run["run_id"], task["worker_id"]),
        ).fetchone()
        if not row or row[1] != run["native_execution_id"]:
            raise LeaseConflict(
                "Await the supervisor's native-agent identity attestation before submitting"
            )
        if (
            task["stage"] in VERIFY_STAGES
            and connection.execute(
                "SELECT 1 FROM research_workers w JOIN research_tasks t ON t.run_id=w.run_id "
                "JOIN research_submissions s ON s.task_id=t.id AND s.worker=w.worker_id "
                "WHERE w.native_agent_id=? AND (t.stage IN ('author','enrich','repair') OR "
                "(?='reverify' AND t.stage='verify' AND t.run_id=? AND t.article_id=?)) LIMIT 1",
                (row[0], task["stage"], task["run_id"], task["article_id"]),
            ).fetchone()
        ):
            raise EvidenceMismatch(
                "The same native agent cannot verify its own authored or enriched material under another worker ID"
            )

    def _renew(self, connection, task, *, worker_activity=False):
        expires = time.time() + task["lease_seconds"]
        if not self.store.runtime_control.renew(
            task["model"], "research:" + task["task_token"], expires_at=expires
        ):
            raise LeaseConflict("Research model admission expired")
        task["expires_at"] = expires
        if worker_activity:
            task["last_progress_at"] = time.time()
        self._save_task(connection, task)

    @staticmethod
    def _ranges_complete(ranges, total):
        cursor = 0
        for start, end in sorted(ranges):
            if start > cursor:
                break
            cursor = max(cursor, end)
        return cursor == total

    def _packet_body(self, connection, task):
        return encode(self._load_source(connection, task["packet_ref"])["packet"])

    def _require_packet_read(self, connection, task):
        reads = self._reads(connection, task["task_token"])
        if reads.get("packet_paged") and not self._ranges_complete(
            reads.get("packet_ranges", []), len(self._packet_body(connection, task))
        ):
            raise EvidenceMismatch(
                "Read every complete research packet JSON window before proceeding"
            )
        return reads

    def packet_text(self, task_token, worker_id, offset=0, limit=1600):
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 2000
        ):
            raise ValueError(
                "Packet JSON windows require a nonnegative offset and 1–2000 Unicode characters"
            )
        with self.ledger.connect(write=True) as connection:
            task, _ = self._lease(connection, task_token, worker_id)
            if not task.get("packet_ref"):
                raise ValueError("This legacy claim uses its complete direct packet")
            body = self._packet_body(connection, task)
            if offset >= len(body):
                raise ValueError("Packet JSON offset is outside the frozen packet")
            end = min(offset + limit, len(body))
            reads = self._reads(connection, task_token)
            reads.setdefault("packet_ranges", []).append([offset, end])
            connection.execute(
                "UPDATE research_reads SET payload=? WHERE token=?", (encode(reads), task_token)
            )
            self._renew(connection, task, worker_activity=True)
            return {
                "task_token": task_token,
                "start": offset,
                "end": end,
                "total": len(body),
                "packet_sha256": digest(body),
                "text": body[offset:end],
            }

    @staticmethod
    def _supervision_task(connection, task):
        if "claimed_at" not in task:
            # Frozen v5 leases predate this field; the retained claim event supplies
            # their original start without changing a frozen content policy.
            row = connection.execute(
                "SELECT created_at FROM research_events WHERE run_id=? AND event='claimed' "
                "AND json_extract(payload,'$.task_token')=? ORDER BY id DESC LIMIT 1",
                (task["run_id"], task.get("task_token")),
            ).fetchone()
            task = {**task, "claimed_at": row[0] if row else task["created_at"]}
        return task

    @staticmethod
    def _attested_active(connection, run, task, active_workers):
        if run["policy"].get("version", 1) < 5 or not run["policy"].get("complete_research"):
            return task.get("worker_id") in active_workers
        return bool(
            task.get("worker_id") in active_workers
            and run.get("native_execution_id")
            and connection.execute(
                "SELECT 1 FROM research_workers WHERE run_id=? AND worker_id=? AND execution_id=?",
                (run["run_id"], task["worker_id"], run["native_execution_id"]),
            ).fetchone()
        )

    @staticmethod
    def _source_characters(run, task):
        primary = next(
            (item for item in run.get("article_index", []) if item["id"] == task["article_id"]), {}
        )
        length = primary.get("text_length", 0)
        if not length and "snapshot" in run:
            length = sum(
                len(item["text"])
                for item in run["snapshot"]["articles"]
                if item["id"] == task["article_id"]
            )
        return length + sum(item.get("text_length", 0) for item in task.get("external_sources", []))

    def started_native_workers(self, run_id, worker_ids, *, execution_id):
        """Successful reads prove startup when a parent wait has not emitted a fresh child status."""
        candidates = sorted(set(worker_ids))
        if not candidates:
            return set()
        with self.ledger.connect() as connection:
            run = self._run(connection, run_id)
            if run.get("native_execution_id") != execution_id or run["state"] not in {
                "ready",
                "running",
            }:
                return set()
            marks = ",".join("?" for _ in candidates)
            rows = connection.execute(
                "SELECT w.worker_id,reads.payload FROM research_tasks t "
                "JOIN research_workers w ON w.run_id=t.run_id AND w.worker_id=json_extract(t.payload,'$.worker_id') "
                "JOIN research_reads reads ON reads.token=json_extract(t.payload,'$.task_token') "
                "WHERE t.run_id=? AND t.state='leased' AND w.execution_id=? "
                f"AND w.worker_id IN ({marks}) AND json_extract(t.payload,'$.expires_at')>?",
                [run_id, execution_id, *candidates, time.time()],
            ).fetchall()
            started = set()
            for worker_id, payload in rows:
                reads = json.loads(payload)
                if reads.get("packet_ranges") or any(reads.get("ranges", {}).values()):
                    started.add(worker_id)
            return started

    def supervision_timeouts(self, run_id, *, active_workers=()):
        """Read-only host supervision; observed native identity is necessary, not an unlimited heartbeat."""
        with self.ledger.connect() as connection:
            run = self._run(connection, run_id)
            if run["state"] in {"paused", "cancelled", "superseded"}:
                return []
            return [
                {
                    "task_id": task["task_id"],
                    "lease_fingerprint": digest(task["task_token"]),
                    "reason": reason,
                }
                for task in self._tasks(connection, run_id)
                if task["state"] == "leased"
                and (
                    reason := task_timeout(
                        run["policy"],
                        self._supervision_task(connection, task),
                        now=time.time(),
                        active=self._attested_active(connection, run, task, active_workers),
                        source_characters=self._source_characters(run, task),
                    )
                )
            ]

    def renew_run_leases(self, run_id, *, active_workers=(), max_idle_seconds=None):
        with self.ledger.connect(write=True) as connection:
            run = self._run(connection, run_id)
            if run["state"] in {"paused", "cancelled", "superseded"}:
                return 0
            renewed = 0
            for task in self._tasks(connection, run_id):
                if (
                    task["state"] == "leased"
                    and task["expires_at"] > time.time()
                    and self._attested_active(connection, run, task, active_workers)
                    and task_timeout(
                        run["policy"],
                        self._supervision_task(connection, task),
                        now=time.time(),
                        active=True,
                        source_characters=self._source_characters(run, task),
                        max_idle_seconds=max_idle_seconds,
                    )
                    is None
                ):
                    self._renew(connection, task)
                    renewed += 1
            return renewed

    def read_source(self, task_token, offset=0, limit=16000, *, article_id=None):
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 32000
        ):
            raise ValueError("Invalid research source window")
        with self.ledger.connect(write=True) as connection:
            task, run = self._lease(connection, task_token)
            self._require_packet_read(connection, task)
            requested = str(article_id) if article_id is not None else task["article_id"]
            article = self._source(task, run, requested)
            if article is None:
                raise EvidenceMismatch("Source article is outside this task")
            if offset >= len(article["text"]):
                raise ValueError("Source offset is outside article")
            end = min(offset + limit, len(article["text"]))
            reads = self._reads(connection, task_token)
            reads["ranges"].setdefault(article["id"], []).append([offset, end])
            connection.execute(
                "UPDATE research_reads SET payload=? WHERE token=?", (encode(reads), task_token)
            )
            self._renew(connection, task, worker_activity=True)
            return {
                "article_id": article["id"],
                "text": article["text"][offset:end],
                "start": offset,
                "end": end,
                "total": len(article["text"]),
                "text_sha256": article["text_sha256"],
                "publication_sha256": article.get("publication_sha256", run["publication_sha256"]),
                "parts": [
                    {key: value for key, value in part.items() if key != "text"}
                    for part in article["parts"]
                    if part["start"] < end and part["end"] > offset
                ],
                "footnotes": [
                    note
                    for note in article.get("footnotes", [])
                    if any(
                        location.get("start", end) < end and location.get("end", offset) > offset
                        for location in [note.get("note", {}), *note.get("references", [])]
                    )
                ],
            }

    def locate_quotes(self, task_token, worker_id, quotes, article_id=None, offset=0, limit=20):
        """Exact source coordinates only after the worker has actually read this frozen source."""
        from hrs.research.quote_locator import locate_quotes

        with self.ledger.connect(write=True) as connection:
            task, run = self._lease(connection, task_token, worker_id)
            self._require_native_worker(connection, task, run)
            reads = self._require_packet_read(connection, task)
            requested = str(article_id) if article_id is not None else task["article_id"]
            source = self._source(task, run, requested)
            if source is None:
                raise EvidenceMismatch("Quote source is outside this task's frozen sources")
            if not self._ranges_complete(reads["ranges"].get(requested, []), len(source["text"])):
                raise EvidenceMismatch(
                    "Read the complete frozen source before locating its quotations"
                )
            result = locate_quotes(source, quotes, offset=offset, limit=limit)
            self._renew(connection, task, worker_activity=True)
            return result

    def source_reference(self, task_token, article_id=None, page=None):
        """Resolve only the frozen physical sources authorized by this live task."""
        with self.ledger.connect() as connection:
            task, run = self._lease(connection, task_token)
            requested = str(article_id) if article_id is not None else task["article_id"]
            article = self._source(task, run, requested)
            if article is None:
                raise EvidenceMismatch("Original is outside this task's frozen sources")
            parts = article["parts"]
            pages = sorted({number for part in parts for number in part["source"]["pages"]})
            selected = pages[0] if page is None else page
            if type(selected) is not int or selected not in pages:
                raise EvidenceMismatch("Original page is outside this task's frozen source ranges")
            sources = [
                deepcopy(part["source"]) for part in parts if selected in part["source"]["pages"]
            ]
            hashes = {source["image_sha256"] for source in sources if source.get("image_sha256")}
            if len(hashes) > 1:
                raise EvidenceMismatch("Frozen original page has conflicting image hashes")
            return {
                "document_id": article.get("document_id", run["document_id"]),
                "page": selected,
                "article_id": requested,
                "image_sha256": next(iter(hashes), None),
                "sources": sources,
                "publication_sha256": article.get("publication_sha256", run["publication_sha256"]),
            }

    @staticmethod
    def _reads(connection, token):
        row = connection.execute(
            "SELECT payload FROM research_reads WHERE token=?", (token,)
        ).fetchone()
        return json.loads(row[0])

    def _sources(self, task, run):
        return [
            self._source(task, run, article_id)
            for article_id in [
                task["article_id"],
                *(item["id"] for item in task.get("external_sources", [])),
            ]
        ]

    def _source(self, task, run, article_id):
        if article_id == task["article_id"]:
            return self._article(run, article_id)
        reference = next(
            (item for item in task.get("external_sources", []) if item["id"] == article_id), None
        )
        if reference is None:
            return None
        with self.ledger.connect() as connection:
            return self._load_source(connection, reference["source_ref"])

    def _required_sources(self, task, run, cards=None):
        required = {task["article_id"]}
        if task["stage"] in VERIFY_STAGES | {"repair"} or cards is not None:
            required.update(
                evidence["article_id"]
                for card in (cards if cards is not None else self._task_cards(task))
                for evidence in card["evidence"]
            )
            targets = {
                relation["target_card_id"]
                for card in (cards if cards is not None else self._task_cards(task))
                for relation in card.get("cross_relations", [])
            }
            for reference in task.get("target_cards", {}).values():
                if reference["card_id"] in targets:
                    required.update(reference["source_ids"])
        return [
            self._source(task, run, article_id)
            for article_id in [task["article_id"], *sorted(required - {task["article_id"]})]
        ]

    def add_source(self, task_token, source_snapshot):
        """Server-only: freeze verified library sources returned by evidence search."""
        snapshot = self._snapshot(source_snapshot)
        with self.ledger.connect(write=True) as connection:
            task, run = self._lease(connection, task_token)
            if not self._retrieval_stage(task, run):
                raise ValueError(
                    "Only enrichment can add independently retrievable library sources"
                )
            allowed_books = (run.get("research_scope") or {}).get("book_ids") or []
            if allowed_books and snapshot["book_id"] not in allowed_books:
                raise EvidenceMismatch(
                    "External research source is outside the frozen selected-book scope"
                )
            indexes = []
            for article in snapshot["articles"]:
                external = deepcopy(article)
                external["original_article_id"] = external["id"]
                external.update(
                    id="source-"
                    + digest([snapshot["book_id"], snapshot["publication_sha256"], article["id"]])[
                        :32
                    ],
                    book_id=snapshot["book_id"],
                    publication_sha256=snapshot["publication_sha256"],
                    source_run_id=snapshot["source_run_id"],
                    document_id=snapshot["document_id"],
                )
                family_id, existing = external["id"], None
                for item in task["external_sources"]:
                    if item["id"] != family_id and not item["id"].startswith(family_id + "-"):
                        continue
                    previous = self._load_source(connection, item["source_ref"])
                    previous = {**previous, "id": family_id}
                    if {k: v for k, v in previous.items() if k != "footnotes"} != {
                        k: v for k, v in external.items() if k != "footnotes"
                    }:
                        raise EvidenceMismatch("Previously frozen external source changed")
                    if item["id"] == family_id and previous == external:
                        existing = item
                if existing is None:
                    # New identities are descriptor-bound across tasks/order.
                    # Only an identical retained legacy binding keeps its bare
                    # ID; neither its source/hash nor old citations are changed.
                    external["id"] += "-" + digest(external)
                    existing = next(
                        (item for item in task["external_sources"] if item["id"] == external["id"]),
                        None,
                    )
                reference = self._freeze_source(connection, external, "external_article")
                if existing is not None and existing["source_ref"] != reference:
                    raise EvidenceMismatch("Previously frozen external source changed")
                if existing is None:
                    task["external_sources"].append(
                        {
                            "id": external["id"],
                            "source_ref": reference,
                            "text_sha256": external["text_sha256"],
                        }
                    )
                indexes.append(source_index(external))
            self._save_task(connection, task)
            self._event(
                connection,
                run["run_id"],
                "source_added",
                {
                    "task_id": task["task_id"],
                    "source_hash": digest(snapshot),
                    "article_ids": [item["id"] for item in indexes],
                },
            )
            return indexes

    def target_card(self, task_token, worker_id, card_id, version=None, *, authorize=None):
        """Freeze a versioned counterpart; verification only reopens the retained descriptor."""
        with self.ledger.connect(write=True) as connection:
            task, run = self._lease(connection, task_token, worker_id)
            self._require_native_worker(connection, task, run)
            self._require_packet_read(connection, task)
            existing = next(
                (
                    item
                    for item in task.get("target_cards", {}).values()
                    if item["card_id"] == card_id
                    and (version is None or item["version"] == version)
                ),
                None,
            )
            if existing:
                return deepcopy(existing)
            if not self._retrieval_stage(task, run):
                raise EvidenceMismatch(
                    "Verification may read only already frozen counterpart cards"
                )
            self._require_read(connection, task, run)
            target = self.card(card_id, version=version)
            target_run = self._run(connection, target["run_id"])
            if (
                target["state"] not in {"verified", "human_accepted"}
                or target_run["state"] == "cancelled"
            ):
                raise EvidenceMismatch(
                    "Counterpart requires a retained verified current source card"
                )
            allowed = (run.get("research_scope") or {}).get("book_ids") or []
            if allowed and target["book_id"] not in allowed:
                raise EvidenceMismatch("Counterpart is outside the frozen selected-book scope")
            if authorize is not None:
                authorize(target)
        target_snapshot = self.snapshot(target["run_id"])
        primary = next(
            item for item in target_snapshot["articles"] if item["id"] == target["article_id"]
        )
        frozen = {**target_snapshot, "articles": [primary]}
        indexes = self.add_source(task_token, frozen)
        descriptor = {
            key: deepcopy(value)
            for key, value in target.items()
            if key not in {"revision_versions", "human_decision"}
        }
        descriptor["frozen_source_index"] = indexes
        with self.ledger.connect(write=True) as connection:
            task, run = self._lease(connection, task_token, worker_id)
            reference = self._freeze_source(connection, descriptor, "target_card")
            body = encode(descriptor)
            manifest = {
                "card_ref": reference,
                "card_id": target["id"],
                "version": target["version"],
                "publication_sha256": target["publication_sha256"],
                "source_ids": [i["id"] for i in indexes],
                "source_index": indexes,
                "total": len(body),
                "sha256": digest(body),
                "read_tool": "research_target_card_text",
            }
            task.setdefault("target_cards", {})[reference] = manifest
            self._save_task(connection, task)
            return deepcopy(manifest)

    def target_card_text(self, task_token, worker_id, card_ref, offset=0, limit=1600):
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 2000
        ):
            raise ValueError("Target card windows require offset>=0 and limit 1–2000")
        with self.ledger.connect(write=True) as connection:
            task, run = self._lease(connection, task_token, worker_id)
            self._require_native_worker(connection, task, run)
            self._require_packet_read(connection, task)
            if card_ref not in task.get("target_cards", {}):
                raise EvidenceMismatch("Counterpart is outside this frozen task")
            reads = self._reads(connection, task_token)
            if task["stage"] in VERIFY_STAGES:
                self._require_read(connection, task, run)
                if not reads["candidate_opened"]:
                    raise EvidenceMismatch(
                        "Freeze independent source observations before counterpart candidates"
                    )
            body = encode(self._load_source(connection, card_ref))
            if offset >= len(body):
                raise ValueError("Counterpart offset is outside the frozen card")
            end = min(offset + limit, len(body))
            reads.setdefault("target_ranges", {}).setdefault(card_ref, []).append([offset, end])
            connection.execute(
                "UPDATE research_reads SET payload=? WHERE token=?", (encode(reads), task_token)
            )
            self._renew(connection, task, worker_activity=True)
            return {
                "card_ref": card_ref,
                "start": offset,
                "end": end,
                "total": len(body),
                "sha256": digest(body),
                "text": body[offset:end],
            }

    def record_plan(self, task_token, worker_id, questions):
        """Retain a bounded adaptive research plan before executing its next queries."""
        plan = [
            ResearchQuestion.model_validate(question).model_dump(mode="json")
            for question in questions
        ]
        if not plan or len(plan) > 12 or len({question["id"] for question in plan}) != len(plan):
            raise ValueError("Research planning requires 1–12 uniquely identified subquestions")
        with self.ledger.connect(write=True) as connection:
            task, run = self._lease(connection, task_token, worker_id)
            self._require_native_worker(connection, task, run)
            self._require_read(connection, task, run)
            if task["stage"] not in ENRICH_STAGES:
                raise ValueError("Only enrichment or directed repair can update research plans")
            receipts = set(self._plan_receipt_ids(task))
            if any(not set(question["search_receipt_ids"]) <= receipts for question in plan):
                raise EvidenceMismatch("Research plan references a search that did not execute")
            previous = {question["id"] for question in task.get("research_plan", [])}
            if not previous <= {question["id"] for question in plan}:
                raise EvidenceMismatch("A later research plan cannot erase prior subquestions")
            reference = self._freeze_source(connection, {"questions": plan}, "research_plan")
            task["research_plan"] = plan
            task.setdefault("research_plan_refs", []).append(reference)
            self._event(
                connection,
                run["run_id"],
                "research_plan",
                {"task_id": task["task_id"], "plan_ref": reference},
            )
            self._renew(connection, task, worker_activity=True)
            return {
                "plan_ref": reference,
                "version": len(task["research_plan_refs"]),
                "question_ids": [question["id"] for question in plan],
                "remaining_queries": max(
                    0,
                    run["policy"].get("max_research_queries", 8) - task.get("search_successes", 0),
                ),
            }

    def prepare_search(self, task_token, query=None):
        """Authorize enrichment before issuing any real retrieval request."""
        with self.ledger.connect(write=True) as connection:
            task, run = self._lease(connection, task_token)
            self._require_packet_read(connection, task)
            if not self._retrieval_stage(task, run):
                raise ValueError(
                    "Only enrichment can execute searches; verification uses frozen server receipts"
                )
            if run["policy"].get("complete_research"):
                if not is_card_only(run["policy"]) and not task.get("research_plan"):
                    raise EvidenceMismatch(
                        "Persist the source-bound research plan before searching"
                    )
                if task.get("search_successes", 0) >= run["policy"].get("max_research_queries", 8):
                    raise EvidenceMismatch(
                        "Research query budget reached; evaluate retained evidence and stop"
                    )
                if (
                    not is_card_only(run["policy"])
                    and query is not None
                    and query
                    not in {
                        value
                        for question in task["research_plan"]
                        for value in question["proposed_queries"]
                    }
                ):
                    raise EvidenceMismatch(
                        "Record the adapted query in the research plan before executing it"
                    )
            self._renew(connection, task, worker_activity=True)
            return {
                "book_ids": list((run.get("research_scope") or {}).get("book_ids") or []),
                "scope": deepcopy(run.get("research_scope") or {}),
            }

    @staticmethod
    def _audit_metrics(value):
        if isinstance(value, dict):
            excluded = {
                "chain_of_thought",
                "reasoning",
                "analysis",
                "thoughts",
                "authorization",
                "api_key",
                "password",
                "secret",
            }
            return {
                key: ResearchWorkflow._audit_metrics(item)
                for key, item in value.items()
                if key.casefold() not in excluded
            }
        if isinstance(value, list):
            return [ResearchWorkflow._audit_metrics(item) for item in value]
        return value if type(value) in {str, int, float, bool, type(None)} else str(value)

    def record_search(
        self, task_token, result, *, book_id=None, book_ids=None, limit=None, preview_summary=None
    ):
        """Server-only audit of actual retrieval inputs, source manifests and metrics."""
        if (
            not isinstance(result, dict)
            or result.get("backend") != "hybrid"
            or not isinstance(result.get("query"), str)
            or not result["query"].strip()
            or type(result.get("available")) is not bool
        ):
            raise ValueError("Research audit requires the actual hybrid retrieval response")
        manifest = [
            {
                key: deepcopy(hit[key])
                for key in (
                    "id",
                    "book_id",
                    "chapter_id",
                    "run_id",
                    "start",
                    "end",
                    "pages",
                    "publication_sha256",
                    "text_sha256",
                    "score",
                )
                if key in hit
            }
            | {
                "snippet_sha256": digest(hit.get("text", "")),
                "reading_target": {
                    key: value
                    for key, value in (hit.get("reading_target") or {}).items()
                    if key != "anchor"
                },
            }
            for hit in result.get("hits", [])[:20]
        ]
        payload = {
            "query": result["query"],
            "backend": "hybrid",
            "available": result["available"],
            "book_id": book_id,
            "book_ids": list(book_ids or []),
            "limit": limit,
            "hits_manifest": manifest,
            "metrics": self._audit_metrics(result.get("metrics", {})),
            "returned_hits": len(result.get("hits", [])),
            "manifest_omitted": max(0, len(result.get("hits", [])) - len(manifest)),
            "preview": deepcopy(preview_summary),
            "evidence_status": result.get("evidence_status"),
            "error": failure_details(result.get("error")),
        }
        with self.ledger.connect(write=True) as connection:
            task, run = self._lease(connection, task_token)
            if not self._retrieval_stage(task, run):
                raise ValueError("Only enrichment records external evidence research")
            allowed = set((run.get("research_scope") or {}).get("book_ids") or [])
            actual = set(book_ids or ([book_id] if book_id else []))
            if allowed and (not actual or not actual <= allowed):
                raise EvidenceMismatch(
                    "Actual research search exceeded its frozen selected-book scope"
                )
            event_id = self._event(
                connection,
                run["run_id"],
                "research_search",
                {"task_id": task["task_id"], **payload},
            )
            task.setdefault("search_receipt_ids", []).append(event_id)
            task["search_attempts"] = task.get("search_attempts", 0) + 1
            task["search_successes"] = task.get("search_successes", 0) + int(result["available"])
            self._renew(connection, task, worker_activity=True)
            return {
                "recorded": True,
                "receipt_id": f"search-{event_id}",
                "backend": "hybrid",
                "available": result["available"],
            }

    def _freeze_research_audit(self, connection, task):
        receipts = []
        if task.get("inherited_audit_ref"):
            receipts.extend(self._load_source(connection, task["inherited_audit_ref"])["receipts"])
        for event_id in task.get("search_receipt_ids", []):
            row = connection.execute(
                "SELECT payload,created_at FROM research_events WHERE id=? AND run_id=? AND event='research_search'",
                (event_id, task["run_id"]),
            ).fetchone()
            if row is None:
                raise EvidenceMismatch("Server search receipt is missing")
            payload = json.loads(row[0])
            if payload.get("task_id") != task["task_id"]:
                raise EvidenceMismatch("Search receipt belongs to another enrichment task")
            receipts.append({"receipt_id": f"search-{event_id}", "created_at": row[1], **payload})
        return self._freeze_source(
            connection,
            {
                "enrich_task_id": task["task_id"],
                "receipts": receipts,
                "scope": "Executed queries and returned-hit manifests only; limits or empty results never prove corpus-wide absence.",
            },
            "research_audit",
        )

    def _plan_receipt_ids(self, task):
        ids = [f"search-{identity}" for identity in task.get("search_receipt_ids", [])]
        if task.get("inherited_audit_ref"):
            with self.ledger.connect() as connection:
                ids.extend(
                    item["receipt_id"]
                    for item in self._load_source(connection, task["inherited_audit_ref"])[
                        "receipts"
                    ]
                )
        return ids

    def release_run_leases(self, run_id, *, reason):
        """An executor interruption returns work without inventing a source or provider failure."""
        with self.ledger.connect(write=True) as connection:
            run = self._run(connection, run_id)
            for task in self._tasks(connection, run_id):
                if task["state"] == "leased":
                    self.store.runtime_control.release("research:" + task["task_token"])
                    task.update(state="ready", task_token=None, worker_id=None, expires_at=0)
                    self._save_task(connection, task)
            self._event(connection, run_id, "executor_interrupted", {"reason": reason})
            self._refresh(connection, run)

    def report_worker_failure(
        self, run_id, worker_id, failure, *, task_id=None, lease_fingerprint=None
    ):
        with self.ledger.connect(write=True) as connection:
            task = next(
                (
                    task
                    for task in self._tasks(connection, run_id)
                    if task["state"] == "leased"
                    and task.get("worker_id") == worker_id
                    and (task_id is None or task["task_id"] == task_id)
                    and (
                        lease_fingerprint is None or digest(task["task_token"]) == lease_fingerprint
                    )
                ),
                None,
            )
            if task is not None:
                run = self._run(connection, run_id)
                if run["state"] not in {"paused", "cancelled", "superseded"}:
                    return self._record_task_failure(
                        connection, task, run, failure["code"], failure_details(failure)
                    )

    def _require_read(self, connection, task, run, cards=None):
        reads = self._require_packet_read(connection, task)
        for article in self._required_sources(task, run, cards):
            if not self._ranges_complete(
                reads["ranges"].get(article["id"], []), len(article["text"])
            ):
                raise EvidenceMismatch(
                    "Read every complete original source before submitting or opening candidates"
                )
        return reads

    def candidate(self, task_token, worker_id, observations, *, paged=False):
        with self.ledger.connect(write=True) as connection:
            task, run = self._lease(connection, task_token, worker_id)
            if paged is None:
                paged = run["policy"].get("version", 1) >= 2
            if task["stage"] not in VERIFY_STAGES:
                raise ValueError("Only independent verification has a source-first candidate gate")
            self._require_native_worker(connection, task, run)
            reads = self._require_read(connection, task, run)
            article = self._article(run, task["article_id"])
            if (
                not isinstance(observations, dict)
                or observations.get("article_id") != article["id"]
                or observations.get("text_sha256") != article["text_sha256"]
                or not isinstance(observations.get("summary"), str)
                or not observations["summary"].strip()
            ):
                raise EvidenceMismatch(
                    "Independent observations require article ID, source hash and reading summary"
                )
            if reads["candidate_opened"] and reads["observations"] != observations:
                raise EvidenceMismatch("Pre-candidate source observations are immutable")
            if run["policy"].get("complete_research"):
                inventory = validate_inventory(
                    observations, article, card_only=is_card_only(run["policy"])
                )
                task["inventory_ref"] = self._freeze_source(
                    connection, inventory, "independent_inventory"
                )
                self._save_task(connection, task)
            reads.update(candidate_opened=True, observations=deepcopy(observations))
            reads["candidate_paged"] = reads.get("candidate_paged", False) or paged
            connection.execute(
                "UPDATE research_reads SET payload=? WHERE token=?", (encode(reads), task_token)
            )
            self._renew(connection, task, worker_activity=True)
            document = self._candidate_document(task)
            cards = document["cards"]
            if paged:
                body = self._candidate_body(task)
                return {
                    "task_token": task_token,
                    "input_hash": task["input_hash"],
                    "card_count": len(cards),
                    "candidate": {
                        "format": "canonical_json",
                        "total": len(body),
                        "sha256": digest(body),
                        "read_tool": "research_candidate_text",
                        "window_limit": 1600,
                    },
                    "instructions": "Read every candidate JSON window through research_candidate_text before submitting. "
                    "The JSON contains all cards and the complete required_checks; no coverage fields are omitted.",
                }
            return {
                "task_token": task_token,
                "input_hash": task["input_hash"],
                **document,
            }

    def _candidate_document(self, task):
        cards = self._task_cards(task)
        required = self._verification_checks(
            cards,
            complete=task.get("complete_research", False),
            card_only=task.get("card_only", False),
            dossier=task.get("dossier_cards", False),
        )
        if task.get("research_disposition"):
            required["checked_research_disposition"] = True
        document = {
            "cards": cards,
            "required_checks": required,
            "research_disposition": task.get("research_disposition"),
        }
        if task.get("inventory_ref"):
            with self.ledger.connect() as connection:
                inventory = self._load_source(connection, task["inventory_ref"])
            document["independent_inventory"] = inventory
            required["checked_inventory_ids"] = [item["id"] for item in inventory["findings"]]
        document["target_cards"] = list(task.get("target_cards", {}).values())
        if task.get("research_audit_ref"):
            with self.ledger.connect() as connection:
                audit = self._load_source(connection, task["research_audit_ref"])
            document["research_audit"] = audit
            required["checked_search_receipt_ids"] = [
                receipt["receipt_id"] for receipt in audit["receipts"]
            ]
        if task.get("mechanical_evidence_ref"):
            with self.ledger.connect() as connection:
                mechanical = self._load_source(connection, task["mechanical_evidence_ref"])
            document["mechanical_evidence"] = mechanical
            required["checked_mechanical_evidence_ids"] = [
                receipt["receipt_id"] for receipt in mechanical["receipts"]
            ]
        return document

    def _candidate_body(self, task):
        return encode(self._candidate_document(task))

    def candidate_text(self, task_token, worker_id, offset=0, limit=1600):
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 2000
        ):
            raise ValueError(
                "Candidate JSON windows require a nonnegative offset and 1–2000 Unicode characters"
            )
        with self.ledger.connect(write=True) as connection:
            task, run = self._lease(connection, task_token, worker_id)
            self._require_native_worker(connection, task, run)
            reads = self._require_read(connection, task, run)
            if task["stage"] not in VERIFY_STAGES or not reads["candidate_opened"]:
                raise EvidenceMismatch(
                    "Record independent source observations before reading candidate JSON"
                )
            body = self._candidate_body(task)
            if offset >= len(body):
                raise ValueError("Candidate JSON offset is outside the frozen candidate")
            end = min(offset + limit, len(body))
            reads["candidate_paged"] = True
            reads.setdefault("candidate_ranges", []).append([offset, end])
            connection.execute(
                "UPDATE research_reads SET payload=? WHERE token=?", (encode(reads), task_token)
            )
            self._renew(connection, task, worker_activity=True)
            return {
                "task_token": task_token,
                "start": offset,
                "end": end,
                "total": len(body),
                "candidate_sha256": digest(body),
                "text": body[offset:end],
            }

    @staticmethod
    def _verification_checks(cards, *, complete=False, card_only=False, dossier=False):
        checks = {"checked_card_ids": [card["id"] for card in cards]}
        for field, collection in (
            ("checked_item_ids", "items"),
            ("checked_evidence_ids", "evidence"),
            ("checked_relation_ids", "relations"),
        ):
            checks[field] = [
                f"{card['id']}/{item['id']}" for card in cards for item in card[collection]
            ]
        checks["checked_identity_links"] = [
            f"{card['id']}/{index}"
            for card in cards
            for index, _ in enumerate(card["identity_links"])
        ]
        if complete:
            checks.update(complete_checks(cards, card_only=card_only, dossier=dossier))
        return checks

    def _validate_cards(self, cards, task, run):
        ids = [card["id"] for card in cards]
        if len(ids) != len(set(ids)):
            raise EvidenceMismatch("Draft card IDs must be unique within the article")
        if task["stage"] in ENRICH_STAGES and set(ids) != {
            card["id"] for card in self._task_cards(task)
        }:
            raise EvidenceMismatch("Enrichment must retain every authored card identity")
        sources = {
            article["id"]: article
            for article in self._required_sources(task, run, cards)
            if article is not None
        }
        if run["policy"].get("complete_research"):
            validate_complete_cards(
                cards,
                self._article(run, task["article_id"]),
                stage=task["stage"],
                planned_questions=task.get("research_plan"),
                questions=self.snapshot(run["run_id"]).get("research_questions", []),
                receipt_ids=self._plan_receipt_ids(task),
                card_only=is_card_only(run["policy"]),
                dossier=is_dossier(run["policy"]),
            )
        validate_card_evidence(cards, task["article_id"], sources)
        validate_identity_sources(cards, task, run, sources, lambda identity: self.entity(identity))

    def _require_targets_read(self, connection, task, cards, reads, *, verify_ranges=True):
        for card_index, card in enumerate(cards):
            evidence = {item["id"]: item for item in card["evidence"]}
            for relation_index, relation in enumerate(card.get("cross_relations", [])):
                reference = next(
                    (
                        item
                        for item in task.get("target_cards", {}).values()
                        if item["card_id"] == relation["target_card_id"]
                        and item["version"] == relation["target_card_version"]
                    ),
                    None,
                )
                if (
                    reference is None
                    or reference["publication_sha256"] != relation["target_publication_sha256"]
                ):
                    raise EvidenceMismatch(
                        "Cross-card relation requires the exact frozen counterpart version"
                    )
                descriptor = self._load_source(connection, reference["card_ref"])
                if verify_ranges and not self._ranges_complete(
                    reads.get("target_ranges", {}).get(reference["card_ref"], []),
                    len(encode(descriptor)),
                ):
                    raise EvidenceMismatch(
                        "Read every complete frozen counterpart card window before submitting"
                    )
                for field, source_ids, message in (
                    (
                        "source_evidence_ids",
                        [task["article_id"]],
                        "Cross-card relation must cite this document's own source",
                    ),
                    (
                        "target_evidence_ids",
                        reference["source_ids"],
                        "Cross-card relation must cite the frozen target document source",
                    ),
                ):
                    matched, bad_count, groups = 0, 0, {}
                    # Inspect every reference, retaining at most four distinct
                    # bad IDs and eight positions each, even for huge repeats.
                    for index, eid in enumerate(relation[field]):
                        actual = evidence[eid]["article_id"]
                        if actual in source_ids:
                            matched += 1
                            continue
                        bad_count += 1
                        key = (eid, actual)
                        if key not in groups:
                            if len(groups) == 4:
                                continue
                            groups[key] = {"indices": [], "occurrences": 0}
                        group = groups[key]
                        group["occurrences"] += 1
                        if len(group["indices"]) < 8:
                            group["indices"].append(index)
                    valid = bool(matched) if field == "source_evidence_ids" else bad_count == 0
                    if valid:
                        continue
                    if not groups:
                        raise EvidenceMismatch(message)
                    errors = []
                    expected = [identity[:128] for identity in source_ids[:3]]
                    omitted = max(0, len(source_ids) - 3)
                    expected_label = f"{expected}" + (
                        f" (+{omitted} further IDs)" if omitted else ""
                    )
                    omitted_groups = bad_count - sum(g["occurrences"] for g in groups.values())
                    omitted_refs = bad_count - sum(len(g["indices"]) for g in groups.values())
                    for (eid, actual), group in groups.items():
                        index = group["indices"][0]
                        omitted_indices = group["occurrences"] - len(group["indices"])
                        context = {
                            "card_id": card["id"][:128],
                            "relation_id": relation["id"][:128],
                            "target_card_id": relation["target_card_id"][:128],
                            "target_card_version": relation["target_card_version"],
                            "evidence_id": eid[:128],
                            "actual_source_id": actual[:128],
                            "expected_source_ids": expected,
                            "omitted_expected_sources": omitted,
                            "bad_reference_count": bad_count,
                            "occurrences": group["occurrences"],
                            "reference_indices": group["indices"],
                            "omitted_indices": omitted_indices,
                            "omitted_group_references": omitted_groups,
                            "omitted_reference_indices": omitted_refs,
                        }
                        errors.append(
                            {
                                "loc": (
                                    "cards",
                                    card_index,
                                    "cross_relations",
                                    relation_index,
                                    field,
                                    index,
                                ),
                                "type": f"cross_card_{field.removesuffix('_ids')}_source",
                                "msg": f"{bad_count} invalid references; {group['occurrences']} occurrences; "
                                f"{omitted_indices} omitted indices; "
                                f"{omitted_groups} further invalid references omitted. "
                                f"cards[{card_index}].cross_relations[{relation_index}].{field}[{index}]: "
                                f"actual source {context['actual_source_id']}; "
                                f"expected frozen source IDs {expected_label}. "
                                f"{message}: relation {context['card_id']}/{context['relation_id']} "
                                f"target {context['target_card_id']}@{context['target_card_version']}; "
                                f"{field}[{index}]={context['evidence_id']} resolves in "
                                "this output card.evidence. "
                                f"Retained indices for this ID: {group['indices']}. "
                                "Counterpart evidence IDs are a separate namespace.",
                                "ctx": context,
                            }
                        )
                    raise AssessmentMismatch(errors)

    def _materialize_submission(self, task, run, result):
        contract = submission_model(
            run["policy"],
            task["stage"],
            frozen_name=task.get("write_contract")
            or ("VerificationSubmission" if task["stage"] in VERIFY_STAGES else "DraftSubmission"),
        )
        delta = None
        if contract in {
            IncrementalDraftSubmission,
            CardOnlyIncrementalSubmission,
            DossierIncrementalSubmission,
        }:
            complete = result
            if result.get("mode") == "delta":
                from hrs.research.card_updates import apply_card_changes

                delta = NativeDeltaSubmission.model_validate(result).model_dump(mode="json")
                complete = {
                    "input_hash": delta["input_hash"],
                    "cards": apply_card_changes(
                        self._task_cards(task),
                        delta["base_sha256"],
                        delta["changes"],
                        dossier=is_dossier(run["policy"]),
                    ),
                    "research_disposition": delta.get("research_disposition")
                    if "research_disposition" in result
                    else deepcopy(task.get("research_disposition")),
                }
            # Full and delta converge before the one complete semantic write model.
            contract = (
                DossierDraftSubmission
                if is_dossier(run["policy"])
                else CardOnlyDraftSubmission
                if is_card_only(run["policy"])
                else CompleteEnrichedDraftSubmission
            )
            result = complete
        return contract.model_validate(result).model_dump(mode="json"), delta

    def _record_materialization(self, connection, task, run, raw_result, delta, cards):
        native = connection.execute(
            "SELECT native_agent_id FROM research_workers WHERE run_id=? AND worker_id=? AND execution_id=?",
            (run["run_id"], task["worker_id"], run.get("native_execution_id")),
        ).fetchone()
        audit = {
            "mode": "delta",
            "base_sha256": delta["base_sha256"],
            "baseline_ref": task["cards_ref"],
            "raw_delta_ref": self._freeze_source(
                connection, {"submission": deepcopy(raw_result)}, "native_card_delta"
            ),
            "canonical_ref": self._freeze_source(connection, {"cards": cards}, "draft"),
            "canonical_sha256": digest(cards),
            "worker_id": task["worker_id"],
            "native_agent_id": native[0] if native else None,
            "native_execution_id": run.get("native_execution_id"),
        }
        task.setdefault("materializations", []).append(audit)
        self._event(
            connection, run["run_id"], "delta_materialized", {"task_id": task["task_id"], **audit}
        )

    @staticmethod
    def _proposal_source_ids(value):
        if isinstance(value, dict):
            if isinstance(value.get("article_id"), str):
                yield value["article_id"]
            if str(value.get("path", "")).endswith("/article_id") and isinstance(
                value.get("value"), str
            ):
                yield value["value"]
            for nested in value.values():
                yield from ResearchWorkflow._proposal_source_ids(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from ResearchWorkflow._proposal_source_ids(nested)

    def record_submission_rejection(self, task_token, worker_id, result, error):
        """Retain an unapproved, changed native proposal after its write transaction rolled back."""
        if not isinstance(result, dict):
            raise ValueError("A rejected proposal must be the actual structured submission")
        with self.ledger.connect(write=True) as connection:
            task, run = self._lease(connection, task_token, worker_id)
            if not run.get("native_execution_id"):
                raise LeaseConflict(
                    "Rejected proposal activity requires native identity attestation"
                )
            self._require_native_worker(connection, task, run)
            verification = (
                task["stage"] in VERIFY_STAGES
                and run["policy"].get("complete_research")
                and run["policy"].get("version", 1) >= 5
            )
            if task["stage"] not in {"author", "enrich", "repair"} and not verification:
                raise ValueError(
                    "Only author/enrich/repair proposals may record rejected write activity"
                )
            if result.get("input_hash") != task["input_hash"]:
                raise EvidenceMismatch(
                    "Rejected proposal input hash does not match the current task"
                )
            reads = self._require_read(connection, task, run)
            if verification and (
                not reads["candidate_opened"]
                or reads.get("candidate_paged")
                and not self._ranges_complete(
                    reads.get("candidate_ranges", []), len(self._candidate_body(task))
                )
            ):
                raise EvidenceMismatch(
                    "Read every complete candidate before recording rejected verification"
                )
            # The rejected object may be structurally incomplete. Only inspect its
            # declared source identities here; never manufacture a valid candidate.
            for identity in set(self._proposal_source_ids(result)):
                source = self._source(task, run, identity)
                if source is None:
                    raise EvidenceMismatch(
                        "Rejected proposal cites a source outside the current task"
                    )
                if not self._ranges_complete(
                    reads["ranges"].get(identity, []), len(source["text"])
                ):
                    raise EvidenceMismatch(
                        "Read every complete proposal source before recording rejected activity"
                    )
            timed = self._supervision_task(connection, task)
            absolute = task_limits(run["policy"], timed, self._source_characters(run, task))[
                "absolute_seconds"
            ]
            if absolute is not None and time.time() - timed["claimed_at"] > absolute:
                raise WorkflowStopped("Rejected proposal cannot extend its absolute task deadline")
            proposal_hash = digest(result)
            previous = next(
                (
                    item
                    for item in task.get("rejected_proposals", [])
                    if item["proposal_sha256"] == proposal_hash
                ),
                None,
            )
            if previous:
                return {
                    "recorded": False,
                    "activity_renewed": False,
                    "proposal_sha256": proposal_hash,
                    "rejection_ref": previous["rejection_ref"],
                }
            retained_error = failure_details(error) or {}
            if isinstance(error, dict) and isinstance(error.get("issues"), list):
                retained_error["issues"] = [
                    {
                        "path": [
                            redact_error(part) if isinstance(part, str) else part
                            for part in item.get("path", [])
                        ],
                        "type": redact_error(item.get("type", "validation_error")),
                        "message": redact_error(item.get("message", "")),
                    }
                    for item in error["issues"]
                    if isinstance(item, dict)
                ]
            reference = self._freeze_source(
                connection,
                {
                    "proposal": deepcopy(result),
                    "proposal_sha256": proposal_hash,
                    "error": retained_error,
                    "accepted": False,
                    "approval_type": None,
                    "task_id": task["task_id"],
                    "worker_id": worker_id,
                    "native_execution_id": run["native_execution_id"],
                },
                "rejected_native_proposal",
            )
            task.setdefault("rejected_proposals", []).append(
                {
                    "proposal_sha256": proposal_hash,
                    "rejection_ref": reference,
                    "at": time.time(),
                }
            )
            self._event(
                connection,
                run["run_id"],
                "submission_rejected",
                {
                    "task_id": task["task_id"],
                    "proposal_sha256": proposal_hash,
                    "rejection_ref": reference,
                    "accepted": False,
                },
            )
            self._renew(connection, task, worker_activity=True)
            return {
                "recorded": True,
                "activity_renewed": True,
                "proposal_sha256": proposal_hash,
                "rejection_ref": reference,
            }

    def submit(self, task_token, worker_id, result):
        result_hash = digest(result)
        with self.ledger.connect(write=True) as connection:
            prior_run = self._submitted_run_id(connection, task_token, worker_id, result_hash)
            if prior_run is not None:
                return self._status(connection, prior_run)
            task, run = self._lease(connection, task_token, worker_id)
            self._require_native_worker(connection, task, run)
            if result.get("input_hash") != task["input_hash"]:
                raise EvidenceMismatch(
                    "Research input hash changed or does not match the claimed task"
                )
            reads = self._require_read(connection, task, run)
            validated, delta = self._materialize_submission(task, run, result)
            if task["stage"] in VERIFY_STAGES:
                self._validate_verification(connection, task, run, reads, validated)
                self._complete_verification(connection, task, run, validated)
            else:
                self._complete_authoring(connection, task, run, reads, validated, delta, result)
            return self._commit_submission(connection, task, run, validated, result_hash)

    def _submitted_run_id(self, connection, task_token, worker_id, result_hash):
        duplicate = connection.execute(
            "SELECT worker,result_hash,task_id FROM research_submissions WHERE token=?",
            (task_token,),
        ).fetchone()
        if duplicate is None:
            return None
        if duplicate[0] != worker_id or duplicate[1] != result_hash:
            raise LeaseConflict("A committed submission cannot be replaced")
        return connection.execute(
            "SELECT run_id FROM research_tasks WHERE id=?", (duplicate[2],)
        ).fetchone()[0]

    def _validate_verification(self, connection, task, run, reads, validated):
        if not reads["candidate_opened"]:
            raise EvidenceMismatch(
                "Read source and record independent observations before inspecting candidates"
            )
        if reads.get("candidate_paged"):
            if not self._ranges_complete(
                reads.get("candidate_ranges", []), len(self._candidate_body(task))
            ):
                raise EvidenceMismatch(
                    "Read every complete candidate JSON window before submitting verification"
                )
        for field, expected in self._verification_checks(
            self._task_cards(task),
            complete=task.get("complete_research", False),
            card_only=is_card_only(run["policy"]),
            dossier=is_dossier(run["policy"]),
        ).items():
            require_checked_ids(
                validated[field],
                set(expected),
                "Independent verification must cover every candidate card, item, citation and relationship: "
                + field,
            )
        if run["policy"].get("complete_research"):
            document = self._candidate_document(task)
            inventory = document["independent_inventory"]
            expected = [item["id"] for item in inventory["findings"]]
            require_checked_ids(
                validated["checked_inventory_ids"],
                expected,
                "Independent verification must check every source inventory finding",
            )
            validate_assessments(
                validated["coverage_assessments"],
                inventory,
                self._task_cards(task),
                decision=validated["decision"],
                card_only=is_card_only(run["policy"]),
            )
            self._require_targets_read(connection, task, self._task_cards(task), reads)
        self._require_verification_receipts(task, validated)

    def _require_verification_receipts(self, task, validated):
        if task.get("research_disposition") and not validated["checked_research_disposition"]:
            raise EvidenceMismatch(
                "Independent verification must check the source-supported no-search disposition"
            )
        if task.get("research_audit_ref"):
            expected = self._candidate_document(task)["required_checks"][
                "checked_search_receipt_ids"
            ]
            require_checked_ids(
                validated["checked_search_receipt_ids"],
                expected,
                "Independent verification must check every server search receipt ID",
            )
        if task.get("mechanical_evidence_ref"):
            document = self._candidate_document(task)
            expected = document["required_checks"]["checked_mechanical_evidence_ids"]
            require_checked_ids(
                validated["checked_mechanical_evidence_ids"],
                expected,
                "Independent verification must check every decoded mechanical evidence receipt",
            )
            if any(
                not receipt["quote_exact"] or not receipt["source_hash_verified"]
                for receipt in document["mechanical_evidence"]["receipts"]
            ):
                raise EvidenceMismatch("Decoded evidence does not exactly match its frozen source")

    def _complete_verification(self, connection, task, run, validated):
        repair = (
            validated["decision"] == "pending"
            and task["stage"] == "verify"
            and run["policy"].get("complete_research")
            and run["policy"].get("max_content_repairs", 0) > 0
        )
        task["state"] = (
            "completed"
            if repair
            else ("pending" if validated["decision"] == "pending" else "completed")
        )
        if repair:
            self._queue_repair(connection, task, run, validated)
        else:
            self._publish_cards(connection, run, task, validated)

    def _queue_repair(self, connection, task, run, validated):
        next_task = self._new_task(
            connection,
            run,
            task["article_id"],
            "repair",
            digest({"prior_verification": validated, "candidate": task.get("cards_ref")}),
            self._task_cards(task),
            task.get("external_sources", []),
            research_disposition=task.get("research_disposition")
            if run["policy"].get("incremental_updates")
            else None,
        )
        next_task.update(
            prior_verification=deepcopy(validated),
            target_cards=deepcopy(task.get("target_cards", {})),
            inherited_audit_ref=task.get("research_audit_ref"),
        )
        self._save_task(connection, next_task)
        self._event(
            connection,
            run["run_id"],
            "directed_repair",
            {"task_id": task["task_id"], "repair_task_id": next_task["task_id"]},
        )

    def _require_enrichment_evidence(self, task, run, validated):
        if (
            task["stage"] in ENRICH_STAGES
            and run["policy"].get("enrichment_search_required")
            and not task.get("search_successes")
            and not (task["stage"] == "repair" and self._plan_receipt_ids(task))
        ):
            disposition = validated.get("research_disposition")
            cards = validated["cards"]
            basis = {
                f"{card['id']}/{item['id']}"
                for card in cards
                for item in card["evidence"]
                if item["article_id"] == task["article_id"]
            }
            if (
                not disposition
                or not set(disposition["evidence_ids"]) <= basis
                or any(
                    card["mentions"] or card["events"] or card["identity_links"] for card in cards
                )
            ):
                raise EvidenceMismatch(
                    "Enrichment requires actual hybrid retrieval or a source-supported no-external-claims disposition"
                )

    def _complete_authoring(self, connection, task, run, reads, validated, delta, result):
        self._require_enrichment_evidence(task, run, validated)
        self._validate_cards(validated["cards"], task, run)
        self._require_read(connection, task, run, validated["cards"])
        self._require_targets_read(connection, task, validated["cards"], reads)
        if delta is not None:
            self._record_materialization(connection, task, run, result, delta, validated["cards"])
        task["state"] = "completed"
        self._queue_next_stage(connection, task, run, validated)

    def _queue_next_stage(self, connection, task, run, validated):
        next_stage = (
            "verify"
            if is_card_only(run["policy"]) and task["stage"] == "author"
            else STAGES[STAGES.index(task["stage"]) + 1]
        )
        manifest = task.get("external_sources", [])
        refs = self._freeze_verification_receipts(connection, task, run, validated, next_stage)
        next_task = self._new_task(
            connection,
            run,
            task["article_id"],
            next_stage,
            digest({"submission": validated, "external_sources": manifest, **refs}),
            validated["cards"],
            manifest,
            research_disposition=validated.get("research_disposition"),
            research_audit_ref=refs.get("research_audit_ref"),
            mechanical_evidence_ref=refs.get("mechanical_evidence_ref"),
        )
        next_task["target_cards"] = deepcopy(task.get("target_cards", {}))
        next_task["research_plan"] = deepcopy(task.get("research_plan", []))
        self._save_task(connection, next_task)

    def _freeze_verification_receipts(self, connection, task, run, validated, next_stage):
        audit_ref = (
            self._freeze_research_audit(connection, task)
            if next_stage in VERIFY_STAGES
            and run["policy"].get("verify_search_audit")
            and (
                not is_card_only(run["policy"])
                or task.get("search_receipt_ids")
                or task.get("inherited_audit_ref")
            )
            else None
        )
        refs = {}
        if audit_ref:
            refs["research_audit_ref"] = audit_ref
        mechanical_ref = None
        if (
            next_stage in VERIFY_STAGES
            and run["policy"].get("version", 1) >= 4
            and run["policy"].get("verify_decoded_evidence")
        ):
            sources = {
                source["id"]: source
                for source in self._required_sources(task, run, validated["cards"])
            }
            mechanical_ref = self._freeze_source(
                connection,
                decoded_evidence_receipts(validated["cards"], sources, run["publication_sha256"]),
                "decoded_evidence",
            )
            refs["mechanical_evidence_ref"] = mechanical_ref
        return refs

    def _commit_submission(self, connection, task, run, validated, result_hash):
        task_token, worker_id = task["task_token"], task["worker_id"]
        task.update(result_hash=result_hash, completed_at=time.time(), expires_at=0)
        self._save_task(connection, task)
        connection.execute(
            "INSERT INTO research_submissions VALUES (?,?,?,?,?,?)",
            (
                task_token,
                task["task_id"],
                worker_id,
                result_hash,
                encode(validated),
                time.time(),
            ),
        )
        self.store.runtime_control.success(
            task["model"], "research:" + task_token, "research:" + task_token
        )
        self._event(
            connection,
            run["run_id"],
            "submitted",
            {"task_id": task["task_id"], "result_hash": result_hash},
        )
        self._refresh(connection, run)
        return self._status(connection, run["run_id"])

    def _publish_cards(self, connection, run, task, checks):
        if run.get("purpose") == "card_revision":
            original = self.card(run["replaces_card_id"])
            if original["version"] != run["revision_base_version"]:
                run.update(state="superseded", superseded_by_card_version=original["version"])
                self._event(
                    connection,
                    run["run_id"],
                    "revision_superseded",
                    {
                        "card_id": original["id"],
                        "current_version": original["version"],
                        "base_version": run["revision_base_version"],
                    },
                )
                return
        article = self._article(run, task["article_id"])
        drafts = self._task_cards(task)
        sources = {item["id"]: item for item in self._required_sources(task, run, drafts)}
        for draft in drafts:
            card = deepcopy(draft)
            identity = digest([run["run_id"], article["id"], card["id"]])[:32]
            state = "verified" if checks["decision"] == "approve" else "pending"
            replaces = run.get("replaces_card_id")
            linked = None
            lineage = run.get("lineage_id") or identity
            if run.get("purpose") == "link_refresh" and run.get("research_scope", {}).get(
                "source_card_id"
            ):
                original = self.card(run["research_scope"]["source_card_id"])
                if original["state"] == "excluded":
                    run.update(state="superseded", superseded_by_card_version=original["version"])
                    self._event(
                        connection,
                        run["run_id"],
                        "link_source_excluded",
                        {
                            "card_id": original["id"],
                            "current_version": original["version"],
                        },
                    )
                    return
                lineage = original.get("lineage_id") or original["id"]
                if (
                    original["state"] == "human_accepted"
                    or run["research_scope"].get("preserve_source_card") is True
                ):
                    linked = original["id"]
                    replaces = None
                else:
                    replaces = original["id"]
            card.update(
                id=identity,
                local_id=draft["id"],
                run_id=run["run_id"],
                book_id=run["book_id"],
                publication_sha256=run["publication_sha256"],
                article_sha256=article["text_sha256"],
                article_title=article.get("title", ""),
                version=1,
                state=state,
                approval_type="native" if state == "verified" else None,
                checks=checks,
                research_disposition=task.get("research_disposition"),
                source_run_id=run["source_run_id"],
                created_at=time.time(),
                document_id=run["document_id"],
                article_key=digest([run["book_id"], run["document_id"], article["id"]]),
                research_created_at=run["created_at"],
                purpose=run.get("purpose", "book"),
                replaces_card_id=replaces,
                linked_card_id=linked,
                lineage_id=lineage,
                card_only=is_card_only(run["policy"]),
                dossier_cards=is_dossier(run["policy"]),
                processing_mode="source_cards"
                if is_card_only(run["policy"])
                else "legacy_research",
            )
            for evidence in card["evidence"]:
                source_article = sources[evidence["article_id"]]
                parts = [
                    part
                    for part in source_article["parts"]
                    if part["start"] < evidence["end"] and part["end"] > evidence["start"]
                ]
                evidence["sources"] = [
                    {key: deepcopy(value) for key, value in part.items() if key != "text"}
                    for part in parts
                ]
                evidence["pages"] = sorted(
                    {page for part in parts for page in part["source"]["pages"]}
                )
                evidence["book_id"] = source_article.get("book_id", run["book_id"])
                evidence["source_run_id"] = source_article.get(
                    "source_run_id", run["source_run_id"]
                )
                evidence["original_article_id"] = source_article.get(
                    "original_article_id", source_article["id"]
                )
                evidence["publication_sha256"] = source_article.get(
                    "publication_sha256", run["publication_sha256"]
                )
                evidence["article_sha256"] = source_article["text_sha256"]
                owned_notes = [
                    note
                    for note in source_article.get("footnotes", [])
                    if any(
                        location.get("start", evidence["end"]) < evidence["end"]
                        and location.get("end", evidence["start"]) > evidence["start"]
                        for location in [note.get("note", {}), *note.get("references", [])]
                    )
                ]
                evidence["footnote_ids"] = sorted(
                    {*evidence["footnote_ids"], *(str(note["id"]) for note in owned_notes)}
                )
                evidence["footnotes"] = deepcopy(owned_notes)
            connection.execute(
                "INSERT INTO research_cards VALUES (?,?,?,?,?,?)",
                (identity, 1, run["run_id"], run["book_id"], state, encode(card)),
            )
            IdentityIndex.record(connection, card)
            self.catalogue.record(card, connection=connection)

    def _refresh(self, connection, run):
        if run["state"] in {"paused", "cancelled", "technical_blocked", "superseded"}:
            self._save_run(connection, run)
            return
        tasks = self._tasks(connection, run["run_id"])
        active = any(task["state"] in {"ready", "leased"} for task in tasks)
        if active:
            run["state"] = "running"
        elif any(task["state"] == "failed" for task in tasks):
            run["state"] = "failed"
        elif any(card["state"] == "pending" for card in self._card_rows(connection, run["run_id"])):
            run["state"] = "pending"
        else:
            run["state"] = "completed"
        self._save_run(connection, run)

    def fail(self, task_token, worker_id, code, *, details=None):
        details = failure_details(details)
        if code not in FAILURE_CODES:
            raise ValueError(
                "Research failure must identify a technical failure, never a content verdict"
            )
        with self.ledger.connect(write=True) as connection:
            task, run = self._lease(connection, task_token, worker_id)
            return self._record_task_failure(connection, task, run, code, details)

    def _record_task_failure(self, connection, task, run, code, details):
        """Shared commit for a live MCP failure or a host-attested retired lease."""
        if code not in FAILURE_CODES:
            raise ValueError(
                "Research failure must identify a technical failure, never a content verdict"
            )
        task_token, worker_id = task["task_token"], task["worker_id"]
        diagnostic = details if isinstance(details, dict) else {}
        if provider_failure({**diagnostic, "code": code}):
            self.store.runtime_control.failure(
                code,
                diagnostic.get("event_id") or "research:" + task_token,
                model=task["model"],
                scope=diagnostic.get("scope", "model"),
                retry_after_seconds=diagnostic.get("retry_after"),
            )
        self.store.runtime_control.release("research:" + task_token)
        self._event(
            connection,
            run["run_id"],
            "technical_failure",
            {
                "task_token": task_token,
                "worker_id": worker_id,
                "code": code,
                "details": details,
            },
        )
        failures = task.get("technical_failures", 0) + 1
        task.update(
            state="failed" if failures >= run["policy"]["max_technical_attempts"] else "ready",
            technical_failures=failures,
            last_failure={"code": code, "details": details, "at": time.time()},
            task_token=None,
            worker_id=None,
            expires_at=0,
        )
        self._save_task(connection, task)
        self._refresh(connection, run)
        return self._status(connection, run["run_id"])

    def pause(self, run_id):
        with self.ledger.connect(write=True) as connection:
            run = self._run(connection, run_id)
            if run["state"] in {"completed", "cancelled", "superseded"}:
                return self._status(connection, run_id)
            for task in self._tasks(connection, run_id):
                if task["state"] == "leased":
                    self.store.runtime_control.release("research:" + task["task_token"])
                    task.update(state="ready", task_token=None, worker_id=None, expires_at=0)
                    self._save_task(connection, task)
            run["state"] = "paused"
            self._save_run(connection, run)
            self._event(connection, run_id, "paused", {})
            return self._status(connection, run_id)

    def resume(self, run_id):
        with self.ledger.connect(write=True) as connection:
            run = self._run(connection, run_id)
            if run["state"] in {"cancelled", "superseded"}:
                raise WorkflowStopped("Cancelled or superseded research cannot be resumed")
            run.update(state="ready", runtime_failures=0, next_retry_at=0)
            recovered = self.store.runtime_control.recover_local_errors(
                models={
                    *run["policy"].get("models", {}).values(),
                    run["policy"].get("coordinator_model", run["policy"].get("model", SOL)),
                }
            )
            if recovered:
                self._event(connection, run_id, "local_runtime_recovered", {"scopes": recovered})
            for task in self._tasks(connection, run_id):
                if task["state"] in {"failed", "ready"}:
                    task.update(state="ready", technical_failures=0)
                    self._save_task(connection, task)
            self._refresh(connection, run)
            self._event(connection, run_id, "resumed", {})
            return self._status(connection, run_id)

    def runtime_failure(self, run_id, failure):
        """Bound coordinator restart attempts durably; content decisions stay intact."""
        failure = failure_details(failure)
        with self.ledger.connect(write=True) as connection:
            run = self._run(connection, run_id)
            if run["state"] in {"cancelled", "completed", "pending", "paused", "superseded"}:
                return self._status(connection, run_id)
            count = run.get("runtime_failures", 0) + 1
            for task in self._tasks(connection, run_id):
                if task["state"] == "leased":
                    self.store.runtime_control.release("research:" + task["task_token"])
                    task.update(state="ready", task_token=None, worker_id=None, expires_at=0)
                    self._save_task(connection, task)
            run.update(
                runtime_failures=count,
                last_runtime_failure=deepcopy(failure),
                next_retry_at=time.time() + min(300, 30 * 2 ** (count - 1)),
                state="technical_blocked" if count >= 3 else "ready",
            )
            self._save_run(connection, run)
            self._event(
                connection, run_id, "coordinator_failure", {"failure": failure, "attempt": count}
            )
            return self._status(connection, run_id)

    def cancel_book(self, book_id):
        with self.ledger.connect(write=True) as connection:
            if not self._exists(connection):
                return []
            runs = connection.execute(
                "SELECT id FROM research_runs WHERE book_id=?", (str(book_id),)
            ).fetchall()
            cancelled = []
            for (run_id,) in runs:
                run = self._run(connection, run_id)
                for task in self._tasks(connection, run_id):
                    if task["state"] == "leased":
                        self.store.runtime_control.release("research:" + task["task_token"])
                    if task["state"] in {"ready", "leased", "failed"}:
                        task.update(
                            state="cancelled", task_token=None, worker_id=None, expires_at=0
                        )
                        self._save_task(connection, task)
                run["state"] = "cancelled"
                self._save_run(connection, run)
                self._event(connection, run_id, "book_cancelled", {"book_id": str(book_id)})
                cancelled.append(self._status(connection, run_id))
            return cancelled

    def _status(self, connection, run_id):
        run = self._run(connection, run_id)
        result = {key: value for key, value in run.items() if key != "snapshot"}
        result.update(
            card_only=is_card_only(run["policy"]),
            dossier_cards=is_dossier(run["policy"]),
            processing_mode="source_cards" if is_card_only(run["policy"]) else "legacy_research",
        )
        tasks = self._tasks(connection, run_id)
        result["tasks"] = [
            {key: value for key, value in task.items() if key not in {"cards", "task_token"}}
            for task in tasks
        ]
        for task in result["tasks"]:
            task.pop("external_sources", None)
            task.update(id=task["task_id"], not_before=0)
        result["counts"] = {
            state: sum(task["state"] == state for task in tasks)
            for state in ("ready", "leased", "completed", "pending", "failed")
        }
        result["counts"]["articles_total"] = len(
            run.get("article_index", run.get("snapshot", {}).get("articles", []))
        )
        cards = self._card_rows(connection, run_id)
        unresolved = {card["article_id"] for card in cards if card["state"] == "pending"}
        published = {card["article_id"] for card in cards}
        result["counts"]["articles_completed"] = len(published - unresolved)
        result["counts"]["articles_pending"] = len(unresolved)
        result["counts"]["native_pending"] = sum(task["state"] == "pending" for task in tasks)
        result["card_ids"] = [card["id"] for card in cards]
        return result

    @staticmethod
    def _card_rows(connection, run_id):
        return [
            {"id": identity, "state": state, "article_id": article_id}
            for identity, state, article_id in connection.execute(
                "SELECT c.id,c.state,json_extract(c.payload,'$.article_id') FROM research_cards c "
                "WHERE c.run_id=? AND c.version=(SELECT MAX(v.version) FROM research_cards v WHERE v.id=c.id)",
                (run_id,),
            )
        ]

    def status(self, run_id):
        with self.ledger.connect() as connection:
            return self._status(connection, run_id)

    def list_runs(self, book_id=None):
        with self.ledger.connect() as connection:
            if not self._exists(connection):
                return []
            rows = connection.execute(
                "SELECT id FROM research_runs" + (" WHERE book_id=?" if book_id else ""),
                (str(book_id),) if book_id else (),
            ).fetchall()
            return sorted(
                (self._status(connection, row[0]) for row in rows),
                key=lambda run: run["created_at"],
                reverse=True,
            )

    def pending_runs(self):
        # The queue recovers interrupted leases before its dispatcher selects ready stages.
        with self.ledger.connect(write=True) as connection:
            if self._exists(connection):
                self._expire(connection)
        return [
            run
            for run in self.list_runs()
            if run["state"] in {"ready", "running"} and run.get("next_retry_at", 0) <= time.time()
        ]

    def has_live_work(self, now=None):
        """Read-only backup guard, including control-only standalone coordinators."""
        now = time.time() if now is None else now
        with self.ledger.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('research_tasks','runtime_admissions')"
                )
            }
            if (
                "research_tasks" in tables
                and connection.execute(
                    "SELECT 1 FROM research_tasks WHERE state='leased' AND expires>? LIMIT 1",
                    (now,),
                ).fetchone()
            ):
                return True
            return bool(
                "runtime_admissions" in tables
                and connection.execute(
                    "SELECT 1 FROM runtime_admissions WHERE owner GLOB 'research-coordinator:*' "
                    "AND json_extract(payload,'$.expires_at')>? LIMIT 1",
                    (now,),
                ).fetchone()
            )

    @staticmethod
    def _cards(connection, *, book_id=None, run_id=None):
        filters, parameters = [], []
        for field, value in (("book_id", book_id), ("run_id", run_id)):
            if value is not None:
                filters.append("c." + field + "=?")
                parameters.append(str(value))
        where = " AND " + " AND ".join(filters) if filters else ""
        return [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT c.payload FROM research_cards c WHERE c.version=(SELECT MAX(v.version) FROM research_cards v "
                "WHERE v.id=c.id)" + where + " ORDER BY c.id",
                parameters,
            )
        ]

    def list_cards(self, book_id=None, run_id=None):
        with self.ledger.connect() as connection:
            return (
                self._cards(connection, book_id=book_id, run_id=run_id)
                if self._exists(connection)
                else []
            )

    def card(self, card_id, *, version=None):
        with self.ledger.connect() as connection:
            if not self._exists(connection):
                raise FileNotFoundError(card_id)
            row = connection.execute(
                "SELECT payload FROM research_cards WHERE id=?"
                + (" AND version=?" if version is not None else " ORDER BY version DESC LIMIT 1"),
                (card_id, version) if version is not None else (card_id,),
            ).fetchone()
            if not row:
                raise FileNotFoundError(card_id)
            card = json.loads(row[0])
            card["revision_versions"] = [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM research_cards WHERE id=? ORDER BY version", (card_id,)
                )
            ]
            return card

    def list_entities(self, book_id=None, run_id=None):
        mentions = [
            entity for card in self.list_cards(book_id, run_id) for entity in card_entities(card)
        ]
        return self.catalogue.annotate_mentions(mentions)

    def entity(self, entity_id):
        try:
            return self.catalogue.entity(entity_id)
        except FileNotFoundError:
            return IdentityIndex(self.ledger).entity(entity_id)

    def catalogue_entities(
        self, book_id=None, *, book_ids=None, query=None, kind=None, offset=0, limit=100
    ):
        return self.catalogue.entities(
            book_id=book_id, book_ids=book_ids, query=query, kind=kind, offset=offset, limit=limit
        )

    def identity_decision(self, entity_id, action, expected_version, **values):
        return self.catalogue.decision(entity_id, action, expected_version, **values)

    @staticmethod
    def _revision_draft(value, original, policy=None):
        card_type = card_model(policy or {})
        if value.get("dossier") is not None and not is_dossier(policy or {}):
            raise ValueError(
                "A retained pre-v7 card revision cannot add a dossier under its frozen policy"
            )
        payload = {key: deepcopy(value[key]) for key in card_type.model_fields if key in value}
        payload["id"] = original.get("local_id", original["id"])
        payload["article_id"] = original["article_id"]
        for key, model in (
            ("evidence", EvidenceSpan),
            ("mentions", EntityMention),
            ("events", EventDraft),
        ):
            payload[key] = [
                {field: entry[field] for field in model.model_fields if field in entry}
                for entry in payload.get(key, [])
            ]
        return card_type.model_validate(payload).model_dump(mode="json")

    def card_draft(self, card_id):
        original = self.card(card_id)
        with self.ledger.connect() as connection:
            policy = self._run(connection, original["run_id"])["policy"]
        return self._revision_draft(original, original, policy)

    def card_revision(self, card_id, expected_version, draft, *, reason=""):
        """Keep the publication readable while a structured user draft gets independent review."""
        if type(expected_version) is not int:
            raise ValueError("Card revision requires its current integer version")
        with self.ledger.connect(write=True) as connection:
            self._schema(connection)
            original = self.card(card_id)
            if original["version"] != expected_version:
                raise LeaseConflict("Card changed; reload before saving its structured revision")
            previous_run = self._run(connection, original["run_id"])
            candidate = self._revision_draft(draft, original, previous_run["policy"])
            request_hash = digest({"draft": candidate, "reason": reason})
            existing = connection.execute(
                "SELECT request_hash,run_id FROM research_revision_requests WHERE card_id=? AND base_version=?",
                (card_id, expected_version),
            ).fetchone()
            if existing:
                if existing[0] != request_hash:
                    raise LeaseConflict(
                        "This card already has a review revision; open that retained draft before editing again"
                    )
                return self._status(connection, existing[1])
            old_tasks = self._tasks(connection, previous_run["run_id"])
            prior = next(
                (
                    task
                    for task in reversed(old_tasks)
                    if task["article_id"] == original["article_id"]
                    and task["stage"] in VERIFY_STAGES
                ),
                {},
            )
            source = self.snapshot(previous_run["run_id"])
            source["articles"] = [
                article for article in source["articles"] if article["id"] == original["article_id"]
            ]
            source["revision_request"] = {
                "source_card_id": card_id,
                "base_version": expected_version,
                "draft_sha256": digest(candidate),
                "reason": reason,
            }
            scaffold = {**prior, "stage": "author", "article_id": original["article_id"]}
            self._validate_cards([candidate], scaffold, previous_run)
            self._require_targets_read(connection, scaffold, [candidate], {}, verify_ranges=False)
            policy = deepcopy(previous_run["policy"])
            policy["max_content_repairs"] = (
                0  # A machine may review a human draft, never rewrite it autonomously.
            )
            result = self.enqueue(
                source,
                revision_of=previous_run["run_id"],
                reason=reason.strip() or "Structured user card revision",
                purpose="card_revision",
                _policy=policy,
                _connection=connection,
            )
            run = self._run(connection, result["run_id"])
            run.update(
                replaces_card_id=card_id,
                revision_base_version=expected_version,
                lineage_id=original.get("lineage_id") or card_id,
            )
            self._save_run(connection, run)
            author = self._tasks(connection, run["run_id"])[0]
            author.update(
                state="completed",
                completed_at=time.time(),
                candidate_origin="user",
                result_hash=digest(candidate),
                worker_id="local-user-draft",
            )
            self._save_task(connection, author)
            sources = {
                source["articles"][0]["id"]: source["articles"][0],
                **{item["id"]: item for item in self.sources(previous_run["run_id"])},
            }
            mechanical = (
                self._freeze_source(
                    connection,
                    decoded_evidence_receipts([candidate], sources, run["publication_sha256"]),
                    "decoded_evidence",
                )
                if policy.get("verify_decoded_evidence")
                else None
            )
            verifier = self._new_task(
                connection,
                run,
                original["article_id"],
                "verify",
                digest(
                    {
                        "draft": candidate,
                        "request_hash": request_hash,
                        "audit_ref": prior.get("research_audit_ref"),
                        "mechanical_ref": mechanical,
                    }
                ),
                [candidate],
                prior.get("external_sources", []),
                research_audit_ref=prior.get("research_audit_ref"),
                mechanical_evidence_ref=mechanical,
            )
            verifier["target_cards"] = deepcopy(prior.get("target_cards", {}))
            self._save_task(connection, verifier)
            connection.execute(
                "INSERT INTO research_revision_requests VALUES (?,?,?,?,?)",
                (
                    card_id,
                    expected_version,
                    request_hash,
                    run["run_id"],
                    encode(source["revision_request"]),
                ),
            )
            self._event(
                connection, run["run_id"], "user_revision_candidate", source["revision_request"]
            )
            self._refresh(connection, run)
            return self._status(connection, run["run_id"])

    def card_relationships(self, card_id, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("Card relationship limit must be 1–200")
        with self.ledger.connect() as connection:
            if not ResearchCatalogue.exists(connection):
                return {"items": [], "truncated": False}
            original = self.card(card_id)
            lineage = original.get("lineage_id") or original["id"]
            family = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT id FROM research_cards WHERE id=? OR json_extract(payload,'$.lineage_id')=? LIMIT 500",
                    (lineage, lineage),
                )
            ]
            family = list(dict.fromkeys([card_id, *family]))
            marks = ",".join("?" for _ in family)
            rows = connection.execute(
                "SELECT payload FROM research_catalogue_edges WHERE active=1 "
                "AND json_extract(payload,'$.target_card_id') IS NOT NULL "
                f"AND (source_id IN ({marks}) OR target_id IN ({marks})) ORDER BY id LIMIT ?",
                [*family, *family, limit + 1],
            ).fetchall()
            return {
                "items": [json.loads(row[0]) for row in rows[:limit]],
                "truncated": len(rows) > limit,
            }

    def pending_items(self, book_id=None, *, limit=100):
        from hrs.research.views import ResearchViews

        return ResearchViews(self).pending(book_id=book_id, limit=limit)

    def reading_annotations(self, article_id, text_sha256, *, limit=200):
        return self.catalogue.reading(article_id, text_sha256, limit=limit)

    def graph(self, entity_id, *, hops=1, limit=50):
        return self.catalogue.graph(entity_id, hops=hops, limit=limit)

    def relation(self, identity):
        return self.catalogue.relation(identity)

    def timeline(self, *, entity_id=None, book_id=None, limit=100):
        from hrs.research.views import ResearchViews

        return ResearchViews(self).timeline(entity_id=entity_id, book_id=book_id, limit=limit)

    def link_refresh_candidates(self, book_id, *, limit=20):
        from hrs.research.views import ResearchViews

        return ResearchViews(self).link_candidates(book_id, limit=limit)

    def upgrade_research_indexes(self):
        with self.ledger.connect(write=True) as connection:
            if self._exists(connection):
                self._schema(connection)
                for (payload,) in connection.execute(
                    "SELECT c.payload FROM research_cards c WHERE c.version=(SELECT MAX(v.version) FROM research_cards v WHERE v.id=c.id) "
                    "AND NOT EXISTS(SELECT 1 FROM research_catalogue_recorded i WHERE i.card_id=c.id AND i.version=c.version)"
                ).fetchall():
                    self.catalogue.record(json.loads(payload), connection=connection)

    def related(self, task_token, entity_id, *, hops=1, limit=50):
        """A live research task may inspect retained, evidence-bearing graph leads."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Research graph limit must be 1–100")
        with self.ledger.connect() as connection:
            task, run = self._lease(connection, task_token)
            if task["stage"] not in VERIFY_STAGES and not self._retrieval_stage(task, run):
                raise ValueError("Graph research is available to enrichment and verification")
            try:
                from hrs.research.graph import graph_leads

                return graph_leads(
                    self.graph(entity_id, hops=hops, limit=min(limit, 8)), requested_limit=limit
                )
            except FileNotFoundError:
                from hrs.research.graph import ResearchGraph

                return ResearchGraph(self).related(entity_id, hops=hops, limit=limit)

    def list_books(self):
        books = {}
        for run in self.list_runs():
            books.setdefault(
                run["book_id"],
                {"book_id": run["book_id"], "title": run["title"], "latest_run": run},
            )
        return list(books.values())

    def book(self, book_id):
        runs = self.list_runs(book_id)
        if not runs:
            raise FileNotFoundError(book_id)
        return {
            "book_id": str(book_id),
            "title": runs[0]["title"],
            "runs": runs,
            "latest_run": runs[0],
            "cards": self.list_cards(book_id),
            "entities": self.list_entities(book_id),
        }

    def decision(
        self, card_id, action, expected_version, edited_text=None, *, limitations=None, text=None
    ):
        """Local single-user API only: genuine human resolution, never a native receipt."""
        edited_text = edited_text if edited_text is not None else text
        if action not in {"accept", "edit", "reject"} or type(expected_version) is not int:
            raise ValueError("Human decision requires accept/edit/reject and an expected version")
        if action == "edit" and (not isinstance(edited_text, str) or not edited_text.strip()):
            raise ValueError("An edited card requires its replacement summary")
        with self.ledger.connect(write=True) as connection:
            row = connection.execute(
                "SELECT version,payload FROM research_cards WHERE id=? ORDER BY version DESC LIMIT 1",
                (card_id,),
            ).fetchone()
            if not row:
                raise FileNotFoundError(card_id)
            if row[0] != expected_version:
                raise LeaseConflict(
                    "Card revision changed; reload before submitting a human decision"
                )
            if connection.execute(
                "SELECT 1 FROM research_revision_requests rr JOIN research_runs r ON r.id=rr.run_id "
                "WHERE rr.card_id=? AND rr.base_version=? AND r.state IN ('ready','running') LIMIT 1",
                (card_id, expected_version),
            ).fetchone():
                raise LeaseConflict(
                    "A structured revision is under review; finish or pause that revision first"
                )
            card = json.loads(row[1])
            if action == "edit":
                card["summary"] = edited_text
                if limitations is not None:
                    if not isinstance(limitations, list) or any(
                        not isinstance(item, str) for item in limitations
                    ):
                        raise ValueError("Card limitations must be a list of text")
                    card["limitations"] = limitations
            card.update(
                version=expected_version + 1,
                state="excluded" if action == "reject" else "human_accepted",
                approval_type="user",
                human_decision={
                    "action": action,
                    "at": time.time(),
                    "previous_version": expected_version,
                },
            )
            connection.execute(
                "INSERT INTO research_cards VALUES (?,?,?,?,?,?)",
                (
                    card_id,
                    card["version"],
                    card["run_id"],
                    card["book_id"],
                    card["state"],
                    encode(card),
                ),
            )
            run = self._run(connection, card["run_id"])
            self.catalogue.record(card, connection=connection)
            self._event(
                connection,
                run["run_id"],
                "human_decision",
                {
                    "card_id": card_id,
                    "action": action,
                    "version": card["version"],
                    "approval_type": "user",
                },
            )
            self._refresh(connection, run)
            return card
