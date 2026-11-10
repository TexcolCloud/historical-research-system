"""Bounded graph views derived from retained relations; edges are research leads."""

from hrs.processing.hashing import digest


def graph_leads(graph, *, requested_limit):
    """MCP graph results are discovery metadata; full quotations remain source tools."""
    nodes = [
        {
            key: node[key]
            for key in ("id", "canonical_id", "kind", "name", "card_id", "book_id")
            if key in node
        }
        for node in graph["nodes"]
    ]
    edges = []
    for edge in graph["edges"]:
        preview = {
            key: edge[key]
            for key in (
                "id",
                "source_id",
                "target_id",
                "kind",
                "predicate",
                "card_id",
                "card_version",
                "target_card_id",
                "target_card_version",
                "approval_type",
            )
            if key in edge
        }
        preview["reason_preview"] = edge.get("reason", "")[:240]
        preview["evidence_refs"] = edge.get("evidence_refs", [])[:4]
        preview["evidence_index"] = [
            {
                key: span[key]
                for key in (
                    "id",
                    "article_id",
                    "book_id",
                    "source_run_id",
                    "publication_sha256",
                    "article_sha256",
                    "start",
                    "end",
                    "pages",
                )
                if key in span
            }
            for span in edge.get("evidence", [])[:4]
        ]
        edges.append(preview)
    return {
        **{key: graph[key] for key in ("entity_id", "hops", "limit", "truncated")},
        "nodes": nodes,
        "edges": edges,
        "requested_limit": requested_limit,
        "metadata_only": True,
        "usage": "Discovery leads only. Open the referenced frozen card and complete original sources before citing.",
    }


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
