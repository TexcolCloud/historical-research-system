"""Stable research identities and evidence-bearing edges in the workflow ledger.

Source mentions remain immutable, version-bound records. A catalogue identity is
an independently versioned grouping, never a global merge on a name string.
"""

import json
import time
import uuid
from contextlib import nullcontext

from hrs.processing.errors import EvidenceMismatch, LeaseConflict
from hrs.processing.hashing import digest
from hrs.processing.ledger import encode

APPROVED = {"verified", "human_accepted"}


class ResearchCatalogue:
    def __init__(self, ledger):
        self.ledger = ledger

    @staticmethod
    def exists(connection):
        return bool(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='research_catalogue_entities'"
            ).fetchone()
        )

    @staticmethod
    def ensure(connection):
        for statement in (
            "CREATE TABLE IF NOT EXISTS research_catalogue_entities "
            "(id TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, "
            "redirect TEXT, payload TEXT NOT NULL, PRIMARY KEY(id,version))",
            "CREATE TABLE IF NOT EXISTS research_catalogue_members "
            "(id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, source_key TEXT NOT NULL, card_id TEXT NOT NULL, "
            "card_version INTEGER NOT NULL, book_id TEXT NOT NULL, active INTEGER NOT NULL, payload TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS research_catalogue_member_entity "
            "ON research_catalogue_members(entity_id,active)",
            "CREATE INDEX IF NOT EXISTS research_catalogue_member_source "
            "ON research_catalogue_members(source_key,active)",
            "CREATE INDEX IF NOT EXISTS research_catalogue_member_card "
            "ON research_catalogue_members(card_id,card_version)",
            "CREATE INDEX IF NOT EXISTS research_catalogue_member_names "
            "ON research_catalogue_members(book_id,active,json_extract(payload,'$.name'),json_extract(payload,'$.kind'))",
            "CREATE INDEX IF NOT EXISTS research_catalogue_member_match "
            "ON research_catalogue_members(json_extract(payload,'$.name'),json_extract(payload,'$.kind'),active)",
            "CREATE TABLE IF NOT EXISTS research_catalogue_edges "
            "(id TEXT NOT NULL, card_version INTEGER NOT NULL, source_id TEXT NOT NULL, target_id TEXT NOT NULL, "
            "card_id TEXT NOT NULL, kind TEXT NOT NULL, active INTEGER NOT NULL, payload TEXT NOT NULL, "
            "PRIMARY KEY(id,card_version))",
            "CREATE INDEX IF NOT EXISTS research_catalogue_edge_source "
            "ON research_catalogue_edges(source_id,active)",
            "CREATE INDEX IF NOT EXISTS research_catalogue_edge_target "
            "ON research_catalogue_edges(target_id,active)",
            "CREATE TABLE IF NOT EXISTS research_catalogue_decisions "
            "(id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, target_id TEXT, action TEXT NOT NULL, "
            "created_at REAL NOT NULL, payload TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS research_catalogue_recorded "
            "(card_id TEXT NOT NULL,version INTEGER NOT NULL,PRIMARY KEY(card_id,version))",
            "CREATE TABLE IF NOT EXISTS research_catalogue_documents "
            "(card_id TEXT NOT NULL,card_version INTEGER NOT NULL,article_id TEXT NOT NULL,text_sha256 TEXT NOT NULL,"
            "book_id TEXT NOT NULL,source_key TEXT NOT NULL,active INTEGER NOT NULL,payload TEXT NOT NULL,"
            "PRIMARY KEY(card_id,card_version))",
            "CREATE INDEX IF NOT EXISTS research_catalogue_document_source "
            "ON research_catalogue_documents(article_id,text_sha256,active)",
            "CREATE TABLE IF NOT EXISTS research_catalogue_locations "
            "(article_id TEXT NOT NULL,text_sha256 TEXT NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,"
            "mention_id TEXT NOT NULL,card_id TEXT NOT NULL,card_version INTEGER NOT NULL,payload TEXT NOT NULL,"
            "PRIMARY KEY(mention_id,card_version,article_id,start,end))",
            "CREATE INDEX IF NOT EXISTS research_catalogue_location_source "
            "ON research_catalogue_locations(article_id,text_sha256)",
        ):
            connection.execute(statement)

    @staticmethod
    def _latest(connection, identity):
        row = connection.execute(
            "SELECT payload FROM research_catalogue_entities WHERE id=? ORDER BY version DESC LIMIT 1",
            (identity,),
        ).fetchone()
        if not row:
            raise FileNotFoundError(identity)
        return json.loads(row[0])

    @staticmethod
    def _resolve(connection, identity):
        member = connection.execute(
            "SELECT entity_id FROM research_catalogue_members WHERE id=?", (identity,)
        ).fetchone()
        identity = member[0] if member else identity
        seen = set()
        while identity not in seen:
            seen.add(identity)
            entity = ResearchCatalogue._latest(connection, identity)
            if not entity.get("redirect"):
                return entity
            identity = entity["redirect"]
        raise EvidenceMismatch("Catalogue identity redirects contain a cycle")

    @staticmethod
    def _save(connection, entity):
        connection.execute(
            "INSERT INTO research_catalogue_entities VALUES (?,?,?,?,?,?)",
            (
                entity["id"],
                entity["version"],
                entity["kind"],
                entity["name"],
                entity.get("redirect"),
                encode(entity),
            ),
        )

    @staticmethod
    def _article_key(card):
        return card.get("article_key") or digest(
            [card["book_id"], card.get("document_id", card["book_id"]), card["article_id"]]
        )

    def record(self, card, *, connection=None):
        """Commit every projection in the caller's transaction, or own one transaction."""
        with (
            nullcontext(connection) if connection is not None else self.ledger.connect(write=True)
        ) as conn:
            self.ensure(conn)
            if conn.execute(
                "SELECT 1 FROM research_catalogue_recorded WHERE card_id=? AND version=?",
                (card["id"], card["version"]),
            ).fetchone():
                return
            conn.execute(
                "INSERT INTO research_catalogue_recorded VALUES (?,?)",
                (card["id"], card["version"]),
            )
            conn.execute(
                "UPDATE research_catalogue_members SET active=0 WHERE card_id=?", (card["id"],)
            )
            conn.execute(
                "UPDATE research_catalogue_edges SET active=0 WHERE card_id=?", (card["id"],)
            )
            conn.execute(
                "UPDATE research_catalogue_documents SET active=0 WHERE card_id=?", (card["id"],)
            )
            if card["state"] not in APPROVED:
                return
            article_key = self._article_key(card)
            derived = card.get("purpose") == "question" or (
                card.get("purpose") == "link_refresh" and not card.get("replaces_card_id")
            )
            if not derived:
                if not self._record_document(conn, card, article_key):
                    return
            evidence = {item["id"]: item for item in card["evidence"]}
            local = self._record_mentions(conn, card, article_key, derived, evidence)
            self._record_relations(conn, card, local, evidence)
            self._record_participations(conn, card, local, evidence)
            self._record_identity_links(conn, card, local)
            self._record_cross_relations(conn, card, evidence)

    def _mention_identity(self, conn, card, mention, article_key, derived):
        source_key = digest(
            [article_key, mention["kind"], mention.get("identity_key") or mention["name"]]
        )
        earlier = conn.execute(
            "SELECT entity_id FROM research_catalogue_members WHERE source_key=? "
            "ORDER BY active DESC, rowid DESC LIMIT 1",
            (source_key,),
        ).fetchone()
        if derived and earlier is None:
            candidates = conn.execute(
                "SELECT DISTINCT m.entity_id FROM research_catalogue_members m "
                "JOIN research_catalogue_documents d ON d.card_id=m.card_id AND d.active=1 "
                "WHERE m.active=1 AND d.source_key=? AND json_extract(m.payload,'$.name')=? "
                "AND json_extract(m.payload,'$.kind')=? LIMIT 2",
                (article_key, mention["name"], mention["kind"]),
            ).fetchall()
            earlier = candidates[0] if len(candidates) == 1 else None
        identity = earlier[0] if earlier else "entity-" + source_key[:32]
        if derived and earlier is None:
            return source_key, None
        if earlier:
            identity = self._resolve(conn, identity)["id"]
        else:
            self._save(
                conn,
                {
                    "id": identity,
                    "canonical_id": identity,
                    "version": 1,
                    "kind": mention["kind"],
                    "name": mention["name"],
                    "aliases": [],
                    "redirect": None,
                    "created_at": time.time(),
                },
            )
        return source_key, identity

    def _record_mentions(self, conn, card, article_key, derived, evidence):
        local = {}
        for mention in [
            *card["mentions"],
            *[
                {**event, "kind": "event", "identity_basis": event["description"]}
                for event in card["events"]
            ],
        ]:
            source_key, identity = self._mention_identity(conn, card, mention, article_key, derived)
            if identity is None:
                continue
            local[mention["id"]] = identity
            if derived:
                continue
            member = self._record_member(conn, card, mention, source_key, identity, evidence)
            self._record_aliases(conn, card, mention, identity)
            self._record_locations(conn, card, mention, member)
            self._edge(
                conn,
                card,
                "mentioned-" + mention["id"],
                identity,
                card["id"],
                "mentioned_in",
                {
                    "reason": "原文中的来源提及",
                    "evidence_ids": mention["evidence_ids"],
                    "source_member_id": member["id"],
                },
                evidence,
            )
        return local

    def _record_member(self, conn, card, mention, source_key, identity, evidence):
        member_id = digest([card["id"], mention["id"]])[:32]
        refs = self._evidence_refs(card, mention["evidence_ids"])
        member = {
            **mention,
            "id": member_id,
            "canonical_id": identity,
            "local_id": mention["id"],
            "card_id": card["id"],
            "card_version": card["version"],
            "book_id": card["book_id"],
            "run_id": card["run_id"],
            "article_id": card["article_id"],
            "publication_sha256": card["publication_sha256"],
            "state": card["state"],
            "approval_type": card["approval_type"],
            "card_title": card["title"],
            "evidence_refs": refs,
            "evidence": [evidence[eid] for eid in mention["evidence_ids"]],
            "identity_scope": "source_mention",
        }
        conn.execute(
            "UPDATE research_catalogue_members SET active=0 WHERE source_key=?",
            (source_key,),
        )
        conn.execute(
            "INSERT INTO research_catalogue_members VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET entity_id=excluded.entity_id, "
            "card_version=excluded.card_version,active=excluded.active,payload=excluded.payload",
            (
                member_id,
                identity,
                source_key,
                card["id"],
                card["version"],
                card["book_id"],
                1,
                encode(member),
            ),
        )
        return member

    def _record_aliases(self, conn, card, mention, identity):
        entity = self._latest(conn, identity)
        new_aliases = [
            {
                "name": mention["name"],
                "evidence_refs": self._evidence_refs(card, mention["evidence_ids"]),
            }
        ]
        new_aliases += [
            {
                "name": alias["name"],
                "evidence_refs": self._evidence_refs(card, alias["evidence_ids"]),
            }
            for alias in mention.get("aliases", [])
        ]
        for alias in new_aliases:
            last_decision = conn.execute(
                "SELECT action FROM research_catalogue_decisions WHERE entity_id=? "
                "AND action IN ('alias','alias_remove') AND json_extract(payload,'$.alias')=? "
                "ORDER BY created_at DESC LIMIT 1",
                (identity, alias["name"]),
            ).fetchone()
            if last_decision and last_decision[0] == "alias_remove":
                continue
            existing_alias = next(
                (item for item in entity["aliases"] if item["name"] == alias["name"]), None
            )
            if existing_alias is None:
                entity["aliases"].append(alias)
            else:
                existing_alias["evidence_refs"].extend(
                    ref
                    for ref in alias["evidence_refs"]
                    if ref not in existing_alias["evidence_refs"]
                )
        entity["version"] += 1
        self._save(conn, entity)

    def _record_relations(self, conn, card, local, evidence):
        for relation in card["relations"]:
            if relation["subject_id"] in local and relation["object_id"] in local:
                self._edge(
                    conn,
                    card,
                    relation["id"],
                    local[relation["subject_id"]],
                    local[relation["object_id"]],
                    relation["predicate"],
                    relation,
                    evidence,
                )

    def _record_participations(self, conn, card, local, evidence):
        for event in card["events"]:
            if event["id"] not in local:
                continue
            roles = {item["mention_id"]: item for item in event.get("participant_roles", [])}
            for participant in event["participant_ids"]:
                if participant not in local:
                    continue
                role = roles.get(participant, {})
                self._edge(
                    conn,
                    card,
                    f"participant-{event['id']}-{participant}",
                    local[event["id"]],
                    local[participant],
                    role.get("role", "participant"),
                    {
                        "reason": role.get("description", event["description"]),
                        "evidence_ids": role.get("evidence_ids", event["evidence_ids"]),
                        "event_id": local[event["id"]],
                        "event_name": event["name"],
                        "event_status": event.get("event_status", "unknown"),
                        "status_note": event.get("status_note", ""),
                        "date": event["date"],
                        "action": event.get("action", ""),
                        "place": event.get("place", {}),
                        "role": role.get("role", "participant"),
                        "source_member_id": digest([card["id"], event["id"]])[:32],
                        "target_member_id": digest([card["id"], participant])[:32],
                    },
                    evidence,
                )

    def _record_identity_links(self, conn, card, local):
        for link in card.get("identity_links", []):
            if link["local_mention_id"] not in local:
                continue
            try:
                target_entity = self._resolve(conn, link["entity_id"])
            except FileNotFoundError:
                continue
            source, target_id = (
                local[link["local_mention_id"]],
                target_entity["id"],
            )
            if link["relation"] == "same_as" and source != target_id:
                denied = conn.execute(
                    "SELECT 1 FROM research_catalogue_decisions WHERE action IN ('distinct','split') AND "
                    "((entity_id=? AND target_id=?) OR (entity_id=? AND target_id=?))",
                    (source, target_id, target_id, source),
                ).fetchone()
                if not denied:
                    self._merge(
                        conn,
                        source,
                        target_id,
                        provenance="native",
                        refs=self._evidence_refs(card, link["evidence_ids"]),
                        reason=link["reason"],
                    )

    def _record_cross_relations(self, conn, card, evidence):
        for relation in card.get("cross_relations", []):
            edge = {
                **relation,
                "id": digest([card["id"], relation["id"]])[:32],
                "source_id": card.get("linked_card_id") or card["id"],
                "target_id": relation["target_card_id"],
                "card_id": card["id"],
                "card_version": card["version"],
                "book_id": card["book_id"],
                "run_id": card["run_id"],
                "approval_type": card["approval_type"],
                "usage": "research_lead",
                "evidence": [
                    evidence[eid]
                    for eid in [
                        *relation["source_evidence_ids"],
                        *relation["target_evidence_ids"],
                    ]
                ],
            }
            conn.execute(
                "INSERT OR REPLACE INTO research_catalogue_edges VALUES (?,?,?,?,?,?,?,?)",
                (
                    edge["id"],
                    card["version"],
                    edge["source_id"],
                    relation["target_card_id"],
                    card["id"],
                    relation["kind"],
                    1,
                    encode(edge),
                ),
            )

    @staticmethod
    def _evidence_refs(card, evidence_ids):
        return [
            {"card_id": card["id"], "card_version": card["version"], "evidence_id": eid}
            for eid in evidence_ids
        ]

    @staticmethod
    def _record_document(connection, card, source_key):
        rows = connection.execute(
            "SELECT card_id,payload FROM research_catalogue_documents WHERE source_key=? AND active=1",
            (source_key,),
        ).fetchall()
        created = card.get("research_created_at", card.get("created_at", 0))
        if any(json.loads(payload).get("research_created_at", 0) > created for _, payload in rows):
            return False
        for old_card, _ in rows:
            connection.execute(
                "UPDATE research_catalogue_members SET active=0 WHERE card_id=?", (old_card,)
            )
            connection.execute(
                "UPDATE research_catalogue_edges SET active=0 WHERE card_id=?", (old_card,)
            )
        connection.execute(
            "UPDATE research_catalogue_documents SET active=0 WHERE source_key=? OR card_id=?",
            (source_key, card["id"]),
        )
        summary = {
            key: card.get(key)
            for key in (
                "id",
                "version",
                "book_id",
                "run_id",
                "article_id",
                "title",
                "summary",
                "state",
                "approval_type",
                "publication_sha256",
            )
        }
        summary["research_created_at"] = created
        connection.execute(
            "INSERT OR REPLACE INTO research_catalogue_documents VALUES (?,?,?,?,?,?,?,?)",
            (
                card["id"],
                card["version"],
                card["article_id"],
                card["article_sha256"],
                card["book_id"],
                source_key,
                1,
                encode(summary),
            ),
        )
        return True

    @staticmethod
    def _record_locations(connection, card, mention, member):
        locations = list(mention.get("locations", []))
        if not locations and mention["kind"] != "event":
            for citation in member["evidence"]:
                if citation["quote"].count(mention["name"]) == 1:
                    start = citation["start"] + citation["quote"].index(mention["name"])
                    locations.append(
                        {
                            "article_id": citation["article_id"],
                            "start": start,
                            "end": start + len(mention["name"]),
                            "quote": mention["name"],
                        }
                    )
        for location in locations:
            citation = next(
                (
                    item
                    for item in member["evidence"]
                    if item["article_id"] == location["article_id"]
                    and item["start"] <= location["start"] < location["end"] <= item["end"]
                ),
                None,
            )
            if citation is None:
                continue
            annotation = {
                "id": digest([member["id"], location])[:32],
                "entity_id": member["canonical_id"],
                "canonical_id": member["canonical_id"],
                "kind": mention["kind"],
                "name": mention["name"],
                "start": location["start"],
                "end": location["end"],
                "quote": location["quote"],
                "card_id": card["id"],
                "card_version": card["version"],
                "evidence_ids": mention["evidence_ids"],
                "evidence_refs": member["evidence_refs"],
                "book_id": card["book_id"],
            }
            connection.execute(
                "INSERT OR REPLACE INTO research_catalogue_locations VALUES (?,?,?,?,?,?,?,?)",
                (
                    citation.get("original_article_id", location["article_id"]),
                    citation.get("article_sha256", card["article_sha256"]),
                    location["start"],
                    location["end"],
                    member["id"],
                    card["id"],
                    card["version"],
                    encode(annotation),
                ),
            )

    @staticmethod
    def _edge(connection, card, key, source, target, kind, relation, evidence):
        edge = {
            **relation,
            "id": digest([card["id"], key])[:32],
            "source_id": source,
            "target_id": target,
            "kind": kind,
            "predicate": kind,
            "card_id": card["id"],
            "card_version": card["version"],
            "book_id": card["book_id"],
            "run_id": card["run_id"],
            "approval_type": card["approval_type"],
            "usage": "research_lead",
            "evidence": [evidence[eid] for eid in relation["evidence_ids"]],
            "evidence_refs": ResearchCatalogue._evidence_refs(card, relation["evidence_ids"]),
        }
        if relation.get("subject_id"):
            edge["source_member_id"] = digest([card["id"], relation["subject_id"]])[:32]
            edge["target_member_id"] = digest([card["id"], relation["object_id"]])[:32]
        connection.execute(
            "INSERT OR REPLACE INTO research_catalogue_edges VALUES (?,?,?,?,?,?,?,?)",
            (edge["id"], card["version"], source, target, card["id"], kind, 1, encode(edge)),
        )

    def entities(self, *, book_id=None, book_ids=None, query=None, kind=None, offset=0, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError("Catalogue pagination requires offset>=0 and limit 1–100")
        with self.ledger.connect() as connection:
            if not self.exists(connection):
                return {"items": [], "total": 0, "offset": offset, "limit": limit}
            selected = list(
                dict.fromkeys(
                    [
                        *([str(book_id)] if book_id else []),
                        *(str(value) for value in (book_ids or [])),
                    ]
                )
            )
            where = [
                "e.version=(SELECT MAX(v.version) FROM research_catalogue_entities v WHERE v.id=e.id)",
                "e.redirect IS NULL",
                "EXISTS(SELECT 1 FROM research_catalogue_members m "
                "WHERE m.entity_id=e.id AND m.active=1"
                + (
                    " AND m.book_id IN (" + ",".join("?" for _ in selected) + ")"
                    if selected
                    else ""
                )
                + ")",
            ]
            args = selected
            if query:
                where.append(
                    "(instr(lower(e.name),lower(?))>0 OR instr(lower(e.payload),lower(?))>0)"
                )
                args.extend([query, query])
            if kind:
                where.append("e.kind=?")
                args.append(kind)
            sql = " FROM research_catalogue_entities e WHERE " + " AND ".join(where)
            total = connection.execute("SELECT COUNT(*)" + sql, args).fetchone()[0]
            ids = connection.execute(
                "SELECT e.id" + sql + " ORDER BY e.name,e.id LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
            items = [self._detail(connection, row[0]) for row in ids]
            return {"items": items, "total": total, "offset": offset, "limit": limit}

    def _detail(self, connection, identity):
        entity = self._resolve(connection, identity)
        members = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT payload FROM research_catalogue_members WHERE entity_id=? AND active=1 ORDER BY id LIMIT 201",
                (entity["id"],),
            )
        ]
        edges = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT payload FROM research_catalogue_edges WHERE active=1 AND (source_id=? OR target_id=?) "
                "ORDER BY id LIMIT 201",
                (entity["id"], entity["id"]),
            )
        ]
        decisions = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT payload FROM research_catalogue_decisions WHERE entity_id=? OR target_id=? "
                "ORDER BY created_at DESC LIMIT 100",
                (entity["id"], entity["id"]),
            )
        ]
        representative = members[0] if members else {}
        for member in members:
            member["canonical_id"] = entity["id"]
        live_refs = {
            (member["card_id"], member["card_version"], eid)
            for member in members
            for eid in member["evidence_ids"]
        }
        active_aliases = []
        historical_aliases = []
        for alias in entity["aliases"]:
            current = any(
                (ref["card_id"], ref["card_version"], ref["evidence_id"]) in live_refs
                for ref in alias["evidence_refs"]
            )
            live = [
                ref
                for ref in alias["evidence_refs"]
                if (ref["card_id"], ref["card_version"], ref["evidence_id"]) in live_refs
            ]
            (active_aliases if current else historical_aliases).append(
                {
                    **alias,
                    "evidence_refs": live if current else alias["evidence_refs"],
                    "historical_evidence_refs": [
                        ref for ref in alias["evidence_refs"] if ref not in live
                    ],
                    "source_current": current,
                }
            )
        entity["aliases"] = active_aliases
        entity["historical_aliases"] = historical_aliases
        return {
            **representative,
            **entity,
            "canonical_id": entity["id"],
            "identity_scope": "canonical",
            "mentions": members[:200],
            "source_mentions": members[:200],
            "evidence": [e for member in members[:200] for e in member["evidence"]],
            "participations": [edge for edge in edges[:200] if edge.get("event_id")],
            "relationships": edges[:200],
            "identity_decisions": decisions,
            "identity_links": [],
            "identity_truncated": len(members) > 200 or len(edges) > 200,
        }

    def entity(self, identity):
        with self.ledger.connect() as connection:
            if not self.exists(connection):
                raise FileNotFoundError(identity)
            return self._detail(connection, identity)

    def relation(self, identity):
        with self.ledger.connect() as connection:
            if not self.exists(connection):
                raise FileNotFoundError(identity)
            row = connection.execute(
                "SELECT payload FROM research_catalogue_edges WHERE id=? AND active=1 "
                "ORDER BY card_version DESC LIMIT 1",
                (identity,),
            ).fetchone()
            if row is None:
                raise FileNotFoundError(identity)
            return json.loads(row[0])

    def annotate_mentions(self, mentions):
        with self.ledger.connect() as connection:
            if not self.exists(connection):
                return mentions
            for mention in mentions:
                try:
                    entity = self._resolve(connection, mention["id"])
                except FileNotFoundError:
                    continue
                mention.update(
                    canonical_id=entity["id"],
                    canonical_version=entity["version"],
                    aliases=entity["aliases"],
                )
            return mentions

    def reading(self, article_id, text_sha256, *, limit=200):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Reading annotation limit must be 1–500")
        with self.ledger.connect() as connection:
            if not self.exists(connection):
                return {
                    "article_id": article_id,
                    "text_sha256": text_sha256,
                    "annotations": [],
                    "cards": [],
                    "truncated": False,
                }
            rows = connection.execute(
                "SELECT l.payload,m.entity_id FROM research_catalogue_locations l "
                "JOIN research_catalogue_members m ON m.id=l.mention_id AND m.active=1 AND m.card_version=l.card_version "
                "WHERE l.article_id=? AND l.text_sha256=? ORDER BY l.start,l.end LIMIT ?",
                (article_id, text_sha256, limit + 1),
            ).fetchall()
            annotations = []
            for payload, identity in rows[:limit]:
                row = json.loads(payload)
                canonical = self._resolve(connection, identity)["id"]
                row.update(entity_id=canonical, canonical_id=canonical)
                annotations.append(row)
            cards = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT payload FROM research_catalogue_documents WHERE article_id=? AND text_sha256=? "
                    "AND active=1 ORDER BY card_id LIMIT 100",
                    (article_id, text_sha256),
                )
            ]
            return {
                "article_id": article_id,
                "text_sha256": text_sha256,
                "annotations": annotations,
                "cards": cards,
                "truncated": len(rows) > limit,
            }

    @staticmethod
    def _merge(connection, source, target, *, provenance, refs, reason):
        left, right = (
            ResearchCatalogue._resolve(connection, source),
            ResearchCatalogue._resolve(connection, target),
        )
        if left["id"] == right["id"]:
            return left["id"]
        if left["kind"] != right["kind"]:
            raise EvidenceMismatch("Different entity kinds cannot be merged")
        right.update(version=right["version"] + 1, redirect=left["id"])
        ResearchCatalogue._save(connection, right)
        left["aliases"] = [
            *left["aliases"],
            *[a for a in right["aliases"] if a not in left["aliases"]],
        ]
        left["version"] += 1
        ResearchCatalogue._save(connection, left)
        connection.execute(
            "UPDATE research_catalogue_members SET entity_id=? WHERE entity_id=?",
            (left["id"], right["id"]),
        )
        ResearchCatalogue._rebind_edges(connection, left["id"])
        decision = {
            "id": uuid.uuid4().hex,
            "entity_id": left["id"],
            "target_id": right["id"],
            "action": "merge",
            "approval_type": provenance,
            "reason": reason,
            "evidence_refs": refs,
            "at": time.time(),
        }
        connection.execute(
            "INSERT INTO research_catalogue_decisions VALUES (?,?,?,?,?,?)",
            (decision["id"], left["id"], right["id"], "merge", decision["at"], encode(decision)),
        )
        return left["id"]

    def decision(
        self,
        entity_id,
        action,
        expected_version,
        *,
        target_id=None,
        target_version=None,
        mention_ids=None,
        alias=None,
        evidence_refs=None,
        reason="",
    ):
        if action not in {"merge", "distinct", "split", "alias", "alias_remove"}:
            raise ValueError("Identity decisions require a supported action")
        reason = (reason.strip() if isinstance(reason, str) else "") or {
            "merge": "本机用户依据所选原文确认同一身份。",
            "distinct": "本机用户依据所选原文确认不同身份。",
            "split": "本机用户将选定的来源提及拆分为独立身份。",
            "alias": "本机用户依据所选原文维护别名。",
            "alias_remove": "本机用户撤销选定别名，保留历史回执。",
        }[action]
        with self.ledger.connect(write=True) as connection:
            self.ensure(connection)
            source = self._resolve(connection, entity_id)
            if source["version"] != expected_version:
                raise LeaseConflict("Identity changed; reload its current version")
            refs = evidence_refs or []
            members = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT payload FROM research_catalogue_members WHERE entity_id=? AND active=1",
                    (source["id"],),
                )
            ]
            target = None
            if action in {"merge", "distinct"}:
                target = self._resolve(connection, target_id)
                if target["version"] != target_version:
                    raise LeaseConflict("Target identity changed; reload both current versions")
                if target["id"] == source["id"]:
                    raise ValueError("Choose a different identity")
                target_members = [
                    json.loads(row[0])
                    for row in connection.execute(
                        "SELECT payload FROM research_catalogue_members WHERE entity_id=? AND active=1",
                        (target["id"],),
                    )
                ]
                for group in (members, target_members):
                    valid = {
                        (m["card_id"], m["card_version"], eid)
                        for m in group
                        for eid in m["evidence_ids"]
                    }
                    if not any(
                        (r.get("card_id"), r.get("card_version"), r.get("evidence_id")) in valid
                        for r in refs
                    ):
                        raise EvidenceMismatch(
                            "Identity decision requires original evidence from both identities"
                        )
            elif not refs or not all(
                any(
                    r.get("card_id") == m["card_id"]
                    and r.get("card_version") == m["card_version"]
                    and r.get("evidence_id") in m["evidence_ids"]
                    for m in members
                )
                for r in refs
            ):
                raise EvidenceMismatch("Identity decision requires current retained evidence")
            if action == "merge":
                self._merge(
                    connection,
                    source["id"],
                    target["id"],
                    provenance="user",
                    refs=refs,
                    reason=reason,
                )
            else:
                decision = {
                    "id": uuid.uuid4().hex,
                    "entity_id": source["id"],
                    "target_id": target["id"] if target else None,
                    "action": action,
                    "approval_type": "user",
                    "reason": reason,
                    "evidence_refs": refs,
                    "mention_ids": mention_ids or [],
                    "alias": alias,
                    "at": time.time(),
                }
                if action in {"alias", "alias_remove"}:
                    if not isinstance(alias, str) or not alias.strip():
                        raise ValueError("Alias cannot be empty")
                    if action == "alias":
                        source["aliases"].append(
                            {"name": alias.strip(), "evidence_refs": refs, "approval_type": "user"}
                        )
                    else:
                        removed = [
                            item for item in source["aliases"] if item["name"] == alias.strip()
                        ]
                        if not removed:
                            raise ValueError("The alias is not present on this identity")
                        source["aliases"] = [
                            item for item in source["aliases"] if item["name"] != alias.strip()
                        ]
                        if source["name"] == alias.strip():
                            if not source["aliases"]:
                                raise ValueError(
                                    "Keep a source-backed primary name before removing its last alias"
                                )
                            source["name"] = source["aliases"][0]["name"]
                        source.setdefault("withdrawn_aliases", []).extend(removed)
                if action == "split":
                    selected = set(mention_ids or [])
                    if not selected or not selected < {member["id"] for member in members}:
                        raise ValueError(
                            "Split must select a nonempty proper subset of source mentions"
                        )
                    new = {
                        **source,
                        "id": "entity-" + uuid.uuid4().hex,
                        "version": 1,
                        "redirect": None,
                        "created_at": time.time(),
                    }
                    new["canonical_id"] = new["id"]
                    self._save(connection, new)
                    marks = ",".join("?" for _ in selected)
                    connection.execute(
                        f"UPDATE research_catalogue_members SET entity_id=? WHERE id IN ({marks})",
                        [new["id"], *sorted(selected)],
                    )
                    self._rebind_edges(connection, new["id"])
                    decision["target_id"] = new["id"]
                source["version"] += 1
                self._save(connection, source)
                connection.execute(
                    "INSERT INTO research_catalogue_decisions VALUES (?,?,?,?,?,?)",
                    (
                        decision["id"],
                        source["id"],
                        decision["target_id"],
                        action,
                        decision["at"],
                        encode(decision),
                    ),
                )
            return self._detail(connection, source["id"])

    @staticmethod
    def _rebind_edges(connection, entity_id):
        cards = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT card_id FROM research_catalogue_members WHERE entity_id=?",
                (entity_id,),
            )
        ]
        if not cards:
            return
        marks = ",".join("?" for _ in cards)
        rows = connection.execute(
            f"SELECT id,card_version,payload FROM research_catalogue_edges WHERE card_id IN ({marks})",
            cards,
        ).fetchall()
        for identity, version, payload in rows:
            edge = json.loads(payload)
            for side in ("source", "target"):
                if edge.get(side + "_member_id"):
                    member = connection.execute(
                        "SELECT entity_id FROM research_catalogue_members WHERE id=?",
                        (edge[side + "_member_id"],),
                    ).fetchone()
                    if member:
                        edge[side + "_id"] = ResearchCatalogue._resolve(connection, member[0])["id"]
            if edge.get("event_id"):
                edge["event_id"] = edge["source_id"]
            connection.execute(
                "UPDATE research_catalogue_edges SET source_id=?,target_id=?,payload=? WHERE id=? AND card_version=?",
                (edge["source_id"], edge["target_id"], encode(edge), identity, version),
            )

    def graph(self, identity, *, hops=1, limit=50):
        if (
            type(hops) is not int
            or hops not in {1, 2}
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise ValueError("Graph queries require 1–2 hops and a limit between 1 and 100")
        with self.ledger.connect() as connection:
            entity = self._resolve(connection, identity)
            reached, frontier, edges = {entity["id"]}, {entity["id"]}, {}
            truncated = False
            for _ in range(hops):
                if not frontier:
                    break
                marks = ",".join("?" for _ in frontier)
                rows = connection.execute(
                    "SELECT payload FROM research_catalogue_edges WHERE active=1 AND "
                    f"(source_id IN ({marks}) OR target_id IN ({marks})) ORDER BY id LIMIT ?",
                    [*sorted(frontier), *sorted(frontier), limit + 1],
                ).fetchall()
                following = set()
                for (payload,) in rows:
                    edge = json.loads(payload)
                    if edge["id"] in edges:
                        continue
                    if len(edges) >= limit:
                        truncated = True
                        continue
                    edges[edge["id"]] = edge
                    following.update({edge["source_id"], edge["target_id"]} - reached)
                    reached.update({edge["source_id"], edge["target_id"]})
                frontier = following
            nodes = []
            for node_id in sorted(
                reached, key=lambda identity: (identity != entity["id"], identity)
            ):
                try:
                    nodes.append(self._detail(connection, node_id))
                except FileNotFoundError:
                    row = connection.execute(
                        "SELECT payload FROM research_catalogue_documents WHERE card_id=? "
                        "ORDER BY card_version DESC LIMIT 1",
                        (node_id,),
                    ).fetchone()
                    summary = json.loads(row[0]) if row else {}
                    nodes.append(
                        {
                            **summary,
                            "id": node_id,
                            "kind": "card",
                            "name": summary.get("title", node_id),
                        }
                    )
            return {
                "entity_id": entity["id"],
                "hops": hops,
                "limit": limit,
                "truncated": truncated,
                "nodes": nodes,
                "edges": list(edges.values()),
                "usage": "evidence-linked research leads",
            }
