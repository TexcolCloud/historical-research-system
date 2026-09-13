"""Construct labelled protocol inputs and adopt them through the real ingestion API.

The inherited one-pixel PNG tests asset transport only. Its constructed text is not
OCR output or a historical-quality sample and is never counted in the real corpus.
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from document_ingestion.client import ManagementClient, submit_package
from document_ingestion.packing import pack_directory

MODULE = Path(__file__).resolve().parents[2]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode("utf-8")).hexdigest()


def apply(client, folder, operations, snapshots, key):
    folder.mkdir(parents=True, exist_ok=True)
    body = {
        "kind": "catalogue",
        "scope": {"target_type": "catalogue"},
        "base_snapshot_ids": snapshots,
        "reason": "Synthetic retrieval protocol fixture; no historical or human approval.",
        "operations": operations,
    }
    save(folder / "request.json", body)
    draft = client.post("/api/v1/drafts", body, key + ":draft")
    preview = client.post(
        f"/api/v1/drafts/{draft['draft_id']}/previews",
        {"draft_revision_id": draft["current_revision_id"]},
        key + ":preview",
    )
    save(folder / "preview.json", preview)
    assert all(g["state"] == "ready" for g in preview["groups"]), preview
    receipt = client.post(
        f"/api/v1/drafts/{draft['draft_id']}/applications",
        {
            "draft_revision_id": draft["current_revision_id"],
            "preview_id": preview["preview_id"],
            "preview_fingerprint": preview["fingerprint"],
            "selected_group_ids": [g["group_id"] for g in preview["groups"]],
            "reason": "Execute the synthetic protocol fixture only.",
        },
        key + ":apply",
    )
    result = client.wait_job(receipt["job"]["job_id"], 600)
    save(folder / "result.json", result)
    assert (
        result["execution_state"] == "completed" and result["result"]["outcome"] == "succeeded"
    ), result
    allocated = {k: v for g in preview["groups"] for k, v in g["allocated_ids"].items()}
    return result, allocated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:18125")
    args = parser.parse_args()
    root = MODULE / "state" / args.run_id
    assert not root.exists(), "Use a new synthetic fixture ID"
    source = root / "工程协议来源"
    shutil.copytree(MODULE / "state/engineering-source/工程来源", source)
    paragraphs = []

    def add(
        name,
        text,
        *,
        kind="paragraph",
        status="usable",
        article="engineering-main",
        separator="\n\n",
        dependencies=None,
    ):
        paragraphs.append(
            {
                "id": name,
                "text": text,
                "kind": kind,
                "status": status,
                "article": article,
                "separator": separator,
                "dependencies": dependencies or [],
            }
        )

    add(
        "unicode",
        "工程取证甲𠀀乙。臺灣與大後方的研究。HistoricalEvidence substring ABCDef, punctuation! whitespace  preserved.",
    )
    add(
        "long",
        "LONG_BEGIN " + "连续原文工程甲乙丙丁。" * 170 + " CROSS_SOFT_BOUNDARY_TOKEN LONG_END",
    )
    add(
        "note-body",
        "BODY_NOTE_TARGET The reported amount is 37 engineering units, subject to note [1].",
        dependencies=["note-one"],
    )
    add(
        "note-one",
        "[1] NOTE_ONE The constructed unit is one protocol record, not a person or historical statistic.",
        kind="note",
    )
    add(
        "ambiguous",
        "AMBIGUOUS_NOTE_TARGET Two neighboring notes are not asserted to have a unique relation.",
    )
    add("ambiguous-a", "[2] NOTE_A Unassigned explanation A.", kind="note")
    add("ambiguous-b", "[2] NOTE_B Unassigned explanation B.", kind="note")
    add("gap-left", "HARDLEFT", separator="")
    add("gap-denied", "DENIED_MIDDLE", status="needs-evidence", separator="")
    add("gap-right", "HARDRIGHT")
    add("article-left", "ARTLEFT", separator="")
    add("article-right", "ARTRIGHT", article="engineering-other")
    add("semantic-left", "SEMLEFT", separator="")
    add("semantic-right", "SEMRIGHT")
    table = (
        "ENGINEERING_TABLE\n项目 | 值 | 单位\n--- | --- | ---\n"
        + "\n".join(f"ROW_{i:03d} | {i * 3} | records" for i in range(180))
        + "\nROW_TAIL_ZEBRA | 540 | records"
    )
    add("table", table, kind="table", dependencies=["note-one"])
    add(
        "unknown",
        "UNKNOWN_STRUCTURE_TARGET This text has no asserted paragraph or cell structure.",
        kind="unknown",
    )
    add("version-one", "VERSION_ONE_ONLY COMMON_VERSION_QUERY", article="engineering-version-one")
    add("version-two", "VERSION_TWO_ONLY COMMON_VERSION_QUERY", article="engineering-version-two")
    text = ""
    spans, blocks = [], []
    for item in paragraphs:
        start = len(text)
        text += item["text"]
        end = len(text)
        span = {
            "start": start,
            "end": end,
            "source_start": start,
            "source_end": end,
            "page": 1,
            "source_kind": "page-candidate",
            "source_ref": "reviews/page-001.json#/candidate_text",
            "article_id": item["article"],
            "text_sha256": sha(item["text"]),
        }
        spans.append(span)
        blocks.append(
            {
                "block_id": item["id"],
                "start": start,
                "end": end,
                "sources": [span],
                "pages": [1],
                "kind": item["kind"],
                "dependencies": item["dependencies"],
                "risks": [],
                "restricted_fields": [],
                "status": item["status"],
            }
        )
        item.update(start=start, end=end, text_sha256=sha(item["text"]))
        text += item["separator"]
    mapping = json.loads((source / "source-map.json").read_text("utf-8"))
    readiness = json.loads((source / "content-readiness.json").read_text("utf-8"))
    manifest = json.loads((source / "manifest.json").read_text("utf-8"))
    mapping.update(markdown_sha256=sha(text), spans=spans)
    readiness.update(
        markdown_sha256=sha(text),
        blocks=blocks,
        summary={
            state: sum(b["status"] == state for b in blocks)
            for state in ("usable", "usable-with-warning", "needs-evidence", "generated-not-source")
        },
    )
    (source / "document.candidate.md").write_bytes(text.encode("utf-8"))
    review = json.loads((source / "reviews/page-001.json").read_text("utf-8"))
    review["candidate_text"] = text
    save(source / "reviews/page-001.json", review)
    save(source / "source-map.json", mapping)
    save(source / "content-readiness.json", readiness)
    manifest["evidence_hashes"] = {
        name: sha((source / name).read_bytes())
        for name in ("source-map.json", "content-readiness.json")
    }
    save(source / "manifest.json", manifest)
    save(
        root / "constructed-input.json",
        {
            "classification": "synthetic_protocol_only",
            "original_image_is_one_pixel": True,
            "historical_quality_sample": False,
            "parts": paragraphs,
            "source_sha256": manifest["source_sha256"],
        },
    )
    archive = root / "工程协议包.zip"
    packed = pack_directory(source, source / "original.png", archive)
    save(root / "packing.json", packed)
    submitted, code = submit_package(
        SimpleNamespace(
            url=args.url,
            package=archive,
            existing_carrier=None,
            display_name="Synthetic retrieval protocol " + args.run_id,
            format_mode="standard",
            receipt=root / "submit.json",
            wait=True,
            timeout=600,
        )
    )
    save(root / "submission.json", submitted)
    assert code == 0, submitted
    receipt = submitted["import"]["receipt"]
    client = ManagementClient(args.url)
    snapshot = receipt["snapshot_id"]
    source_rows = client.get(f"/api/v1/snapshots/{snapshot}/segments")["items"]
    assert len(source_rows) == len(paragraphs)
    by_range = {(s["start"], s["end"]): s for s in source_rows}
    for item in paragraphs:
        s = by_range[item["start"], item["end"]]
        item["source"] = {k: s[k] for k in ("snapshot_id", "source_span_ref", "start", "end")}
        item["source"]["expected_text_sha256"] = s["text_sha256"]
    proof = [
        {
            "classification": "synthetic_engineering_only",
            "artifact": str(root / "constructed-input.json"),
        }
    ]
    operations = [
        {
            "op": "create_document",
            "op_id": "document",
            "client_ref": "document",
            "display_name": "工程同名多版本",
        },
        {
            "op": "create_organization",
            "op_id": "organization",
            "client_ref": "organization",
            "display_name": "工程取证目录",
        },
    ]
    for label in ("one", "two"):
        edition, occurrence = "edition-" + label, "occurrence-" + label
        operations += [
            {
                "op": "create_edition",
                "op_id": edition,
                "client_ref": edition,
                "document": {"client_ref": "document"},
                "description": "Synthetic version " + label,
            },
            {
                "op": "create_occurrence",
                "op_id": occurrence,
                "client_ref": occurrence,
                "edition": {"client_ref": edition},
            },
            {
                "op": "create_node",
                "op_id": "node-" + label,
                "client_ref": "node-" + label,
                "organization": {"client_ref": "organization"},
                "position": 0 if label == "one" else 1,
                "kind": "item",
                "label": "工程版本" + label,
                "role": "research_item",
                "occurrence": {"client_ref": occurrence},
                "label_origin": "provisional",
                "evidence": proof,
            },
        ]
        chosen = [
            p
            for p in paragraphs
            if (p["id"] != "version-two" if label == "one" else p["id"] == "version-two")
        ]
        operations.append(
            {
                "op": "set_reading",
                "op_id": "reading-" + label,
                "target": {"target_type": "occurrence", "target": {"client_ref": occurrence}},
                "organization": {"client_ref": "organization"},
                "segments": [
                    {
                        "kind": "source",
                        "source": p["source"],
                        "usage_kind": "primary" if i == 0 else "continuation",
                        "source_edition": {"client_ref": edition},
                        "evidence": proof,
                    }
                    for i, p in enumerate(chosen)
                ],
            }
        )
    for label, year in (("adopted", 1937), ("conflict", 1939)):
        operations.append(
            {
                "op": "add_bibliographic_assertion",
                "op_id": label,
                "client_ref": label,
                "subject": {"client_ref": "edition-one"},
                "field": "date",
                "semantic": "publication",
                "value": {"calendar": "gregorian", "year": year},
                "state": "confirmed",
                "evidence": proof,
            }
        )
    operations.append(
        {
            "op": "select_bibliography",
            "op_id": "select-date",
            "subject": {"client_ref": "edition-one"},
            "selections": [
                {
                    "assertion": {"client_ref": "adopted"},
                    "adopted": True,
                    "reason": "Synthetic chosen date",
                },
                {
                    "assertion": {"client_ref": "conflict"},
                    "adopted": False,
                    "reason": "Synthetic conflicting alternative",
                },
            ],
        }
    )
    result, allocated = apply(client, root / "adoption", operations, [snapshot], args.run_id)
    snapshots = [
        e["snapshot_id"]
        for g in result["result"]["groups"]
        for e in g.get("receipt", {}).get("effects", [])
        if e["kind"] == "set_reading"
    ]
    save(
        root / "fixture.json",
        {
            "classification": "synthetic_engineering_only",
            "carrier_snapshot": snapshot,
            "allocated_ids": allocated,
            "reading_snapshots": snapshots,
            "parts": paragraphs,
            "source_segments": source_rows,
            "human_review": False,
            "sealed_gold": False,
        },
    )
    print(
        json.dumps(
            {
                "fixture": str(root / "fixture.json"),
                "sources": len(paragraphs),
                "snapshots": snapshots,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
