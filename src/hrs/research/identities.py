"""Indexed source mentions and confirmed identity links over immutable card revisions."""

import json

from hrs.processing.hashing import digest


def card_entities(card):
    if card["state"] == "excluded":
        return []
    mentions = [
        *card["mentions"],
        *[
            {**event, "kind": "event", "identity_basis": event["description"]}
            for event in card["events"]
        ],
    ]
    return [
        {
            **mention,
            "id": digest([card["id"], mention["id"]])[:32],
            "local_id": mention["id"],
            "card_id": card["id"],
            "book_id": card["book_id"],
            "run_id": card["run_id"],
            "state": card["state"],
            "evidence": [
                item for item in card["evidence"] if item["id"] in mention["evidence_ids"]
            ],
            "article_id": card["article_id"],
            "publication_sha256": card["publication_sha256"],
            "relations": [
                rel
                for rel in card["relations"]
                if mention["id"] in {rel["subject_id"], rel["object_id"]}
            ],
            "identity_scope": "source_mention",
        }
        for mention in mentions
    ]


class IdentityIndex:
    def __init__(self, ledger):
        self.ledger = ledger

    @staticmethod
    def ensure(connection):
        for sql in (
            "CREATE TABLE IF NOT EXISTS research_entity_index (id TEXT PRIMARY KEY, card_id TEXT NOT NULL, local_id TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS research_identity_index (card_id TEXT NOT NULL, ordinal INTEGER NOT NULL, "
            "source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT NOT NULL, PRIMARY KEY(card_id,ordinal))",
            "CREATE INDEX IF NOT EXISTS research_identity_source ON research_identity_index(source_id)",
            "CREATE INDEX IF NOT EXISTS research_identity_target ON research_identity_index(target_id)",
            "CREATE TABLE IF NOT EXISTS research_indexed_cards (card_id TEXT PRIMARY KEY)",
        ):
            connection.execute(sql)
        # Explicit runtime writes upgrade prior publications and mixed-version producers once.
        for (payload,) in connection.execute(
            "SELECT c.payload FROM research_cards c WHERE NOT EXISTS "
            "(SELECT 1 FROM research_indexed_cards i WHERE i.card_id=c.id) AND c.version="
            "(SELECT MAX(v.version) FROM research_cards v WHERE v.id=c.id)"
        ):
            IdentityIndex.record(connection, json.loads(payload))

    @staticmethod
    def record(connection, card):
        connection.execute("INSERT OR IGNORE INTO research_indexed_cards VALUES (?)", (card["id"],))
        for mention in [*card["mentions"], *card["events"]]:
            connection.execute(
                "INSERT OR IGNORE INTO research_entity_index VALUES (?,?,?)",
                (digest([card["id"], mention["id"]])[:32], card["id"], mention["id"]),
            )
        for ordinal, link in enumerate(card.get("identity_links", [])):
            connection.execute(
                "INSERT OR IGNORE INTO research_identity_index VALUES (?,?,?,?,?)",
                (
                    card["id"],
                    ordinal,
                    digest([card["id"], link["local_mention_id"]])[:32],
                    link["entity_id"],
                    link["relation"],
                ),
            )

    @staticmethod
    def adjacent(connection, identities, limit):
        marks = ",".join("?" for _ in identities)
        return connection.execute(
            "SELECT i.card_id,i.ordinal,i.source_id,i.target_id,i.relation FROM research_identity_index i "
            "JOIN research_cards c ON c.id=i.card_id AND c.version=(SELECT MAX(v.version) FROM research_cards v WHERE v.id=c.id) "
            "JOIN research_entity_index e ON e.id=i.target_id "
            "JOIN research_cards t ON t.id=e.card_id AND t.version=(SELECT MAX(v.version) FROM research_cards v WHERE v.id=t.id) "
            "WHERE c.state IN ('verified','human_accepted') AND t.state IN ('verified','human_accepted') "
            f"AND (i.source_id IN ({marks}) OR i.target_id IN ({marks})) ORDER BY i.card_id,i.ordinal LIMIT ?",
            [*identities, *identities, limit],
        ).fetchall()

    @staticmethod
    def read_card(connection, card_id, cache):
        if card_id not in cache:
            row = connection.execute(
                "SELECT payload FROM research_cards WHERE id=? ORDER BY version DESC LIMIT 1",
                (card_id,),
            ).fetchone()
            if not row:
                raise FileNotFoundError(card_id)
            cache[card_id] = json.loads(row[0])
        return cache[card_id]

    @staticmethod
    def read_entity(connection, entity_id, cache):
        row = connection.execute(
            "SELECT card_id FROM research_entity_index WHERE id=?", (entity_id,)
        ).fetchone()
        if not row:
            raise FileNotFoundError(entity_id)
        entity = next(
            (
                item
                for item in card_entities(IdentityIndex.read_card(connection, row[0], cache))
                if item["id"] == entity_id
            ),
            None,
        )
        if entity is None:
            raise FileNotFoundError(entity_id)
        return entity

    def entity(self, entity_id, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Identity detail limit must be between 1 and 100")
        with self.ledger.connect() as connection:
            if not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='research_entity_index'"
            ).fetchone():
                raise FileNotFoundError(entity_id)
            cache = {}
            entity = self.read_entity(connection, entity_id, cache)
            connected, frontier, links, truncated = {entity_id}, {entity_id}, {}, False
            while frontier and len(links) < 200:
                following = set()
                rows = self.adjacent(connection, sorted(frontier), 201)
                truncated |= len(rows) > 200
                for card_id, ordinal, source, target, relation in rows[:200]:
                    key = (card_id, ordinal)
                    if key in links:
                        continue
                    card = self.read_card(connection, card_id, cache)
                    links[key] = {
                        **card["identity_links"][ordinal],
                        "source_entity_id": source,
                        "card_id": card_id,
                        "approval_type": card["approval_type"],
                    }
                    for other in {source, target} - connected if relation == "same_as" else ():
                        if len(connected) >= limit:
                            truncated = True
                            continue
                        connected.add(other)
                        following.add(other)
                    if len(links) >= 200:
                        truncated = True
                        break
                frontier = following
            return {
                **entity,
                "mentions": [
                    self.read_entity(connection, identity, cache) for identity in sorted(connected)
                ],
                "identity_links": list(links.values()),
                "identity_truncated": truncated,
            }

    def reference(self, entity_id):
        with self.ledger.connect() as connection:
            return self.read_entity(connection, entity_id, {})
