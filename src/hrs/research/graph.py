"""Bounded graph views derived from retained relations; edges are research leads."""

from hrs.processing.hashing import digest


class ResearchGraph:
    def __init__(self, workflow):
        self.workflow = workflow

    def related(self, entity_id, *, hops=1, limit=50):
        if (
            type(hops) is not int
            or hops not in {1, 2}
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise ValueError("Graph queries require 1–2 hops and a limit between 1 and 100")
        entities = {
            item["id"]: item
            for item in self.workflow.list_entities()
            if item["state"] in {"verified", "human_accepted"}
        }
        if entity_id not in entities:
            raise FileNotFoundError(entity_id)
        edges, nodes = [], dict(entities)
        for card in self.workflow.list_cards():
            if card["state"] not in {"verified", "human_accepted"}:
                continue
            local = {
                item["id"]: digest([card["id"], item["id"]])[:32]
                for item in [*card["mentions"], *card["events"], *card["items"]]
            }
            for item in card["items"]:
                nodes[local[item["id"]]] = {
                    **item,
                    "id": local[item["id"]],
                    "kind": item["kind"],
                    "name": item["title"] or item["text"][:100],
                    "card_id": card["id"],
                    "book_id": card["book_id"],
                }
            relations = [
                {
                    **relation,
                    "source_id": local[relation["subject_id"]],
                    "target_id": local[relation["object_id"]],
                }
                for relation in card["relations"]
            ]
            relations += [
                {
                    "id": f"identity-{index}",
                    "source_id": local[link["local_mention_id"]],
                    "target_id": link["entity_id"],
                    "predicate": link["relation"],
                    "reason": link["reason"],
                    "evidence_ids": link["evidence_ids"],
                }
                for index, link in enumerate(card.get("identity_links", []))
            ]
            relations += [
                {
                    "id": f"participant-{event['id']}-{participant}",
                    "source_id": local[event["id"]],
                    "target_id": local[participant],
                    "predicate": "participant",
                    "reason": event["description"],
                    "evidence_ids": event["evidence_ids"],
                }
                for event in card["events"]
                for participant in event["participant_ids"]
            ]
            for relation in relations:
                evidence = [
                    item for item in card["evidence"] if item["id"] in relation["evidence_ids"]
                ]
                if (
                    not evidence
                    or relation["source_id"] not in nodes
                    or relation["target_id"] not in nodes
                ):
                    continue
                edges.append(
                    {
                        **relation,
                        "id": digest([card["id"], relation["id"]])[:32],
                        "card_id": card["id"],
                        "book_id": card["book_id"],
                        "run_id": card["run_id"],
                        "approval_type": card["approval_type"],
                        "evidence": evidence,
                        "usage": "research_lead",
                    }
                )
        edges.sort(key=lambda edge: edge["id"])
        reached, frontier, selected, truncated = {entity_id}, {entity_id}, {}, False
        for _ in range(hops):
            following = set()
            for edge in edges:
                pair = {edge["source_id"], edge["target_id"]}
                if not frontier & pair or edge["id"] in selected:
                    continue
                if len(selected) >= limit:
                    truncated = True
                    continue
                selected[edge["id"]] = edge
                following.update(pair - reached)
                reached.update(pair)
            frontier = following
            if not frontier:
                break
        return {
            "entity_id": entity_id,
            "hops": hops,
            "limit": limit,
            "truncated": truncated,
            "nodes": [nodes[identity] for identity in sorted(reached)],
            "edges": list(selected.values()),
            "usage": "Retained relationships are evidence-linked research leads, not new source facts.",
        }
