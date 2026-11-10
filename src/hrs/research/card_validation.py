"""Exact citation and identity-source checks, independent of task storage."""

from hrs.processing.errors import EvidenceMismatch
from hrs.research.complete_contracts import AssessmentMismatch
from hrs.research.models import is_card_only


def validate_card_evidence(cards, article_id, sources):
    evidence_errors, evidence_error_count = [], 0

    def reject_evidence(message, path, kind, context, detail):
        nonlocal evidence_error_count
        evidence_error_count += 1
        if len(evidence_errors) < 16:
            label = f"cards[{path[1]}].evidence[{path[3]}]"
            label += f".footnote_ids[{path[5]}]" if len(path) == 6 else ".quote"
            evidence_errors.append(
                {
                    "loc": path,
                    "type": kind,
                    "msg": f"{message}; {label}; {detail}; "
                    f"evidence {context['qualified_evidence_id']}, "
                    f"article {context['article_id']}"
                    f"[{context['evidence_range']['start']},{context['evidence_range']['end']}) "
                    "(exclusive end)",
                    "ctx": context,
                }
            )

    for card_index, card in enumerate(cards):
        if card["article_id"] != article_id:
            raise EvidenceMismatch("Card article is outside the task's frozen source")
        for evidence_index, evidence in enumerate(card["evidence"]):
            article = sources.get(evidence["article_id"])
            start, end = evidence["start"], evidence["end"]
            context = {
                "card_id": card["id"][:96],
                "evidence_id": evidence["id"][:96],
                "qualified_evidence_id": f"{card['id'][:96]}/{evidence['id'][:96]}",
                "article_id": evidence["article_id"][:128],
                "evidence_range": {"start": start, "end": end},
                "article_length": len(article["text"]) if article is not None else None,
            }
            if (
                article is None
                or not start < end <= len(article["text"])
                or article["text"][start:end] != evidence["quote"]
            ):
                original = article["text"][start:end] if article is not None else ""
                mismatch = next(
                    (
                        index
                        for index, (left, right) in enumerate(zip(original, evidence["quote"]))
                        if left != right
                    ),
                    min(len(original), len(evidence["quote"])),
                )
                reject_evidence(
                    "Evidence quote does not match the frozen original text range",
                    ("cards", card_index, "evidence", evidence_index, "quote"),
                    "evidence_original_range",
                    {
                        **context,
                        "quote_length": len(evidence["quote"]),
                        "first_difference_offset": start + mismatch,
                    },
                    f"source length={context['article_length']}, quote length={len(evidence['quote'])}, "
                    f"first difference offset={start + mismatch}",
                )
                continue
            notes = {
                str(note["id"]): (index, note)
                for index, note in enumerate(article.get("footnotes", []))
            }
            for note_index, note_id in enumerate(evidence["footnote_ids"]):
                source_note_index, note = notes.get(note_id, (None, None))
                path = (
                    "cards",
                    card_index,
                    "evidence",
                    evidence_index,
                    "footnote_ids",
                    note_index,
                )
                if note is None:
                    reject_evidence(
                        "Footnote belongs to another article or does not exist",
                        path,
                        "footnote_missing_source",
                        {
                            **context,
                            "note_id": note_id[:96],
                            "source_note_index": None,
                            "note_range": None,
                            "reference_ranges": [],
                        },
                        f"note_id={note_id[:96]} is absent from this frozen article",
                    )
                    continue
                ranges = [note.get("note", {}), *note.get("references", [])]
                if not any(
                    item.get("start", end) < end and item.get("end", start) > start
                    for item in ranges
                ):
                    note_range = {key: note.get("note", {}).get(key) for key in ("start", "end")}
                    references = [
                        {"index": index, "start": ref.get("start"), "end": ref.get("end")}
                        for index, ref in enumerate(note.get("references", [])[:8])
                    ]
                    omitted = max(0, len(note.get("references", [])) - len(references))
                    bounds = [(ref["start"], ref["end"]) for ref in references]
                    reject_evidence(
                        "Footnote citation is not owned by this evidence range",
                        path,
                        "footnote_evidence_ownership",
                        {
                            **context,
                            "note_id": note_id[:96],
                            "source_note_index": source_note_index,
                            "note_label": str(note.get("label", ""))[:64],
                            "note_range": note_range,
                            "reference_ranges": references,
                            "omitted_reference_ranges": omitted,
                        },
                        f"note_id={note_id[:96]} (source note[{source_note_index}]), "
                        f"note range=[{note_range['start']},{note_range['end']}), "
                        f"reference ranges={bounds}, {omitted} further reference ranges omitted",
                    )
    if evidence_errors:
        omitted = evidence_error_count - len(evidence_errors)
        if omitted:
            first = evidence_errors[0]
            prefix, detail = first["msg"].split("; ", 1)
            first["msg"] = (
                f"{prefix}; {evidence_error_count} invalid evidence references, {omitted} omitted; {detail}"
            )
            first["ctx"].update(error_count=evidence_error_count, omitted_errors=omitted)
        raise AssessmentMismatch(evidence_errors)


def validate_identity_sources(cards, task, run, sources, lookup_entity):
    for card in cards:
        for link in card["identity_links"]:
            if is_card_only(run["policy"]) and link["relation"] == "same_as":
                raise EvidenceMismatch(
                    "Global identity merges require an explicit local human decision; "
                    "card-only identity leads use possible_same_as"
                )
            target = lookup_entity(link["entity_id"])
            if target["state"] not in {"verified", "human_accepted"}:
                raise EvidenceMismatch(
                    "Identity target must be an existing verified source mention"
                )
            mentions = [
                *card["mentions"],
                *[{**event, "kind": "event"} for event in card["events"]],
            ]
            mention = next(item for item in mentions if item["id"] == link["local_mention_id"])
            if mention["kind"] != target["kind"]:
                raise EvidenceMismatch("Identity links cannot merge different entity kinds")
            linked_evidence = [
                item for item in card["evidence"] if item["id"] in link["evidence_ids"]
            ]
            linked_sources = {
                (
                    source.get("book_id", run["book_id"]),
                    source.get("publication_sha256", run["publication_sha256"]),
                    source.get("original_article_id", item["article_id"]),
                )
                for item in linked_evidence
                for source in [sources[item["article_id"]]]
            }
            target_sources = {
                (
                    target_source["book_id"],
                    target_source["publication_sha256"],
                    target_source.get("original_article_id", target_source["article_id"]),
                )
                for target_source in (target.get("source_mentions") or [target])
            }
            if not linked_sources & target_sources:
                raise EvidenceMismatch(
                    "Identity link must cite the frozen original source of its existing target"
                )
            primary = sources[task["article_id"]]
            primary_key = (
                primary.get("book_id", run["book_id"]),
                primary.get("publication_sha256", run["publication_sha256"]),
                primary.get("original_article_id", task["article_id"]),
            )
            if link["relation"] == "same_as" and (
                primary_key not in linked_sources
                or not ((linked_sources & target_sources) - {primary_key})
            ):
                raise EvidenceMismatch(
                    "A same_as identity decision requires evidence from both source articles"
                )
