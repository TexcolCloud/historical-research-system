"""Workspace-local current records and append-only study revision history."""

import json
import time
from uuid import uuid4

from hrs.library.errors import ServiceError
from hrs.processing.ledger import encode
from hrs.processing.workspace import identity


class StudyStore:
    """Share the native ledger transaction; never write source or approval tables."""

    def __init__(self, native_store):
        self.native_store = native_store
        self.ledger = native_store.checkpoints.ledger
        if self.ledger is None:
            raise ServiceError(
                409, "旧工作区保持只读；请停止执行器并显式运行 migrate-ledger 后使用专题功能。"
            )
        self.workspace_id = identity(native_store)["workspace_id"]

    @staticmethod
    def exists(connection):
        return bool(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='study_items'"
            ).fetchone()
        )

    @staticmethod
    def _schema(connection):
        for statement in (
            "CREATE TABLE IF NOT EXISTS study_items (workspace_id TEXT NOT NULL,id TEXT NOT NULL,"
            "kind TEXT NOT NULL,topic_id TEXT,version INTEGER NOT NULL,archived INTEGER NOT NULL,"
            "updated_at REAL NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(workspace_id,id))",
            "CREATE INDEX IF NOT EXISTS study_scope ON study_items(workspace_id,kind,topic_id,archived,updated_at)",
            "CREATE TABLE IF NOT EXISTS study_revisions (workspace_id TEXT NOT NULL,id TEXT NOT NULL,"
            "kind TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL,"
            "PRIMARY KEY(workspace_id,id,version))",
        ):
            connection.execute(statement)

    def _read(self, connection, kind, item_id):
        row = (
            connection.execute(
                "SELECT payload FROM study_items WHERE workspace_id=? AND id=? AND kind=?",
                (self.workspace_id, item_id, kind),
            ).fetchone()
            if self.exists(connection)
            else None
        )
        if row is None:
            raise ServiceError(404, "研究记录不存在或不属于当前工作区。")
        return json.loads(row[0])

    def read(self, kind, item_id):
        with self.ledger.connect() as connection:
            return self._read(connection, kind, item_id)

    def transaction(self):
        """Compose related study operations atomically through the native transaction."""
        self.native_store.checkpoints.require_writable()
        return self.ledger.connect(write=True)

    @staticmethod
    def _encoded(value):
        content = encode(value)
        if len(content.encode("utf8")) > 8_000_000:
            raise ServiceError(400, "研究记录过大，请拆分画布或缩短内容。")
        return content

    def create(self, kind, payload, *, topic_id=None, item_id=None):
        self.native_store.checkpoints.require_writable()
        now = time.time()
        value = {
            **payload,
            "id": item_id or uuid4().hex,
            "type": kind,
            "topic_id": topic_id,
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "archived": bool(payload.get("archived", False)),
        }
        content = self._encoded(value)
        with self.ledger.connect(write=True) as connection:
            self._schema(connection)
            if topic_id is not None:
                self._read(connection, "topic", topic_id)
            previous = connection.execute(
                "SELECT kind,payload FROM study_items WHERE workspace_id=? AND id=?",
                (self.workspace_id, value["id"]),
            ).fetchone()
            if previous:
                retained = json.loads(previous[1])
                if (
                    previous[0] == kind
                    and retained.get("topic_id") == topic_id
                    and all(retained.get(key) == item for key, item in payload.items())
                ):
                    return retained
                raise ServiceError(409, "记录标识已经存在，请重新读取。")
            connection.execute(
                "INSERT INTO study_items VALUES (?,?,?,?,?,?,?,?)",
                (
                    self.workspace_id,
                    value["id"],
                    kind,
                    topic_id,
                    1,
                    int(value["archived"]),
                    now,
                    content,
                ),
            )
            connection.execute(
                "INSERT INTO study_revisions VALUES (?,?,?,?,?)",
                (
                    self.workspace_id,
                    value["id"],
                    kind,
                    1,
                    content,
                ),
            )
        return value

    def save(self, kind, item_id, changes, *, expected_version):
        if type(expected_version) is not int or expected_version < 1:
            raise ServiceError(400, "请提供有效的记录版本。")
        if set(changes) & {"id", "type", "topic_id", "version", "created_at", "updated_at"}:
            raise ServiceError(400, "记录身份和版本不能作为正文修改。")
        self.native_store.checkpoints.require_writable()
        with self.ledger.connect(write=True) as connection:
            previous = self._read(connection, kind, item_id)
            if expected_version <= previous["version"] and all(
                previous.get(key) == value for key, value in changes.items()
            ):
                return previous
            if previous["version"] != expected_version:
                raise ServiceError(409, "记录已有新版本，当前输入可以保留，请重新读取后保存。")
            value = {
                **previous,
                **changes,
                "version": expected_version + 1,
                "updated_at": time.time(),
            }
            content = self._encoded(value)
            connection.execute(
                "UPDATE study_items SET version=?,archived=?,updated_at=?,payload=? "
                "WHERE workspace_id=? AND id=? AND version=?",
                (
                    value["version"],
                    int(value.get("archived", False)),
                    value["updated_at"],
                    content,
                    self.workspace_id,
                    item_id,
                    expected_version,
                ),
            )
            connection.execute(
                "INSERT INTO study_revisions VALUES (?,?,?,?,?)",
                (
                    self.workspace_id,
                    item_id,
                    kind,
                    value["version"],
                    content,
                ),
            )
        return value

    @staticmethod
    def _projection(kind, summary):
        if summary and kind == "topic":
            return "json_set(json_remove(payload,'$.materials'),'$.material_count',json_array_length(payload,'$.materials'))"
        if summary and kind == "canvas":
            return "json_set(json_remove(payload,'$.document'),'$.node_count',json_array_length(payload,'$.document.nodes'),'$.edge_count',json_array_length(payload,'$.document.edges'))"
        return "payload"

    def list(self, kind, *, topic_id=None, archived=False, offset=0, limit=50, summary=False):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ServiceError(400, "列表范围无效，每页支持1–200条记录。")
        where = "workspace_id=? AND kind=?"
        args = [self.workspace_id, kind]
        if topic_id is not None:
            where += " AND topic_id=?"
            args.append(topic_id)
        if archived is not None:
            where += " AND archived=?"
            args.append(int(archived))
        with self.ledger.connect() as connection:
            if not self.exists(connection):
                return {"items": [], "total": 0, "offset": offset, "limit": limit}
            total = connection.execute(
                "SELECT COUNT(*) FROM study_items WHERE " + where, args
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT "
                + self._projection(kind, summary)
                + " FROM study_items WHERE "
                + where
                + " ORDER BY updated_at DESC,id LIMIT ? OFFSET ?",
                [*args, limit, offset],
            )
            items = [json.loads(row[0]) for row in rows]
        return {"items": items, "total": total, "offset": offset, "limit": limit}

    def history(self, kind, item_id, *, offset=0, limit=50, summary=False):
        self.read(kind, item_id)
        if offset < 0 or not 1 <= limit <= 200:
            raise ServiceError(400, "版本列表范围无效。")
        with self.ledger.connect() as connection:
            rows = connection.execute(
                "SELECT "
                + self._projection(kind, summary)
                + " FROM study_revisions WHERE workspace_id=? AND id=? AND kind=? "
                "ORDER BY version DESC LIMIT ? OFFSET ?",
                (self.workspace_id, item_id, kind, limit, offset),
            )
            return [json.loads(row[0]) for row in rows]
