"""Offline replay of three original-reviewed retained pages; never modifies a job.

Run with --attempt pointing at the retained conversion attempt and --output at a new
development evidence directory. UUIDs below identify offline fixture anchors only.
"""

import argparse
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from document_ingestion.article_structure import compile_article
from document_ingestion.draft_contract import SetReading


def digest(data):
    return hashlib.sha256(data).hexdigest()


def evaluate(attempt, output):
    output.mkdir(parents=True, exist_ok=True)
    groups = []
    for pages in ((15,), (46, 47)):
        segments, coverage, rules, sources = [], [], [], {}
        for page in pages:
            path = attempt / "ocr" / f"page-{page:03}.paddleocr-vl.json"
            image = attempt / "pages" / f"page-{page:03}.png"
            raw = json.loads(path.read_text(encoding="utf-8"))
            blocks = raw["metadata"]["paddle_results"][0]["parsing_res_list"]
            text = "".join(b["block_content"] for b in blocks)
            span = str(uuid5(NAMESPACE_URL, str(path.resolve())))
            anchor = dict(
                snapshot_id=str(uuid5(NAMESPACE_URL, "offline-note-evaluation")),
                source_span_ref=span,
                start=0,
                end=len(text),
                expected_text_sha256=digest(text.encode()),
            )
            coverage.append(anchor)
            sources[span] = dict(
                text=text,
                physical_page=page,
                original_assets=[
                    dict(
                        asset_id=str(uuid5(NAMESPACE_URL, str(image.resolve()))),
                        path=str(image.resolve()),
                        sha256=digest(image.read_bytes()),
                    )
                ],
            )
            offset, body_ids, note_ids = 0, [], []
            for block in blocks:
                value = block["block_content"]
                if not value:
                    continue
                identity = f"p{page}-b{block['block_id']}"
                role = {
                    "paragraph_title": "title",
                    "header": "page_header",
                    "number": "page_number",
                    "footnote": "footnote",
                }.get(block["block_label"], "body")
                if role in {"body", "title"}:
                    body_ids.append(identity)
                if role == "footnote":
                    note_ids.append(identity)
                segments.append(
                    dict(
                        kind="source",
                        usage_kind="primary",
                        source={
                            **anchor,
                            "start": offset,
                            "end": offset + len(value),
                            "expected_text_sha256": digest(value.encode()),
                        },
                        fragment=dict(
                            id=identity,
                            role=role,
                            join="new_paragraph",
                            reason="Retained provider role checked against original in development",
                            printed_page=str(page - 14) if page in (46, 47) else None,
                        ),
                        evidence=[
                            {
                                "kind": "retained_provider_block",
                                "path": str(path.resolve()),
                                "sha256": digest(path.read_bytes()),
                                "json_pointer": f"/metadata/paddle_results/0/parsing_res_list/{block['block_id']}/block_content",
                            }
                        ],
                    )
                )
                offset += len(value)
            rules.append(
                dict(
                    id=f"page-{page}",
                    kind="page",
                    body_fragments=body_ids,
                    note_fragments=note_ids,
                    source_state="complete",
                    evidence=[
                        {
                            "kind": "development_original_scope",
                            "human_review": False,
                            "reason": "Page-local ①… numbering observed on original image",
                        }
                    ],
                )
            )
        op = SetReading.model_validate(
            dict(
                op="set_reading",
                op_id="reviewed-slice",
                target={
                    "target_type": "occurrence",
                    "target": {"id": str(uuid5(NAMESPACE_URL, str(pages)))},
                },
                segments=segments,
                structure={"coverage": coverage, "numbering_rules": rules},
            )
        )

        def resolve(anchor, sources=sources):
            source = sources[str(anchor.source_span_ref)]
            return {
                **source,
                "text": source["text"][anchor.start : anchor.end],
                "content_revision_id": str(anchor.source_span_ref),
                "evidence_revision_id": "offline-retained-provider",
            }

        article = compile_article(op, resolve)
        groups.append(
            {
                "pages": pages,
                "classification": "offline_development_slice_not_production_identity",
                "input": op.model_dump(mode="json"),
                "article": article,
            }
        )
    counts = {
        "reviewed_pages": 3,
        "original_reference_count": 7,
        "original_note_count": 7,
        "discovered_references": sum(len(g["article"]["note_references"]) for g in groups),
        "discovered_notes": sum(len(g["article"]["notes"]) for g in groups),
        "rule_adopted_relations": sum(
            c["status"] == "rule_adopted" for g in groups for c in g["article"]["note_candidates"]
        ),
        "production_model_calls": 0,
        "production_model_input_codepoints": 0,
        "production_model_images": 0,
        "human_per_note_confirmations": 0,
        "development_page_rule_declarations": 3,
        "limitations": [
            "Purposefully selected retained pages; no independent human gold or unseen-data estimate.",
            "Local scopes were declared after GPT original inspection; full-book automatic partitioning is not evaluated.",
            "Provider block text is replayed unchanged; conversion task and frozen baselines are untouched.",
            "No real cross-page continued note or book-endnote source was assessed.",
        ],
    }
    (output / "sample-replay.json").write_text(
        json.dumps({"metrics": counts, "groups": groups}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evaluate(args.attempt, args.output)
