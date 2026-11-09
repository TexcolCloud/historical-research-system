"""Versioned personal card notes, separate from model content and approval."""

import json
import time

from hrs.processing.ledger import encode


class CardNotes:
    def __init__(self, store):
        self.store = store
        self.ledger = store.checkpoints.ledger
        if self.ledger is None:
            raise ValueError("Card notes require the native ledger")

    @staticmethod
    def _key(card):
        return card.get("lineage_id") or card["id"]

    @staticmethod
    def _read(connection, key):
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='card_note_revisions'"
        ).fetchone():
            row = connection.execute(
                "SELECT payload FROM card_note_revisions WHERE lineage_id=? "
                "ORDER BY version DESC LIMIT 1",
                (key,),
            ).fetchone()
            if row:
                return json.loads(row[0])
        return {"text": "", "version": 0, "updated_at": None}

    def read(self, card):
        with self.ledger.connect() as connection:
            return self._read(connection, self._key(card))

    def save(self, card, expected_version, text):
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("Invalid card note version")
        if not isinstance(text, str) or len(text) > 60000:
            raise ValueError("Card notes must contain at most 60000 characters")
        self.store.checkpoints.require_writable()
        with self.ledger.connect(write=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS card_note_revisions ("
                "lineage_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, "
                "PRIMARY KEY(lineage_id,version))"
            )
            key = self._key(card)
            previous = self._read(connection, key)
            if previous["version"] != expected_version:
                raise ValueError("个人备注已有新版本，请重新读取；当前输入可以保留。")
            if previous["text"] == text:
                return previous
            note = {
                "text": text,
                "version": expected_version + 1,
                "updated_at": time.time(),
                "card_id": card["id"],
                "card_version": card["version"],
                "lineage_id": key,
                "origin": "personal_note",
            }
            connection.execute(
                "INSERT INTO card_note_revisions VALUES (?,?,?)",
                (key, note["version"], encode(note)),
            )
            return note
