"""SQLite write authority: record CAS, atomic follow-ups and an ordered durable change feed.

JSON payloads retain the public evidence contract; pages, tasks and document fields
are independent records. No whole-document JSON is rewritten by a page commit.
"""

import json
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def records(document):
    result = {}
    for key, value in document.items():
        if key in {"pages", "tasks"}:
            kind, identity = ("page", "page") if key == "pages" else ("task", "task_id")
            # The field record distinguishes an absent collection from an empty one.
            result[("field", key)] = "null"
            for position, item in enumerate(value):
                record_key = (kind, str(item[identity]))
                if record_key in result:
                    raise ValueError(f"Duplicate {kind} identity")
                result[record_key] = encode({"position": position, "value": item})
        else:
            result[("field", key)] = encode(value)
    return result


class Snapshot(dict):
    """Private read versions travel with a mutable business snapshot, outside its JSON."""

    def __init__(self, document, baseline, revision):
        super().__init__(document)
        self.baseline = baseline
        self.revision = revision


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        self.local = threading.local()
        with closing(sqlite3.connect(path, timeout=30, isolation_level=None)) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 1:
                return
            if version != 0:
                raise ValueError(
                    "Unsupported native ledger schema; use the matching application version"
                )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS records (
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK(kind IN ('field','page','task')),
                    key TEXT NOT NULL, value TEXT NOT NULL, version INTEGER NOT NULL,
                    PRIMARY KEY(document_id, kind, key)
                );
                CREATE INDEX IF NOT EXISTS task_state ON records (
                    document_id, json_extract(value,'$.value.state'),
                    json_extract(value,'$.value.stage')
                ) WHERE kind='task';
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    created_at REAL NOT NULL, changes TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                    stage TEXT NOT NULL, state TEXT NOT NULL,
                    expires_at REAL NOT NULL, payload TEXT NOT NULL
                );
                PRAGMA user_version=1;
            """)

    @staticmethod
    def ensure_runtime_schema(connection):
        """Add control records only in an explicit runtime write transaction.

        Merely inspecting a sealed/older ledger must not change its schema. These
        additive tables contain scheduling state, never source approval evidence.
        """
        for statement in (
            "CREATE TABLE IF NOT EXISTS runtime_control (scope TEXT PRIMARY KEY, payload TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS runtime_events (identity TEXT PRIMARY KEY, payload TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS runtime_admissions (owner TEXT PRIMARY KEY, payload TEXT NOT NULL)",
        ):
            connection.execute(statement)

    @contextmanager
    def connect(self, *, write=False):
        existing = getattr(self.local, "connection", None)
        if existing is not None:
            yield existing
            return
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self.local.connection = connection
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            self.local.connection = None
            connection.close()

    def ids(self):
        with self.connect() as connection:
            return [row[0] for row in connection.execute("SELECT id FROM documents ORDER BY id")]

    def revision(self, document_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT revision FROM documents WHERE id=?", (document_id,)
            ).fetchone()
            if row is None:
                raise FileNotFoundError(document_id)
            return row[0]

    def read(self, document_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT revision FROM documents WHERE id=?", (document_id,)
            ).fetchone()
            if row is None:
                raise FileNotFoundError(document_id)
            baseline = {
                (kind, key): (value, version)
                for kind, key, value, version in connection.execute(
                    "SELECT kind,key,value,version FROM records WHERE document_id=?", (document_id,)
                )
            }
        document = {
            key: json.loads(value)
            for (kind, key), (value, _) in baseline.items()
            if kind == "field"
        }
        for kind, field in (("page", "pages"), ("task", "tasks")):
            if field in document:
                rows = [
                    json.loads(value)
                    for (record_kind, _), (value, _) in baseline.items()
                    if record_kind == kind
                ]
                document[field] = [
                    row["value"] for row in sorted(rows, key=lambda item: item["position"])
                ]
        return Snapshot(document, baseline, row[0])

    def write(self, document):
        identity = document["id"]
        desired = records(document)
        with self.connect(write=True) as connection:
            current = {
                (kind, key): (value, version)
                for kind, key, value, version in connection.execute(
                    "SELECT kind,key,value,version FROM records WHERE document_id=?", (identity,)
                )
            }
            baseline = document.baseline if isinstance(document, Snapshot) else current
            changed = sorted(
                key
                for key in baseline.keys() | desired.keys()
                if baseline.get(key, (None,))[0] != desired.get(key)
            )
            if not changed:
                return
            for key in changed:
                if current.get(key) != baseline.get(key):
                    raise ValueError(f"Concurrent {key[0]} update; reload the current snapshot")
            connection.execute("INSERT OR IGNORE INTO documents(id) VALUES (?)", (identity,))
            changes = []
            for kind, key in changed:
                version = current.get((kind, key), (None, 0))[1] + 1
                if (kind, key) not in desired:
                    connection.execute(
                        "DELETE FROM records WHERE document_id=? AND kind=? AND key=?",
                        (identity, kind, key),
                    )
                else:
                    connection.execute(
                        "INSERT INTO records VALUES (?,?,?,?,?) ON CONFLICT(document_id,kind,key) DO UPDATE SET value=excluded.value,version=excluded.version",
                        (identity, kind, key, desired[(kind, key)], version),
                    )
                changes.append({"kind": kind, "key": key, "version": version})
            connection.execute("UPDATE documents SET revision=revision+1 WHERE id=?", (identity,))
            revision = connection.execute(
                "SELECT revision FROM documents WHERE id=?", (identity,)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO events(document_id,revision,created_at,changes) VALUES (?,?,?,?)",
                (identity, revision, time.time(), encode(changes)),
            )
            request = document.get("run_request", {})
            if (
                document.get("state") == "published"
                or request.get("pause_requested")
                or request.get("cancel_requested")
                or request.get("state") in {"waiting_human", "paused", "cancelled", "succeeded"}
            ):
                connection.execute(
                    "UPDATE sessions SET state='exited',expires_at=?,payload=json_set(payload,'$.state','exited','$.exited_at',?,'$.exit_reason','document_stopped') WHERE document_id=? AND state<>'exited'",
                    (time.time(), time.time(), identity),
                )
        if isinstance(document, Snapshot):
            # Permit a second command in the same locked workflow to build on this commit.
            for key in changed:
                if key in desired:
                    document.baseline[key] = (desired[key], current.get(key, (None, 0))[1] + 1)
                else:
                    document.baseline.pop(key, None)
            document.revision = revision

    def events_after(self, sequence, limit=1000):
        if not 1 <= limit <= 10000 or sequence < 0:
            raise ValueError("Invalid change feed cursor or limit")
        with self.connect() as connection:
            return [
                {
                    "sequence": seq,
                    "document_id": identity,
                    "revision": revision,
                    "created_at": at,
                    "changes": json.loads(changes),
                }
                for seq, identity, revision, at, changes in connection.execute(
                    "SELECT sequence,document_id,revision,created_at,changes FROM events WHERE sequence>? ORDER BY sequence LIMIT ?",
                    (sequence, limit),
                )
            ]

    def sequence(self):
        with self.connect() as connection:
            return connection.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]

    def delete(self, document_id):
        with self.connect(write=True) as connection:
            connection.execute("DELETE FROM sessions WHERE document_id=?", (document_id,))
            connection.execute("DELETE FROM documents WHERE id=?", (document_id,))
            connection.execute(
                "INSERT INTO events(document_id,revision,created_at,changes) VALUES (?,0,?,?)",
                (
                    document_id,
                    time.time(),
                    encode([{"kind": "document", "key": document_id, "deleted": True}]),
                ),
            )

    def backup(self, destination):
        with (
            closing(sqlite3.connect(self.path)) as source,
            closing(sqlite3.connect(destination)) as target,
        ):
            source.backup(target)

    def sessions(self):
        with self.connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute("SELECT payload FROM sessions ORDER BY token")
            ]

    def save_session(self, session):
        with self.connect(write=True) as connection:
            connection.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET state=excluded.state,expires_at=excluded.expires_at,payload=excluded.payload",
                (
                    session["session_token"],
                    session["document_id"],
                    session["stage"],
                    session["state"],
                    session["expires_at"],
                    encode(session),
                ),
            )

    def record_submission_timing(self, token, seconds):
        """Telemetry updates the current session, never a stale business snapshot."""
        with self.connect(write=True) as connection:
            row = connection.execute(
                "SELECT payload FROM sessions WHERE token=?", (token,)
            ).fetchone()
            if row is not None:
                session = json.loads(row[0])
                session.setdefault("submission_timings", []).append(seconds)
                connection.execute(
                    "UPDATE sessions SET payload=? WHERE token=?", (encode(session), token)
                )

    def capacity_used(self, excluding_session=None, *, now=None):
        # Session/task handoff commits together; sample both from that same revision.
        now = time.time() if now is None else now
        with self.connect() as connection:
            sessions = [
                session
                for session in self.sessions()
                if session["state"] in {"starting", "idle", "busy", "cooldown"}
                and session["expires_at"] > now
            ]
            managed = {(session["document_id"], session.get("task_id")) for session in sessions}
            leases = list(
                connection.execute(
                    "SELECT document_id,key FROM records WHERE kind='task' AND json_extract(value,'$.value.state')='leased' AND json_extract(value,'$.value.claim.expires_at')>?",
                    (now,),
                )
            )
        return sum(session["session_token"] != excluding_session for session in sessions) + sum(
            (identity, task) not in managed for identity, task in leases
        )

    def model_capacity_used(self, model, excluding_session=None, *, now=None):
        """Count model reservations and unmanaged leases from one ledger snapshot."""
        from hrs_codex.runtime_control import model_for_stage

        now = time.time() if now is None else now
        with self.connect() as connection:
            sessions = [
                session
                for session in self.sessions()
                if session["state"] in {"starting", "idle", "busy", "cooldown"}
                and session["expires_at"] > now
            ]
            managed = {(session["document_id"], session.get("task_id")) for session in sessions}
            leased = [
                (identity, task_id, json.loads(value)["value"])
                for identity, task_id, value in connection.execute(
                    "SELECT document_id,key,value FROM records WHERE kind='task' AND json_extract(value,'$.value.state')='leased' AND json_extract(value,'$.value.claim.expires_at')>?",
                    (now,),
                )
            ]
            return sum(
                session["session_token"] != excluding_session
                and session.get("actual_model", model_for_stage(session["stage"])) == model
                for session in sessions
            ) + sum(
                (identity, task_id) not in managed
                and task["claim"].get("actual_model", model_for_stage(task["stage"])) == model
                for identity, task_id, task in leased
            )
