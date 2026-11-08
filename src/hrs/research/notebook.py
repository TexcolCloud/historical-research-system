"""Versioned user questions and selected evidence in the native SQLite authority.

Notebook records are research notes, never source approval or a second task engine.
The HTTP adapter resolves and validates live source references before selecting them.
"""

import json
import time
from copy import deepcopy
from uuid import uuid4

from hrs.processing.hashing import digest
from hrs.processing.ledger import encode

STATUSES = {"open", "researching", "ready", "resolved", "archived"}
TERMINAL_RUN_STATES = {"completed", "pending", "paused", "cancelled", "failed", "technical_blocked"}


class ResearchNotebook:
    def __init__(self, store):
        self.store = store
        self.ledger = store.checkpoints.ledger
        if self.ledger is None:
            raise ValueError("Research notes require a writable native ledger")

    @staticmethod
    def _exists(connection):
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_questions'"
            ).fetchone()
            is not None
        )

    def _schema(self, connection):
        self.store.checkpoints.require_writable()
        for statement in (
            "CREATE TABLE IF NOT EXISTS research_questions (id TEXT PRIMARY KEY, "
            "version INTEGER NOT NULL, status TEXT NOT NULL, updated_at REAL NOT NULL, payload TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS research_question_books (question_id TEXT NOT NULL REFERENCES "
            "research_questions(id), book_id TEXT NOT NULL, PRIMARY KEY(question_id,book_id))",
            "CREATE INDEX IF NOT EXISTS research_question_book_lookup ON research_question_books(book_id)",
            "CREATE TABLE IF NOT EXISTS research_question_revisions (question_id TEXT NOT NULL REFERENCES "
            "research_questions(id), version INTEGER NOT NULL, event TEXT NOT NULL, payload TEXT NOT NULL, "
            "PRIMARY KEY(question_id,version))",
            "CREATE TABLE IF NOT EXISTS research_selections (id TEXT PRIMARY KEY, question_id TEXT NOT NULL REFERENCES "
            "research_questions(id), fingerprint TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, "
            "UNIQUE(question_id,fingerprint))",
            "CREATE TABLE IF NOT EXISTS research_selection_revisions (selection_id TEXT NOT NULL REFERENCES "
            "research_selections(id), version INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(selection_id,version))",
            "CREATE TABLE IF NOT EXISTS research_question_findings (id TEXT PRIMARY KEY, question_id TEXT NOT NULL "
            "REFERENCES research_questions(id), card_id TEXT NOT NULL, card_version INTEGER NOT NULL, payload TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS research_question_finding_lookup ON research_question_findings(question_id)",
            "CREATE TABLE IF NOT EXISTS research_notebook_cards (id TEXT PRIMARY KEY, version INTEGER NOT NULL, state TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS research_question_runs (question_id TEXT NOT NULL REFERENCES research_questions(id), "
            "run_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(question_id,run_id))",
            "CREATE INDEX IF NOT EXISTS research_question_run_lookup ON research_question_runs(run_id)",
            "CREATE INDEX IF NOT EXISTS research_question_run_definition ON "
            "research_question_runs(question_id,json_extract(payload,'$.definition_version'))",
        ):
            connection.execute(statement)

    @staticmethod
    def _read(connection, identity):
        if not ResearchNotebook._exists(connection):
            raise FileNotFoundError(identity)
        row = connection.execute(
            "SELECT payload FROM research_questions WHERE id=?", (identity,)
        ).fetchone()
        if row is None:
            raise FileNotFoundError(identity)
        return json.loads(row[0])

    @staticmethod
    def _expect(question, expected_version):
        if type(expected_version) is not int or question["version"] != expected_version:
            raise ValueError("Research question changed; reload the current version before saving")

    @staticmethod
    def _text(value, field, *, required=False, maximum=60000):
        if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
            raise ValueError(f"Invalid research {field}")
        return value.strip() if required else value

    @staticmethod
    def _books(values):
        if (
            not isinstance(values, (list, tuple))
            or len(values) > 200
            or any(
                not isinstance(value, str) or not value.strip() or len(value) > 100
                for value in values
            )
        ):
            raise ValueError("Research scope must contain at most 200 book IDs")
        return sorted(set(values))

    @staticmethod
    def _write(connection, question, event):
        question["version"] += 1
        question["updated_at"] = time.time()
        connection.execute(
            "INSERT INTO research_questions VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "version=excluded.version,status=excluded.status,updated_at=excluded.updated_at,payload=excluded.payload",
            (
                question["id"],
                question["version"],
                question["status"],
                question["updated_at"],
                encode(question),
            ),
        )
        connection.execute(
            "INSERT INTO research_question_revisions VALUES (?,?,?,?)",
            (question["id"], question["version"], event, encode(question)),
        )
        connection.execute(
            "DELETE FROM research_question_books WHERE question_id=?", (question["id"],)
        )
        connection.executemany(
            "INSERT INTO research_question_books VALUES (?,?)",
            [(question["id"], book) for book in question["book_ids"]],
        )
        return deepcopy(question)

    def create(self, title, book_ids=(), *, notes=""):
        question = {
            "id": uuid4().hex,
            "title": self._text(title, "question", required=True, maximum=2000),
            "book_ids": self._books(book_ids),
            "notes": self._text(notes, "notes"),
            "status": "open",
            "version": 0,
            "definition_version": 1,
            "created_at": time.time(),
        }
        with self.ledger.connect(write=True) as connection:
            self._schema(connection)
            return self._write(connection, question, "created")

    def update(
        self, identity, expected_version, *, title=None, book_ids=None, notes=None, status=None
    ):
        with self.ledger.connect(write=True) as connection:
            self._schema(connection)
            question = self._read(connection, identity)
            self._expect(question, expected_version)
            previous = (question["title"], question["book_ids"])
            if title is not None:
                question["title"] = self._text(title, "question", required=True, maximum=2000)
            if book_ids is not None:
                question["book_ids"] = self._books(book_ids)
            if notes is not None:
                question["notes"] = self._text(notes, "notes")
            if status is not None:
                if status not in {"open", "resolved", "archived"}:
                    raise ValueError("Only a research task sets researching or ready state")
                question["status"] = status
            if previous != (question["title"], question["book_ids"]):
                question["definition_version"] += 1
            self._write(connection, question, "user_updated")
        return self.question(identity)

    def question(self, identity):
        with self.ledger.connect() as connection:
            question = self._read(connection, identity)
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_selections'"
            ).fetchone()
            rows = (
                connection.execute(
                    "SELECT payload FROM research_selections WHERE question_id=? ORDER BY id LIMIT 201",
                    (identity,),
                ).fetchall()
                if exists
                else []
            )
            selections = [json.loads(row[0]) for row in rows[:200]]
            for selection in selections:
                selection["definition_current"] = (
                    selection["definition_version"] == question["definition_version"]
                )
            found = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_question_findings'"
            ).fetchone()
            finding_rows = (
                connection.execute(
                    "SELECT f.payload,c.version,c.state FROM research_question_findings f "
                    "JOIN research_notebook_cards c ON c.id=f.card_id WHERE f.question_id=? ORDER BY f.id LIMIT 201",
                    (identity,),
                ).fetchall()
                if found
                else []
            )
            findings = []
            for payload, card_version, state in finding_rows[:200]:
                item = json.loads(payload)
                item["definition_current"] = (
                    item["definition_version"] == question["definition_version"]
                )
                item["card_current"] = item["card_version"] == card_version and state in {
                    "verified",
                    "human_accepted",
                }
                findings.append(item)
            runs = (
                connection.execute(
                    "SELECT payload FROM research_question_runs WHERE question_id=? ORDER BY run_id LIMIT 201",
                    (identity,),
                ).fetchall()
                if found
                else []
            )
            return {
                **question,
                "selections": selections,
                "selections_truncated": len(rows) > 200,
                "findings": findings,
                "findings_truncated": len(finding_rows) > 200,
                "runs": [json.loads(row[0]) for row in runs[:200]],
                "runs_truncated": len(runs) > 200,
            }

    def sync_card(self, card):
        """Idempotently retain already-reviewed findings; never change user conclusions."""
        findings = card.get("question_findings") or []
        accepted = (
            card.get("state") == "verified"
            and card.get("approval_type") == "native"
            and card.get("checks", {}).get("decision") == "approve"
        ) or (
            card.get("state") == "human_accepted"
            and card.get("approval_type") == "user"
            and card.get("human_decision", {}).get("action") in {"accept", "edit"}
        )
        if not accepted:
            with self.ledger.connect() as connection:
                if (
                    not self._exists(connection)
                    or not connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='research_notebook_cards'"
                    ).fetchone()
                ):
                    return 0
                tracked = connection.execute(
                    "SELECT 1 FROM research_notebook_cards WHERE id=?", (card["id"],)
                ).fetchone()
            if not tracked:
                return 0
        elif not findings:
            return 0
        changed = set()
        with self.ledger.connect(write=True) as connection:
            self._schema(connection)
            previous = connection.execute(
                "SELECT version,state FROM research_notebook_cards WHERE id=?", (card["id"],)
            ).fetchone()
            if previous and previous[0] > card["version"]:
                return 0
            connection.execute(
                "INSERT INTO research_notebook_cards VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "version=excluded.version,state=excluded.state",
                (card["id"], card["version"], card["state"]),
            )
            evidence = {span["id"]: span for span in card.get("evidence", [])}
            for finding in findings if accepted else []:
                question = self._read(connection, finding["question_id"])
                definition = finding.get("definition_version")
                if (
                    type(definition) is not int
                    or not 1 <= definition <= question["definition_version"]
                ):
                    raise ValueError(
                        "Finding requires an existing frozen research definition version"
                    )
                if finding.get("status") not in {"answered", "partial", "unresolved"}:
                    raise ValueError("Unknown research finding state")
                self._text(finding.get("text"), "finding", required=True)
                ids = finding.get("evidence_ids") or []
                if len(ids) != len(set(ids)) or set(ids) - evidence.keys():
                    raise ValueError("Research findings must retain their card's evidence")
                if finding["status"] != "unresolved" and not ids:
                    raise ValueError("An answered or partial finding requires source evidence")
                key = digest([card["id"], card["version"], finding["id"], finding["question_id"]])
                retained = {
                    **deepcopy(finding),
                    "id": key,
                    "finding_id": finding["id"],
                    "card_id": card["id"],
                    "card_version": card["version"],
                    "book_id": card["book_id"],
                    "run_id": card["run_id"],
                    "approval_type": card["approval_type"],
                    "evidence": [deepcopy(evidence[identity]) for identity in ids],
                }
                inserted = connection.execute(
                    "INSERT OR IGNORE INTO research_question_findings VALUES (?,?,?,?,?)",
                    (key, finding["question_id"], card["id"], card["version"], encode(retained)),
                ).rowcount
                if inserted:
                    changed.add(finding["question_id"])
            if previous and tuple(previous) != (card["version"], card["state"]):
                changed.update(
                    row[0]
                    for row in connection.execute(
                        "SELECT DISTINCT question_id FROM research_question_findings WHERE card_id=?",
                        (card["id"],),
                    )
                )
            for question_id in changed:
                self._write(
                    connection, self._read(connection, question_id), "reviewed_finding_retained"
                )
        return len(changed)

    @classmethod
    def _selection_record(cls, item):
        """Retain derived identity separately from exact original-source evidence."""
        reference = item.get("record_ref")
        versions = item.get("version_refs")
        if item["kind"] == "source" and (reference is not None or versions is not None):
            raise ValueError("Original-source selections do not bind derived records")
        if reference is not None:
            if (
                not isinstance(reference, dict)
                or set(reference) - {"kind", "id", "version", "sha256"}
                or reference.get("kind") != item["kind"]
                or reference.get("id") != item["target_id"]
            ):
                raise ValueError("Selected record identity must match the retained target")
            if "version" in reference and (
                type(reference["version"]) is not int or reference["version"] < 1
            ):
                raise ValueError("Selected record requires a positive integer version")
            if "sha256" in reference and (
                not isinstance(reference["sha256"], str)
                or len(reference["sha256"]) != 64
                or any(character not in "0123456789abcdef" for character in reference["sha256"])
            ):
                raise ValueError("Selected record requires its exact SHA-256")
            if not {"version", "sha256"}.intersection(reference):
                raise ValueError("Selected record requires a version or exact digest")
        if versions is not None:
            if not isinstance(versions, list) or not versions:
                raise ValueError("Selected record requires its owner versions")
            for version in versions:
                if (
                    not isinstance(version, dict)
                    or set(version) != {"card_id", "card_version"}
                    or type(version["card_version"]) is not int
                    or version["card_version"] < 1
                ):
                    raise ValueError("Selected owner requires an exact positive card version")
                cls._text(version["card_id"], "owner card ID", required=True, maximum=200)

    def select(self, identity, expected_version, selection, *, trusted_evidence=False):
        if not isinstance(selection, dict) or set(selection) - {
            "kind",
            "target_id",
            "title",
            "decision",
            "note",
            "evidence",
            "record_ref",
            "version_refs",
        }:
            raise ValueError(
                "Selections contain source references and notes, never source approval"
            )
        item = deepcopy(selection)
        if item.get("kind") not in {"source", "card", "entity", "relation"}:
            raise ValueError("Unknown selected research record kind")
        self._selection_record(item)
        self._text(item.get("target_id"), "target ID", required=True, maximum=200)
        self._text(item.get("title", ""), "selection title", maximum=1000)
        self._text(item.get("note", ""), "selection note")
        if item.get("decision", "saved") not in {"saved", "adopted", "excluded"}:
            raise ValueError("Unknown evidence selection decision")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("A selection requires retained evidence references")
        if not trusted_evidence and len(evidence) > 100:
            raise ValueError("An explicit selection accepts at most 100 evidence references")
        # Trusted collections are resolved from current server-owned records. Preserve
        # every reference, including complete cards larger than the explicit-ID API limit.
        snapshot_limit = 8 * 1024 * 1024 if trusted_evidence else 120000
        if len(encode(item).encode("utf-8")) > snapshot_limit:
            raise ValueError("Selected evidence exceeds the bounded snapshot size")
        for span in evidence:
            if not isinstance(span, dict) or not all(
                k in span for k in ("start", "end", "quote", "text_sha256")
            ):
                raise ValueError(
                    "Selected evidence requires exact text coordinates and source version"
                )
            start, end, quote = span["start"], span["end"], span["quote"]
            if type(start) is not int or type(end) is not int or start < 0 or end <= start:
                raise ValueError("Invalid selected evidence range")
            if not isinstance(quote, str) or len(quote) != end - start:
                raise ValueError("Selected quotation must match its Unicode-codepoint range")
            sha = span["text_sha256"]
            if (
                not isinstance(sha, str)
                or len(sha) != 64
                or any(c not in "0123456789abcdef" for c in sha)
            ):
                raise ValueError("Selected evidence requires its source text SHA-256")
        fingerprint_parts = [item["kind"], item["target_id"], evidence]
        if item.get("record_ref") is not None or item.get("version_refs") is not None:
            fingerprint_parts.extend([item.get("record_ref"), item.get("version_refs")])
        fingerprint = digest(fingerprint_parts)
        with self.ledger.connect(write=True) as connection:
            self._schema(connection)
            question = self._read(connection, identity)
            self._expect(question, expected_version)
            existing = connection.execute(
                "SELECT id FROM research_selections WHERE question_id=? AND fingerprint=?",
                (identity, fingerprint),
            ).fetchone()
            if existing is None:
                item.update(
                    id=uuid4().hex,
                    version=1,
                    definition_version=question["definition_version"],
                    created_at=time.time(),
                    decision=item.get("decision", "saved"),
                    note=item.get("note", ""),
                    title=item.get("title", ""),
                )
                connection.execute(
                    "INSERT INTO research_selections VALUES (?,?,?,?,?)",
                    (item["id"], identity, fingerprint, 1, encode(item)),
                )
                connection.execute(
                    "INSERT INTO research_selection_revisions VALUES (?,?,?)",
                    (item["id"], 1, encode(item)),
                )
                self._write(connection, question, "evidence_selected")
        return self.question(identity)

    def set_selection(self, identity, selection_id, expected_version, *, decision=None, note=None):
        with self.ledger.connect(write=True) as connection:
            self._schema(connection)
            question = self._read(connection, identity)
            self._expect(question, expected_version)
            row = connection.execute(
                "SELECT payload FROM research_selections WHERE question_id=? AND id=?",
                (identity, selection_id),
            ).fetchone()
            if row is None:
                raise FileNotFoundError(selection_id)
            selection = json.loads(row[0])
            if decision is not None:
                if decision not in {"saved", "adopted", "excluded"}:
                    raise ValueError("Unknown evidence selection decision")
                selection["decision"] = decision
                selection["definition_version"] = question["definition_version"]
            if note is not None:
                selection["note"] = self._text(note, "selection note")
            selection["version"] += 1
            connection.execute(
                "UPDATE research_selections SET version=?,payload=? WHERE id=?",
                (selection["version"], encode(selection), selection_id),
            )
            connection.execute(
                "INSERT INTO research_selection_revisions VALUES (?,?,?)",
                (selection_id, selection["version"], encode(selection)),
            )
            self._write(connection, question, "evidence_decided")
        return self.question(identity)

    def attach_runs(self, identity, expected_version, run_ids):
        if (
            not isinstance(run_ids, list)
            or not 1 <= len(run_ids) <= 200
            or any(
                not isinstance(run_id, str) or not run_id or len(run_id) > 100 for run_id in run_ids
            )
        ):
            raise ValueError("Question research requires 1–200 actual run IDs")
        with self.ledger.connect(write=True) as connection:
            self._schema(connection)
            question = self._read(connection, identity)
            self._expect(question, expected_version)
            if question["status"] == "archived":
                raise ValueError("Reopen the archived question before starting research")
            for run_id in sorted(set(run_ids)):
                value = {
                    "run_id": run_id,
                    "state": "ready",
                    "definition_version": question["definition_version"],
                }
                existing = connection.execute(
                    "SELECT payload FROM research_question_runs WHERE question_id=? AND run_id=?",
                    (identity, run_id),
                ).fetchone()
                if existing:
                    if (
                        json.loads(existing[0])["definition_version"]
                        != question["definition_version"]
                    ):
                        raise ValueError("A run cannot be rebound to a changed research question")
                    continue
                connection.execute(
                    "INSERT INTO research_question_runs VALUES (?,?,?)",
                    (identity, run_id, encode(value)),
                )
            states = self._run_states(connection, question)
            question["status"] = (
                "ready"
                if states and all(state in TERMINAL_RUN_STATES for state in states)
                else "researching"
            )
            self._write(connection, question, "research_requested")
        return self.question(identity)

    @staticmethod
    def _run_states(connection, question):
        return [
            row[0]
            for row in connection.execute(
                "SELECT json_extract(payload,'$.state') FROM research_question_runs "
                "WHERE question_id=? AND json_extract(payload,'$.definition_version')=?",
                (question["id"], question["definition_version"]),
            )
        ]

    def sync_run(self, status):
        """Project genuine native progress without treating completion as user agreement."""
        with self.ledger.connect() as connection:
            if (
                not self._exists(connection)
                or not connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='research_question_runs'"
                ).fetchone()
            ):
                return 0
            rows = connection.execute(
                "SELECT question_id,payload FROM research_question_runs WHERE run_id=?",
                (status["run_id"],),
            ).fetchall()
        changed = 0
        for identity, _ in rows:
            with self.ledger.connect(write=True) as connection:
                question = self._read(connection, identity)
                record = json.loads(
                    connection.execute(
                        "SELECT payload FROM research_question_runs WHERE question_id=? AND run_id=?",
                        (identity, status["run_id"]),
                    ).fetchone()[0]
                )
                incoming_version = status.get("version", record.get("run_version", 0))
                if incoming_version < record.get("run_version", 0):
                    continue
                if (
                    record["state"] == status["state"]
                    and record.get("run_version", 0) == incoming_version
                ):
                    continue
                state_changed = record["state"] != status["state"]
                record.update(state=status["state"], run_version=incoming_version)
                connection.execute(
                    "UPDATE research_question_runs SET payload=? WHERE question_id=? AND run_id=?",
                    (encode(record), identity, status["run_id"]),
                )
                current = self._run_states(connection, question)
                if question["status"] in {"researching", "ready"} and current:
                    progress = (
                        "ready"
                        if all(state in TERMINAL_RUN_STATES for state in current)
                        else "researching"
                    )
                    state_changed |= question["status"] != progress
                    question["status"] = progress
                if state_changed:
                    self._write(connection, question, "native_progress")
                    changed += 1
        return changed

    def research_context(self, book_ids=None, *, question_id=None):
        """Bounded frozen research intent; selected excerpts remain retrieval leads."""
        if question_id:
            question = self.question(question_id)
            if question["status"] == "archived":
                raise ValueError("Reopen the archived question before starting research")
            scope = set(self._books(book_ids or []))
            if scope and question["book_ids"] and not scope.intersection(question["book_ids"]):
                raise ValueError("Question is outside the selected research scope")
            questions, total = [question], 1
        else:
            result = self.questions(book_ids, status=["open", "researching", "ready"], limit=10)
            questions, total = (
                [self.question(row["id"]) for row in result["items"]],
                result["total"],
            )
        output = []
        for question in questions:
            selected = [
                item
                for item in question["selections"]
                if item["decision"] != "excluded" and item["definition_current"]
            ]
            leads = []
            for item in selected[:6]:
                evidence = []
                for span in item["evidence"][:2]:
                    evidence.append(
                        {
                            **{
                                k: span[k]
                                for k in (
                                    "book_id",
                                    "chapter_id",
                                    "article_id",
                                    "start",
                                    "end",
                                    "text_sha256",
                                )
                                if k in span
                            },
                            "quote_preview": span["quote"][:160],
                            "quote_sha256": digest(span["quote"]),
                            "preview_only": True,
                        }
                    )
                leads.append(
                    {
                        "kind": item["kind"],
                        "target_id": item["target_id"],
                        "title": item["title"],
                        "decision": item["decision"],
                        "evidence": evidence,
                        "evidence_omitted": max(0, len(item["evidence"]) - 2),
                    }
                )
            output.append(
                {
                    "id": question["id"],
                    "title": question["title"],
                    "book_ids": question["book_ids"],
                    "definition_version": question["definition_version"],
                    "selections": leads,
                    "selections_omitted": max(0, len(selected) - 6),
                }
            )
        return {"questions": output, "total": total, "omitted": total - len(output)}

    def questions(self, book_ids=None, *, status=None, offset=0, limit=50):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Question pagination requires a nonnegative offset and limit 1–100")
        books = self._books(book_ids) if book_ids is not None else []
        states = [status] if isinstance(status, str) else status or []
        if not isinstance(states, list) or any(state not in STATUSES for state in states):
            raise ValueError("Unknown research question state")
        conditions, values = [], []
        if books:
            marks = ",".join("?" for _ in books)
            conditions.append(
                f"(EXISTS (SELECT 1 FROM research_question_books b WHERE b.question_id=q.id AND b.book_id IN ({marks})) "
                "OR NOT EXISTS (SELECT 1 FROM research_question_books b WHERE b.question_id=q.id))"
            )
            values.extend(books)
        if states:
            conditions.append("q.status IN (" + ",".join("?" for _ in states) + ")")
            values.extend(states)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.ledger.connect() as connection:
            if not self._exists(connection):
                return {"items": [], "total": 0, "offset": offset, "limit": limit}
            total = connection.execute(
                "SELECT COUNT(*) FROM research_questions q" + where, values
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT q.payload FROM research_questions q"
                + where
                + " ORDER BY q.updated_at DESC,q.id LIMIT ? OFFSET ?",
                [*values, limit, offset],
            ).fetchall()
        return {
            "items": [json.loads(row[0]) for row in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def summary_counts(self, identities):
        """Count one page of notebook records without reading their retained evidence."""
        identities = list(dict.fromkeys(identities))
        if len(identities) > 100 or any(not isinstance(value, str) for value in identities):
            raise ValueError("Notebook summary counts require at most 100 question IDs")
        counts = {
            key: {"selection_count": 0, "finding_count": 0, "run_count": 0} for key in identities
        }
        if not identities:
            return counts
        with self.ledger.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            marks = ",".join("?" for _ in identities)
            for table, field in (
                ("research_selections", "selection_count"),
                ("research_question_findings", "finding_count"),
                ("research_question_runs", "run_count"),
            ):
                if table in tables:
                    for key, total in connection.execute(
                        f"SELECT question_id,COUNT(*) FROM {table} WHERE question_id IN ({marks}) GROUP BY question_id",
                        identities,
                    ):
                        counts[key][field] = total
        return counts

    def history(self, identity, *, offset=0, limit=50):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("History requires a nonnegative offset and limit 1–100")
        with self.ledger.connect() as connection:
            self._read(connection, identity)
            total = connection.execute(
                "SELECT COUNT(*) FROM research_question_revisions WHERE question_id=?", (identity,)
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT event,payload FROM research_question_revisions WHERE question_id=? ORDER BY version DESC LIMIT ? OFFSET ?",
                (identity, limit, offset),
            ).fetchall()
        return {
            "items": [{"event": event, **json.loads(payload)} for event, payload in rows],
            "total": total,
        }
