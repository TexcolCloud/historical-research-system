"""Complete-document extraction and independent omission checks for policy v5."""

from hrs.processing.errors import EvidenceMismatch
from hrs.research.models import DocumentMetadata, IndependentFinding


class AssessmentMismatch(EvidenceMismatch):
    """Locate rejected source-finding mappings without changing the approval gate."""

    def __init__(self, issues):
        super().__init__(issues[0]["msg"])
        self.validation_issues = issues


def coverage_ranges(article):
    return [[part["start"], part["end"]] for part in article["parts"]]


def complete_checks(cards):
    checks = {}
    for field, collection in (
        ("checked_entity_ids", "mentions"),
        ("checked_event_ids", "events"),
        ("checked_coverage_ids", "coverage"),
        ("checked_research_question_ids", "research_plan"),
        ("checked_cross_relation_ids", "cross_relations"),
        ("checked_question_finding_ids", "question_findings"),
    ):
        checks[field] = [
            f"{card['id']}/{item['id']}" for card in cards for item in card[collection]
        ]
    checks["checked_metadata_fields"] = [
        f"{card['id']}/metadata/{key}" for card in cards for key in DocumentMetadata.model_fields
    ]
    return checks


def validate_complete_cards(
    cards, article, *, stage, planned_questions=None, questions=(), receipt_ids=()
):
    if len(cards) != 1:
        raise EvidenceMismatch("Policy v5 requires one complete document master card per article")
    card = cards[0]
    if card.get("metadata") is None:
        raise EvidenceMismatch(
            "Complete cards require explicit bibliographic and formation metadata"
        )
    for key, value in card["metadata"].items():
        if key != "title_origin" and value["certainty"] == "unknown" and not value["note"].strip():
            raise EvidenceMismatch(
                "Unknown metadata requires an explicit source limitation: " + key
            )
    if [[unit["start"], unit["end"]] for unit in card["coverage"]] != coverage_ranges(article):
        raise EvidenceMismatch(
            "Extraction coverage must account for every complete source part in order"
        )
    available = {
        key: {item["id"] for item in card[key]} for key in ("mentions", "events", "relations")
    }
    covered = {key: set() for key in available}
    for unit in card["coverage"]:
        if unit["article_id"] != article["id"]:
            raise EvidenceMismatch("Extraction coverage belongs to another article")
        for collection, field, reason in (
            ("mentions", "mention_ids", "no_mentions_reason"),
            ("events", "event_ids", "no_events_reason"),
            ("relations", "relation_ids", "no_relations_reason"),
        ):
            if not set(unit[field]) <= available[collection]:
                raise EvidenceMismatch("Extraction coverage references an absent candidate object")
            if not unit[field] and not unit[reason].strip():
                raise EvidenceMismatch(
                    "An empty extraction category requires a source-specific explanation"
                )
            covered[collection].update(unit[field])
    if covered != available:
        raise EvidenceMismatch(
            "Every extracted entity, event and relation requires a source coverage unit"
        )
    occurrence_errors = []
    for mention_index, mention in enumerate(card["mentions"]):
        if not mention["identity_key"].strip():
            raise EvidenceMismatch("Source mentions require a stable source-qualified identity_key")
        if not mention.get("locations"):
            raise EvidenceMismatch(
                "Complete source mentions require exact original occurrence locations"
            )
        for location_index, location in enumerate(mention["locations"]):
            issue_type, message = None, None
            if (
                location["article_id"] != article["id"]
                or not 0 <= location["start"] < location["end"] <= len(article["text"])
                or article["text"][location["start"] : location["end"]] != location["quote"]
            ):
                issue_type = "entity_occurrence_original_range"
                message = "Entity occurrence must exactly locate this original article"
            elif not any(
                evidence["id"] in mention["evidence_ids"]
                and evidence["article_id"] == location["article_id"]
                and evidence["start"] <= location["start"] < location["end"] <= evidence["end"]
                for evidence in card["evidence"]
            ):
                issue_type = "entity_occurrence_evidence_containment"
                message = "Entity occurrence must be contained in its cited evidence"
            if issue_type:
                context = {
                    "card_id": card["id"],
                    "mention_id": mention["id"],
                    "qualified_mention_id": f"{card['id']}/{mention['id']}",
                    "location": {key: location[key] for key in ("article_id", "start", "end")},
                    "expected_article_id": article["id"],
                    "article_length": len(article["text"]),
                    "cited_evidence": [
                        {
                            "evidence_id": evidence["id"],
                            "evidence_index": index,
                            "qualified_evidence_id": f"{card['id']}/{evidence['id']}",
                            **{key: evidence[key] for key in ("article_id", "start", "end")},
                        }
                        for index, evidence in enumerate(card["evidence"])
                        if evidence["id"] in mention["evidence_ids"]
                    ],
                }
                cited = context["cited_evidence"]
                ranges = (
                    "; ".join(
                        f"evidence[{span['evidence_index']}] {span['qualified_evidence_id']} "
                        f"{span['article_id']}[{span['start']},{span['end']})"
                        for span in cited[:3]
                    )
                    or "none"
                )
                if len(cited) > 3:
                    ranges += f"; +{len(cited) - 3} further cited spans; inspect the retained proposal"
                message += (
                    f": mention {context['qualified_mention_id']}; "
                    f"location {location['article_id']}[{location['start']},{location['end']}); "
                    f"cited {ranges}. Ranges use Unicode codepoints and exclusive ends."
                )
                occurrence_errors.append(
                    {
                        "loc": ("cards", 0, "mentions", mention_index, "locations", location_index),
                        "type": issue_type,
                        "msg": message,
                        "ctx": context,
                    }
                )
    if occurrence_errors:
        raise AssessmentMismatch(occurrence_errors)
    for event in card["events"]:
        if not event.get("status_note", "").strip():
            raise EvidenceMismatch("Event occurrence status requires a source-backed explanation")
        if not event["identity_key"].strip() or not event["action"].strip():
            raise EvidenceMismatch(
                "Events require a source-qualified identity_key and a concrete action"
            )
        roles = [role["mention_id"] for role in event["participant_roles"]]
        if set(roles) != set(event["participant_ids"]) or len(roles) != len(set(roles)):
            raise EvidenceMismatch("Each event participant requires exactly one source-backed role")
        if event["place"]["certainty"] == "unknown" and not event["place"]["note"].strip():
            raise EvidenceMismatch("Unknown event location requires an explicit limitation")
    if not card["research_plan"]:
        raise EvidenceMismatch(
            "Author must include a proposed card.research_plan; only enrich/repair call research_plan to persist it"
            if stage == "author"
            else "Enrich/repair must include their final server-recorded card.research_plan"
        )
    if stage in {"enrich", "repair"}:
        if planned_questions is None or card["research_plan"] != planned_questions:
            raise EvidenceMismatch("Submission must retain the final server-recorded research plan")
        for question in card["research_plan"]:
            if (
                question["status"] == "planned"
                or not question["evaluation"].strip()
                or not question["stop_reason"].strip()
            ):
                raise EvidenceMismatch(
                    "Research questions require evidence evaluation and a bounded stopping reason"
                )
            if not set(question["search_receipt_ids"]) <= set(receipt_ids):
                raise EvidenceMismatch("Research plan cites a nonexistent search receipt")
            if (
                question["status"] in {"answered", "partial", "conflicting"}
                and not question["evidence_ids"]
            ):
                raise EvidenceMismatch(
                    "Answered or conflicting research questions require retained evidence"
                )
    frozen = {question["id"]: question for question in questions}
    findings = {finding["question_id"]: finding for finding in card["question_findings"]}
    if set(findings) != set(frozen) or len(findings) != len(card["question_findings"]):
        raise EvidenceMismatch("Every frozen user question requires one version-bound finding")
    if any(
        findings[key]["definition_version"] != value["definition_version"]
        for key, value in frozen.items()
    ):
        raise EvidenceMismatch("User research question definition changed")


def validate_inventory(observations, article):
    inventory = observations.get("inventory")
    if not isinstance(inventory, dict):
        raise EvidenceMismatch(
            "Policy v5 independent reading requires a source-first extraction inventory"
        )
    coverage = inventory.get("coverage", [])
    if [[unit.get("start"), unit.get("end")] for unit in coverage] != coverage_ranges(article):
        raise EvidenceMismatch("Independent inventory must cover every original source part")
    if any(
        not isinstance(unit.get("summary"), str) or not unit["summary"].strip() for unit in coverage
    ):
        raise EvidenceMismatch("Independent coverage requires actual reading summaries")
    findings = [
        IndependentFinding.model_validate(item).model_dump()
        for item in inventory.get("findings", [])
    ]
    if len({item["id"] for item in findings}) != len(findings):
        raise EvidenceMismatch("Independent finding IDs must be unique")
    if not findings and not str(inventory.get("no_findings_reason", "")).strip():
        raise EvidenceMismatch("An empty independent inventory requires its source-specific reason")
    for finding in findings:
        if (
            finding["article_id"] != article["id"]
            or not 0 <= finding["start"] < finding["end"] <= len(article["text"])
            or article["text"][finding["start"] : finding["end"]] != finding["quote"]
        ):
            raise EvidenceMismatch(
                "Independent finding quotation must exactly match the frozen original"
            )
    return {
        "coverage": coverage,
        "findings": findings,
        "no_findings_reason": str(inventory.get("no_findings_reason", "")),
    }


def validate_assessments(assessments, inventory, cards, *, decision):
    findings = {item["id"]: item for item in inventory["findings"]}
    if {item["finding_id"] for item in assessments} != set(findings) or len(assessments) != len(
        findings
    ):
        raise EvidenceMismatch(
            "Independent verification must assess every pre-candidate source finding"
        )
    candidates = {}
    for card in cards:
        evidence = {item["id"]: item for item in card["evidence"]}
        for collection in ("mentions", "events", "relations"):
            for item in card[collection]:
                candidates[f"{card['id']}/{item['id']}"] = (
                    "event"
                    if collection == "events"
                    else "relation"
                    if collection == "relations"
                    else item["kind"],
                    [evidence[eid] for eid in item["evidence_ids"]],
                    item.get("event_status", "unknown"),
                )
    errors = []
    for index, assessment in enumerate(assessments):
        finding = findings[assessment["finding_id"]]
        if assessment["status"] == "covered":
            if not assessment["candidate_ids"]:
                raise EvidenceMismatch("Covered source findings require candidate object IDs")
            for candidate_index, identity in enumerate(assessment["candidate_ids"]):
                candidate = candidates.get(identity)
                if (
                    candidate is None
                    or candidate[0] != finding["kind"]
                    or not any(
                        span["article_id"] == finding["article_id"]
                        and span["start"] < finding["end"]
                        and span["end"] > finding["start"]
                        for span in candidate[1]
                    )
                ):
                    errors.append(
                        {
                            "loc": (
                                "coverage_assessments",
                                index,
                                "candidate_ids",
                                candidate_index,
                            ),
                            "type": "source_finding_mapping",
                            "msg": "Covered source findings require matching source-backed candidate objects: "
                            f"finding {finding['id']} ({finding['kind']}, article {finding['article_id']}, "
                            f"range {finding['start']}:{finding['end']}) is not covered by {identity}. "
                            "Use a qualified card_id/object_id of the same kind with overlapping source evidence. "
                            "If the candidate omits it, mark the finding missing, give its issue, and submit pending.",
                        }
                    )
                    continue
                if (
                    finding["kind"] == "event"
                    and finding.get("event_status", "unknown") != "unknown"
                    and candidate[2] != finding["event_status"]
                ):
                    errors.append(
                        {
                            "loc": (
                                "coverage_assessments",
                                index,
                                "candidate_ids",
                                candidate_index,
                            ),
                            "type": "source_event_modality",
                            "msg": "Event status changes the independently observed plan, condition, negation or report: "
                            f"finding {finding['id']} has {finding['event_status']} modality, "
                            f"but candidate {identity} has {candidate[2]}. "
                            "A plan, condition, negation or report cannot silently change; record the mismatch as pending.",
                        }
                    )
        elif decision == "approve" and (finding["required"] or assessment["status"] == "missing"):
            raise EvidenceMismatch(
                "Required source findings cannot be silently omitted from approved cards"
            )
    if errors:
        raise AssessmentMismatch(errors)
