"""Bounded ledger views for research decisions, event navigation and incremental linking."""

import json

from hrs.processing.hashing import digest
from hrs.research.catalogue import ResearchCatalogue


class ResearchViews:
    def __init__(self, workflow):
        self.workflow = workflow
        self.ledger = workflow.ledger

    def pending(self, *, book_id=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Pending research limit must be 1–500")
        with self.ledger.connect() as connection:
            if not self.workflow._exists(connection):
                return {"items": [], "total": 0, "truncated": False}
            args = [str(book_id)] if book_id else []
            rows = connection.execute(
                "SELECT c.payload FROM research_cards c JOIN research_runs r ON r.id=c.run_id WHERE "
                "c.version=(SELECT MAX(v.version) FROM research_cards v WHERE v.id=c.id) "
                "AND c.state IN ('pending','verified','human_accepted') AND r.state<>'cancelled' "
                "AND (c.state='pending' OR EXISTS(SELECT 1 FROM json_each(c.payload,'$.identity_links') link "
                "WHERE json_extract(link.value,'$.relation')='possible_same_as') OR "
                "EXISTS(SELECT 1 FROM json_each(c.payload,'$.cross_relations') rel "
                "WHERE json_extract(rel.value,'$.certainty')='disputed')) "
                + ("AND c.book_id=? " if book_id else "")
                + "AND json_extract(r.payload,'$.created_at')>=COALESCE((SELECT MAX(json_extract(done.payload,'$.created_at')) "
                "FROM research_runs done WHERE done.book_id=c.book_id AND done.state='completed' "
                "AND COALESCE(json_extract(done.payload,'$.purpose'),'book')='book'),0) "
                "AND NOT EXISTS(SELECT 1 FROM research_cards replacement WHERE "
                "json_extract(replacement.payload,'$.replaces_card_id')=c.id AND replacement.state IN ('verified','human_accepted')) "
                "AND NOT EXISTS(SELECT 1 FROM research_revision_requests rr JOIN research_runs revision "
                "ON revision.id=rr.run_id WHERE rr.card_id=c.id AND rr.base_version=c.version "
                "AND revision.state IN ('ready','running')) "
                "ORDER BY c.id LIMIT ?",
                [*args, 1000],
            ).fetchall()
            items = []
            catalogue = ResearchCatalogue(self.ledger)
            for (payload,) in rows:
                card = json.loads(payload)
                base = {
                    "book_id": card["book_id"],
                    "run_id": card["run_id"],
                    "card_id": card["id"],
                    "title": card["title"],
                    "version": card["version"],
                }
                if card["state"] == "pending":
                    items.append(
                        {
                            **base,
                            "id": "card-" + card["id"],
                            "kind": "card",
                            "issues": card["checks"]["issues"],
                        }
                    )
                for index, link in enumerate(card.get("identity_links", [])):
                    if link["relation"] != "possible_same_as":
                        continue
                    source_id = digest([card["id"], link["local_mention_id"]])[:32]
                    try:
                        source = catalogue.entity(source_id)
                        target = catalogue.entity(link["entity_id"])
                    except FileNotFoundError:
                        continue
                    if source["id"] == target["id"] or any(
                        decision["action"] == "distinct"
                        and {decision["entity_id"], decision.get("target_id")}
                        == {source["id"], target["id"]}
                        for decision in source["identity_decisions"]
                    ):
                        continue
                    items.append(
                        {
                            **base,
                            "id": f"identity-{card['id']}-{index}",
                            "kind": "identity",
                            "entity_id": source["id"],
                            "target_id": target["id"],
                            "entity_version": source["version"],
                            "target_version": target["version"],
                            "issues": [link["reason"]],
                        }
                    )
                for relation in card.get("cross_relations", []):
                    if relation["certainty"] == "disputed":
                        items.append(
                            {
                                **base,
                                "id": f"relation-{card['id']}-{relation['id']}",
                                "kind": "relationship",
                                "relation_id": relation["id"],
                                "target_card_id": relation["target_card_id"],
                                "issues": [relation["reason"]],
                            }
                        )
            return {
                "items": items[:limit],
                "total": len(items),
                "truncated": len(items) > limit or len(rows) >= 1000,
            }

    def timeline(self, *, entity_id=None, book_id=None, limit=100):
        from hrs.research.network import NetworkQuery

        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("Timeline limit must be 1–200")
        result = NetworkQuery(self.ledger).timeline(
            entity_id=entity_id, book_ids=[str(book_id)] if book_id else None, limit=limit
        )
        catalogue = ResearchCatalogue(self.ledger)
        with self.ledger.connect() as connection:
            sources = (
                {
                    identity: (json.loads(payload), json.loads(card))
                    for identity, payload, card in connection.execute(
                        "SELECT m.id,m.payload,c.payload FROM research_catalogue_members m "
                        "JOIN research_cards c ON c.id=m.card_id AND c.version=m.card_version "
                        "WHERE m.id IN (SELECT value FROM json_each(?))",
                        (json.dumps([item["id"] for item in result["items"]]),),
                    )
                }
                if result["items"]
                else {}
            )
        entities, events = {}, []
        for item in result["items"]:
            identity = item["entity_id"]
            if identity not in entities:
                entities[identity] = catalogue.entity(identity)
            source, card = sources[item["id"]]
            events.append(
                {
                    **entities[identity],
                    **source,
                    **item,
                    "id": identity,
                    "evidence": [
                        span for span in card["evidence"] if span["id"] in item["evidence_ids"]
                    ],
                    "evidence_refs": [
                        {
                            "card_id": card["id"],
                            "card_version": card["version"],
                            "evidence_id": identity,
                        }
                        for identity in item["evidence_ids"]
                    ],
                }
            )
        return {**result, "items": events}

    def link_candidates(self, book_id, *, limit=20):
        """Names identify candidates only; native workers must read both documents and adjudicate."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Incremental linking limit must be 1–100")
        with self.ledger.connect() as connection:
            if not ResearchCatalogue.exists(connection):
                return []
            pairs = connection.execute(
                "SELECT DISTINCT old.book_id,old.card_id,new.card_id FROM research_catalogue_members old "
                "JOIN research_catalogue_members new ON json_extract(old.payload,'$.name')=json_extract(new.payload,'$.name') "
                "AND json_extract(old.payload,'$.kind')=json_extract(new.payload,'$.kind') "
                "WHERE old.active=1 AND new.active=1 AND new.book_id=? AND old.book_id<>new.book_id "
                "ORDER BY old.book_id,old.card_id,new.card_id LIMIT ?",
                (str(book_id), limit),
            ).fetchall()
            grouped = {}
            for old_book, old_card, new_card in pairs:
                group = grouped.setdefault(
                    old_book, {"book_id": old_book, "card_ids": set(), "target_card_ids": set()}
                )
                group["card_ids"].add(old_card)
                group["target_card_ids"].add(new_card)
                reverse = grouped.setdefault(
                    str(book_id),
                    {"book_id": str(book_id), "card_ids": set(), "target_card_ids": set()},
                )
                reverse["card_ids"].add(new_card)
                reverse["target_card_ids"].add(old_card)
            return [
                {
                    **group,
                    "card_ids": sorted(group["card_ids"]),
                    "target_card_ids": sorted(group["target_card_ids"]),
                }
                for group in grouped.values()
            ]
