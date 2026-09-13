"""Export source-bound Markdown, review cards and the v4 extraction package."""

from dataclasses import asdict
from pathlib import Path

from hrs_runtime.page_layout import RULE_VERSION as LAYOUT_POLICY
from hrs_runtime.page_layout import page_layout_exclusions
from hrs_runtime.review_scope import figure_page, locate

from .content_readiness import _blocks
from .models import PageResult
from .provenance import MappedText, source_map, text_hash
from .review_routing import ARCHIVE_POLICY, conversion_deferred, rule_passed
from .review_routing import RELEASE_POLICY as ROUTED_RELEASE_POLICY
from .semantic_completion import POLICY
from .utils import sha256, write_json

RELEASE_POLICY = 'deepseek-errors-only-block-v1'


def block_text_hints(page):
    """Character offsets are exact only for a unique match; image location stays page-level."""
    hints = []
    for index, block in enumerate((page.get('ocr_evidence') or {}).get('blocks', [])):
        value = block.get('text', '')
        start = page['text'].index(value) if value and page['text'].count(value)==1 else None
        hints.append({'block_index':index, 'label':block.get('label'), 'order':block.get('order'),
            'text_sha256':text_hash(value), 'start':start, 'end':start+len(value) if start is not None else None,
            'offset_basis':'page_candidate_unicode_codepoints', 'page_text_sha256':text_hash(page['text']),
            'provider_bbox':block.get('bbox'), 'coordinate_basis':'provider-layout-frame-pixels',
            'image_localization':'page', 'basis':'unique-exact-text' if start is not None else 'unmapped-provider-block'})
    return hints


def _issue_range(page, concern):
    return locate(page["text"], concern.get("excerpt", ""))


def clean_page_numbers(page):
    """Normalize the conversion candidate before any downstream offsets are assigned."""
    original = page['text']
    previous = page.get('layout_cleanup')
    if previous and previous.get('rule') == LAYOUT_POLICY and previous.get('output_sha256') == text_hash(original):
        return page
    exclusions = page_layout_exclusions(original, 0, {'block_text_hints':block_text_hints(page)})
    removed = [item for item in exclusions if item['role']=='page_number']
    cleaned, cursor, retained = '', 0, []
    for item in removed + [{'start':len(original), 'end':len(original)}]:
        if cursor < item['start']:
            retained.append({'start':len(cleaned),'end':len(cleaned)+item['start']-cursor,
                             'source_start':cursor,'source_end':item['start']})
            cleaned += original[cursor:item['start']]
        cursor = item['end']
    trace = {'rule':LAYOUT_POLICY, 'original_text':original, 'input_sha256':text_hash(original),
             'output_sha256':text_hash(cleaned), 'removed':removed, 'retained_ranges':retained,
             'offset_basis':'page_candidate_unicode_codepoints', 'human_review':False}
    if previous:
        trace['previous'] = previous
    return {**page, 'text':cleaned, 'final_text_sha256':text_hash(cleaned), 'layout_cleanup':trace}


def write_outputs(
    source, output, completion, backend_names, runtime, *, auto_accept=True, filter_page_numbers=True
):
    """Keep the existing extraction-package versions with new semantic policy."""
    if filter_page_numbers:
        completion = {**completion, 'pages':[clean_page_numbers(page) for page in completion['pages']]}
    output.mkdir(parents=True, exist_ok=True)
    routed = 'routing_summary' in completion
    release_policy = ROUTED_RELEASE_POLICY if routed else RELEASE_POLICY
    deferred = bool(completion['pages']) and all(conversion_deferred(p) for p in completion['pages'])
    if deferred:
        release_policy = ARCHIVE_POLICY
    results, final, page_offsets, assets = (
        [],
        MappedText(f"<!-- source: {source.stem} -->\n\n"),
        {},
        [],
    )
    for page in completion["pages"]:
        number = page["page"]
        image = Path(page["image_path"]).relative_to(output).as_posix()
        structure = page.get("structure", {})
        text = page["text"]
        page_offsets[number] = len(final.text)
        source_piece = MappedText.source(
            text,
            page=number,
            source_ref=f"reviews/page-{number:03d}.json#/candidate_text",
            source_kind="page-candidate",
            article_id=None,
            layout_group_id=f"page-{number:04d}",
            article_relation="unresolved",
            article_identity_status="carrier-only",
            content_role="body",
            boundary_basis="not-model-reviewed" if rule_passed(page) else "docling-document-structure",
            boundary_evidence=structure,
            layout_evidence=(page.get('ocr_evidence', {}).get('metadata') or {}).get('layout_evidence'),
            block_text_hints=block_text_hints(page),
            **({'layout_cleanup':{key:page['layout_cleanup'][key] for key in ('rule','input_sha256','output_sha256','removed','retained_ranges')}}
               if page.get('layout_cleanup') else {}),
            restructure_evidence=({**page['restructure_evidence'],
                'stale':page['restructure_evidence'].get('source_text_sha256') != text_hash(text)}
                if page.get('restructure_evidence') else None),
        )
        final = final + source_piece + "\n\n"
        risks = [
            {
                "kind": c["kind"],
                "reason": c["explanation"],
                "source_range": _issue_range(page, c),
            }
            for c in page["concerns"]
        ]
        eligible = bool((page["verified"] or rule_passed(page)) and auto_accept)
        assessment = {
            "decision": "ACCEPT" if eligible else "PAGE_REVIEW",
            "eligible": eligible,
            "reasons": [
                r["reason"]
                for r in risks
                if r["kind"] not in {"normalization", "citation"}
            ],
            "regions": risks,
            "policy": POLICY,
        }
        result = PageResult(
            number,
            image,
            text,
            "release-accepted" if eligible else "pending-human-review",
            None,
            page.get("ocr", {}),
            [],
            None,
            None,
            {
                "completion": page,
                "policy": POLICY,
                "risk_assessment": assessment,
                "final_risk_assessment": assessment,
                "changes": page["changes"],
                "structure": structure,
            },
        )
        results.append(result)
        assets.append(
            {
                "asset_id": f"original-page-{number:04d}",
                "page": number,
                "kind": "source-page",
                "path": image,
                "sha256": sha256(output / image),
                "source_evidence": True,
            }
        )
    mapping = source_map(source, output, final, results)
    mapping.update(
        structure_evidence_type="mixed-page-review-see-routing" if routed else "production-original-image-semantic-review",
        article_identity_basis="carrier-only; article organization belongs to document ingestion",
    )
    blocks = _blocks(final.text, mapping)
    by_page = {p["page"]: p for p in completion["pages"]}
    pages_with_tables = {n for b in blocks if b["kind"] == "table" for n in b["pages"]}
    for block in blocks:
        block['review_basis'] = sorted({by_page[n].get('review_route', 'deepseek') for n in block['pages']})
        for number in block["pages"]:
            page = by_page[number]
            receipts = page.get('receipts', [])
            if conversion_deferred(page):
                block['risks'].append({'reason': 'article-review-not-performed', 'kind': 'unreviewed',
                                       'page': number, 'scope': 'block', 'action': 'warning'})
                reasons = page['routing_decision']['reasons']
                for reason in reasons:
                    if reason in {'empty-or-sparse-text', 'garbled-or-repeated-characters', 'repeated-lines'}:
                        block['risks'].append({'reason':reason, 'kind':'program-anomaly',
                            'page':number, 'scope':'block', 'action':'warning'})
                if block['kind'] == 'table':
                    block['risks'].append({'reason':'table-original-review', 'kind':'table',
                        'page':number, 'scope':'block', 'action':'evidence-required'})
            elif not rule_passed(page) and (not receipts or receipts[-1].get('verdict', {}).get('review_state') != 'completed'):
                block['risks'].append({'reason': 'deepseek-review-incomplete', 'kind': 'unreviewed',
                                       'page': number, 'scope': 'block', 'action': 'evidence-required'})
            if not auto_accept:
                block["risks"].append(
                    {
                        "reason": "automatic-acceptance-disabled",
                        "action": "evidence-required",
                    }
                )
            for concern in page["concerns"]:
                if (
                    concern["kind"] == "table"
                    and number in pages_with_tables
                    and block["kind"] != "table"
                    and (
                        not concern.get("excerpt")
                        or page["text"].count(concern["excerpt"]) != 1
                    )
                ):
                    continue
                location = _issue_range(page, concern)
                left, right = location if location else (0, len(page['text']))
                left += page_offsets[number]
                right += page_offsets[number]
                if left < block["end"] and right > block["start"]:
                    warning = concern["kind"] in {"citation", "normalization"}
                    block["risks"].append(
                        {
                            "reason": concern["explanation"],
                            "kind": concern["kind"],
                            "page": number,
                            "scope": "block",
                            "action": "warning" if warning else "evidence-required",
                        }
                    )
                    if concern["kind"] == "citation":
                        block["restricted_fields"].append("bibliographic_reference")
                    if concern["kind"] == "attribution":
                        block["restricted_fields"].extend(["article_title", "author"])
            if not block["article_ids"]:
                block["restricted_fields"].extend(["article_title", "author"])
        if block["sources"]:
            block["status"] = (
                "needs-evidence"
                if any(r["action"] == "evidence-required" for r in block["risks"])
                else "usable-with-warning"
                if block["risks"]
                else "usable"
            )
        block["restricted_fields"] = sorted(set(block["restricted_fields"]))
        block["bibliographic_identity_verified"] = False
    # Link source-anchored continuation text, never a nearby footer or citation.
    for previous, page in zip(completion["pages"], completion["pages"][1:]):
        if page.get("structure", {}).get("continues_previous"):
            structure = page["structure"]

            def anchored_blocks(item, key):
                anchor = structure.get(key, "")
                if not anchor or item["text"].count(anchor) != 1:
                    return []
                start = page_offsets[item["page"]] + item["text"].index(anchor)
                end = start + len(anchor)
                return [b for b in blocks if b["start"] < end and b["end"] > start]

            before = anchored_blocks(previous, "continuation_before")
            after = anchored_blocks(page, "continuation_after")
            if before and after:
                before[-1]["dependencies"].append(after[0]["block_id"])
                after[0]["dependencies"].append(before[-1]["block_id"])
    by_id = {b["block_id"]: b for b in blocks}
    for _ in range(len(blocks)):
        changed = False
        for block in blocks:
            if block["status"] in {"usable", "usable-with-warning"} and any(
                by_id[d]["status"] == "needs-evidence" for d in block["dependencies"]
            ):
                block.update(status="needs-evidence")
                block["risks"].append(
                    {"reason": "unresolved-continuation", "action": "evidence-required"}
                )
                changed = True
        if not changed:
            break
    counts = {
        state: sum(b["status"] == state for b in blocks)
        for state in (
            "usable",
            "usable-with-warning",
            "needs-evidence",
            "generated-not-source",
        )
    }
    held_blocks = [b for b in blocks if b['status'] == 'needs-evidence']
    table_blocks = [b for b in held_blocks if any(r.get('kind') == 'table' and r['action'] == 'evidence-required' for r in b['risks'])]
    readiness = {
        "schema_version": 1,
        "policy": POLICY,
        "table_policy": "deepseek-error-only",
        "release_policy": release_policy,
        "source_sha256": mapping["source_sha256"],
        "markdown_sha256": mapping["markdown_sha256"],
        "structure_sha256": mapping["structure_sha256"],
        "offset_basis": mapping["offset_basis"],
        "summary": counts,
        "blocks": blocks,
        "articles": [],
        "meaning": "Source-preserved reading input; review evidence and local risks are separate from archival readiness." if deferred else "Semantically usable source text; individual quotations are checked against original evidence when used.",
    }
    for result in results:
        related = [b for b in blocks if result.page in b["pages"]]
        result.status = (
            "release-accepted"
            if related
            and all(
                b["status"] in {"usable", "usable-with-warning", "generated-not-source"}
                for b in related
            )
            else "pending-human-review"
        )
        assessment = result.processing["risk_assessment"]
        assessment.update(
            eligible=result.status == "release-accepted",
            decision="ACCEPT" if result.status == "release-accepted" else "PAGE_REVIEW",
        )
        assessment["reasons"] = list(
            dict.fromkeys(
                r["reason"]
                for b in related
                for r in b["risks"]
                if r["action"] == "evidence-required"
            )
        )
        if deferred and result.status == 'release-accepted':
            result.status = 'conversion-completed'
            assessment.update(eligible=False, decision='NOT_REVIEWED')
        write_json(output / "reviews" / f"page-{result.page:03d}.json", asdict(result))
    pending = [asdict(r) for r in results if r.status not in {"release-accepted", "conversion-completed"}]
    page_holds = {p['page'] for p in completion['pages'] if figure_page(p['text']) or any(
        c['kind'] not in {'citation', 'normalization'} and _issue_range(p, c) is None for c in p['concerns'])}
    cards = [
        {
            "page": r.page,
            "page_image": r.image,
            "decision": "PAGE_REVIEW",
            "reviewer_required": "human",
            "reasons": r.processing["risk_assessment"]["reasons"],
            "region": {
                "kind": "figure" if figure_page(by_page[r.page]['text']) else "page",
                "page": r.page,
                "issues": r.processing["risk_assessment"]["regions"],
                "start": page_offsets[r.page],
                "end": page_offsets[r.page] + len(by_page[r.page]['text']),
                "text": by_page[r.page]['text'],
            },
        }
        for r in results
        if r.status not in {"release-accepted", "conversion-completed"}
        and (r.page in page_holds or not any(r.page in b["pages"] for b in blocks))
    ]
    for block in held_blocks:
        for number in block["pages"]:
            if number in page_holds:
                continue
            result = next(r for r in results if r.page == number)
            cards.append(
                {
                    "page": number,
                    "page_image": result.image,
                    "decision": "PAGE_REVIEW",
                    "reviewer_required": "human",
                    "reasons": [r['reason'] for r in block['risks'] if r['action'] == 'evidence-required'],
                    "region": {
                        "kind": 'table' if block in table_blocks else block['kind'],
                        "page": number,
                        "block_id": block["block_id"],
                        "start": block["start"],
                        "end": block["end"],
                        "offset_basis": mapping["offset_basis"],
                        "text": final.text[block["start"] : block["end"]],
                    },
                }
            )
    (output / "document.candidate.md").write_text(
        final.text, encoding="utf-8", newline="\n"
    )
    write_json(output / "source-map.json", mapping)
    write_json(output / "content-readiness.json", readiness)
    write_json(
        output / "artifact-manifest.json",
        {
            "schema_version": 1,
            "source_sha256": sha256(source),
            "asset_count": len(assets),
            "assets": assets,
        },
    )
    write_json(
        output / "manual-review.json",
        {
            "pending_count": len(pending),
            "pages": pending,
            "table_pending_count": len(table_blocks),
            "table_policy": "deepseek-error-only",
        },
    )
    write_json(
        output / "review-cards.json",
        {"schema_version": 1, "card_count": len(cards), "cards": cards},
    )
    write_json(
        output / "page-boundaries.json",
        {
            "schema_version": 1,
            "offset_basis": mapping["offset_basis"],
            "pages": [
                {
                    "page": r.page,
                    "start": page_offsets[r.page],
                    "end": page_offsets[r.page] + len(r.candidate_text),
                }
                for r in results
            ],
        },
    )
    write_json(output / "semantic-acceptance.json", completion)
    write_json(
        output / "article-structure-review.json",
        {
            "schema_version": 1,
            "policy": POLICY,
            "articles": [],
            "reviewer": completion.get('reviewer', 'production-deepseek'),
            "source_sha256": sha256(source),
            "document_sha256": text_hash(final.text),
            "kind": "page-observations",
            "relation_graph_claimed": False,
            "observations": [
                {"page": p["page"], "structure": p.get("structure", {}), "review_route":p.get('review_route', 'deepseek')}
                for p in completion["pages"]
            ],
        },
    )
    complete = bool(results) and not pending
    manifest = {
        "schema_version": 4,
        "source": str(source.resolve()),
        "source_sha256": sha256(source),
        "page_count": len(results),
        "pending_human_review": len(pending),
        "ocr_backends": backend_names,
        "runtime": runtime,
        "candidate_markdown": "document.candidate.md",
        "page_number_policy": LAYOUT_POLICY if filter_page_numbers else "preserve-targeted-review-input",
        "page_boundaries": "page-boundaries.json",
        "source_map": "source-map.json",
        "content_readiness": "content-readiness.json",
        "artifact_manifest": "artifact-manifest.json",
        "asset_count": len(assets),
        "content_readiness_summary": counts,
        "manual_review": "manual-review.json",
        "review_cards": "review-cards.json",
        "review_card_count": len(cards),
        "semantic_acceptance": "semantic-acceptance.json",
        "article_structure_review": "article-structure-review.json",
        "acceptance_policy": POLICY,
        "release_policy": release_policy,
        "review_routing": completion.get('routing_summary'),
        "document_status": "conversion-completed" if deferred else "release-accepted" if complete else "pending-human-review",
        "content_status": "unreviewed-source" if deferred else "release-accepted" if not pending else "pending-human-review",
        "archive_status": "ready" if results else "incomplete",
        "review_status": "not-reviewed" if deferred else "see-review-evidence",
        "article_structure_status": "not-claimed",
        "review_scope": "source-carrier",
        "articles": [],
        "document_risks": [],
        "embedded_source_page_images": False,
        "pages": [
            {
                "page": r.page,
                "image": r.image,
                "image_sha256": sha256(output / r.image),
                "status": r.status,
                "ocr_similarity": None,
            }
            for r in results
        ],
    }
    evidence_files = [
        "source-map.json",
        "content-readiness.json",
        "semantic-acceptance.json",
        "article-structure-review.json",
        "artifact-manifest.json",
    ]
    evidence_files.extend(f"reviews/page-{r.page:03d}.json" for r in results)
    if (output / 'paddle-restructure.json').is_file():
        manifest['paddle_restructure'] = 'paddle-restructure.json'
        evidence_files.append('paddle-restructure.json')
    for name in ("docling-document.json", "conversion.json"):
        if (output / name).is_file():
            manifest[
                "docling_document"
                if name.startswith("docling")
                else "conversion_report"
            ] = name
            evidence_files.append(name)
    manifest["evidence_hashes"] = {
        name: sha256(output / name) for name in evidence_files
    }
    write_json(output / "manifest.json", manifest)
    return manifest
