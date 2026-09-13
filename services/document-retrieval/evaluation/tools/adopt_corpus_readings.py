"""Publish reviewed research compositions through ingestion's public draft workflow."""

import argparse
import hashlib
import json
import time
from pathlib import Path

import httpx

from document_retrieval.records import atomic_json, utc_now

MODULE = Path(__file__).resolve().parents[2]
ENDS = {
    "english-jones-dixie": 2,
    "family-01": 5,
    "family-02": 14,
    "family-03": 7,
    "family-04": 12,
    "family-05": 12,
    "family-06": 5,
    "family-07": 15,
    "family-08": 6,
    "family-09": 9,
    "family-10": 15,
    "family-11": 16,
    "family-12": 18,
    "family-13": 16,
    "family-14": 18,
    "family-15": 1,
    "family-16": 26,
    "family-17": 36,
    "family-18": 41,
    "family-19": 50,
    "family-20": 48,
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_anchor(span):
    return {
        **{key: span[key] for key in ("snapshot_id", "source_span_ref", "start", "end")},
        "expected_text_sha256": span["text_sha256"],
    }


def post(client, output, path, body, key):
    response = client.post(
        path, json=body, headers={"Idempotency-Key": output.parent.name + ":reading-v3:" + key}
    )
    atomic_json(output / (key + ".json"), {"status": response.status_code, "body": response.json()})
    response.raise_for_status()
    return response.json()


def groups_for(family):
    if family == "primary-new-fourth-army":
        return [
            ("report", list(range(1, 7)), "闽西南三个月和谈工作报告"),
            ("telegram-0928", [7], "张闻天毛泽东致林伯渠电 1937年9月28日"),
            ("telegram-1001", [8, 9], "中央书记处关于南方游击队集中改编电 1937年10月1日"),
        ]
    if family == "primary-political-council":
        return [
            (f"page-{page}", [page], f"国民参政会报刊原件 节录物理页 {page}")
            for page in range(1, 7)
        ]
    return [("main", list(range(1, ENDS[family] + 1)), None)]


def review_for(family, receipt, archive_path, groups):
    observations = []
    for name in ("calibration-original-tasks.json", "holdout-original-tasks.json"):
        source = json.loads((MODULE / "evaluation/development" / name).read_text("utf-8"))
        observations.extend(
            row["verdict"] for row in source["observations"] if row["family"] == family
        )
    if family == "family-03":
        observations.append(
            "Rechecked original pages 1, 2, 3 and 7: title, running heading, paragraph continuation and conclusion belong to one article. The extractor's page-3 article split is not adopted as a work boundary."
        )
    if family == "family-20":
        observations.append(
            "Original 48-page manuscript contains its cover, declarations, Chinese/English abstracts, contents, chapters, conclusion, bibliography, author biography and acknowledgments. The existing one-page candidate omitted the manuscript reading range. Adopt the whole manuscript with its own notes; English abstracts are not counted as English body. Reviewed cover/contents/bibliography at full resolution and the structure contact sheet; this is composition review, not approval of every extracted character."
        )
    if family == "english-jones-dixie":
        observations.append(
            "Original pages 1-2 are a continuous English memoir by Louis Jones, written in September 1986 and published by the 40th Bomb Group Association in March 1987. Adopt only the declared two-page excerpt of the six-page downloaded original. This is a retrospective participant account, not an official government position or independent historical adjudication."
        )
    observations.append(
        "Keep notes as separate original ranges in this explicitly adopted research item. Retain original offsets, readiness and all field restrictions. No character-level or bibliographic accuracy approval is implied by this composition."
    )
    if family == "family-02":
        observations.append(
            "Reading range ends at physical page 14; the mixed final page with a separate journal notice is outside this evaluation composition."
        )
    if family == "family-16":
        observations.append(
            "Exclude the separately identified final book advertisement (article-0002)."
        )
    if family == "primary-political-council":
        observations.append(
            "Each facsimile page is a separate research item. News items within a page are not asserted to be one continuous article; only separately reviewed headline text may qualify. These page compositions retain page-level original lookup."
        )
    observed = []
    for path in sorted((MODULE / "evaluation/development").glob("*-rendered.json")):
        rows = json.loads(path.read_text("utf-8"))
        if not isinstance(rows, list):
            continue
        for row in rows:
            if row.get("id") not in (family, family + "-structure"):
                continue
            observed.extend(row.get("rendered_pages", []))
            if row.get("contact_sheet"):
                observed.append(
                    {
                        "path": row["contact_sheet"],
                        "sha256": row["contact_sha256"],
                        "kind": "observed_contact_sheet",
                    }
                )
    assert observed, family
    return {
        "classification": "machine_review",
        "reviewer": "active Codex GPT vision model",
        "original_first": True,
        "human_review": False,
        "sealed_gold": False,
        "historical_truth_approved": False,
        "confidence": "high",
        "verdict": "verified_research_composition_only",
        "unresolved_items": [],
        "observations": observations,
        "original_source_sha256": receipt["source_sha256"],
        "original_evidence": observed,
        "raw_archive_sha256": digest(archive_path),
        "reading_groups": [
            {"id": key, "physical_pages": pages, "label": label} for key, pages, label in groups
        ],
        "reviewed_at": utc_now(),
    }


def apply_one(client, folder):
    family = folder.name
    output = folder / "research-reading-v3"
    output.mkdir(exist_ok=True)
    if (output / "result.json").exists():
        return json.loads((output / "result.json").read_text("utf-8"))
    receipt = json.loads((folder / "input.json").read_text("utf-8"))
    archive_path = folder / "original-review-source-archive.json"
    spans = json.loads(archive_path.read_text("utf-8"))["spans"]
    groups = groups_for(family)
    request_path = output / "request.json"
    if not request_path.exists():
        review_path = output / "gpt-composition-review.json"
        review = (
            json.loads(review_path.read_text("utf-8"))
            if review_path.exists()
            else review_for(family, receipt, archive_path, groups)
        )
        assert review["original_first"] and not review["human_review"] and not review["sealed_gold"]
        assert review["original_source_sha256"] == receipt["source_sha256"]
        assert review["raw_archive_sha256"] == digest(archive_path)
        review_hash = (
            digest(review_path) if review_path.exists() else atomic_json(review_path, review)
        )
        evidence = [
            {
                "kind": "gpt_original_first_machine_review",
                "sha256": review_hash,
                "record": str(review_path),
                "human_review": False,
            }
        ]
        ops = [
            {
                "op": "create_organization",
                "op_id": "organization",
                "client_ref": "org",
                "display_name": "检索固定研究范围 " + family,
            }
        ]
        for position, (key, pages, label) in enumerate(groups):
            selected = [
                span
                for span in spans
                if span["physical_page"] in pages
                and not (
                    family == "family-16"
                    and span["source_record"].get("article_id") == "article-0002"
                )
            ]
            assert selected, (family, key)
            label = label or Path(receipt["source"]).stem
            # Evaluation identity is explicit and provisional; no bibliographic
            # or historical-version relation to the earlier candidate is asserted.
            ops.extend(
                [
                    {
                        "op": "create_document",
                        "op_id": key + "-doc",
                        "client_ref": key + "-doc",
                        "display_name": label,
                    },
                    {
                        "op": "create_edition",
                        "op_id": key + "-edition",
                        "client_ref": key + "-edition",
                        "depends_on": [key + "-doc"],
                        "document": {"client_ref": key + "-doc"},
                        "description": "Fixed original-source evaluation reading; machine composition only.",
                    },
                    {
                        "op": "create_occurrence",
                        "op_id": key + "-occ",
                        "client_ref": key + "-occ",
                        "depends_on": [key + "-edition"],
                        "edition": {"client_ref": key + "-edition"},
                    },
                    {
                        "op": "create_node",
                        "op_id": key + "-node",
                        "client_ref": key + "-node",
                        "depends_on": ["organization", key + "-occ"],
                        "organization": {"client_ref": "org"},
                        "position": position,
                        "kind": "item",
                        "role": "research_item",
                        "label": label,
                        "label_origin": "provisional",
                        "occurrence": {"client_ref": key + "-occ"},
                        "evidence": evidence,
                    },
                ]
            )
            segments, dependencies = [], [key + "-node"]
            for index, span in enumerate(selected):
                note = span["source_record"].get("source_kind") == "footnote"
                segments.append(
                    {
                        "kind": "source",
                        "source": source_anchor(span),
                        "usage_kind": "primary"
                        if note or index == 0 or family == "primary-political-council"
                        else "continuation",
                        "source_edition": {"client_ref": key + "-edition"},
                        "evidence": evidence,
                    }
                )
                if note:
                    op_id = key + f"-note-{index}"
                    ops.append(
                        {
                            "op": "assign_attachment",
                            "op_id": op_id,
                            "depends_on": [key + "-node"],
                            "organization": {"client_ref": "org"},
                            "attachment": {"kind": "source_span", "id": span["source_span_ref"]},
                            "state": "confirmed",
                            "owners": [{"client_ref": key + "-occ"}],
                            "affected_occurrences": [{"client_ref": key + "-occ"}],
                            "restricted_uses": [],
                            "evidence": evidence,
                        }
                    )
                    dependencies.append(op_id)
            ops.append(
                {
                    "op": "set_reading",
                    "op_id": key + "-reading",
                    "depends_on": dependencies,
                    "target": {"target_type": "occurrence", "target": {"client_ref": key + "-occ"}},
                    "organization": {"client_ref": "org"},
                    "segments": segments,
                }
            )
        body = {
            "kind": "organization",
            "scope": {"target_type": "catalogue"},
            "base_snapshot_ids": [receipt["carrier_snapshot_id"]],
            "reason": "Adopt GPT original-first reviewed research compositions with separate source-note ranges for retrieval evaluation. Machine review only; source readiness and production OCR/DeepSeek are unchanged.",
            "operations": ops,
        }
        atomic_json(request_path, body)
    body = json.loads(request_path.read_text("utf-8"))
    draft = post(client, output, "drafts", body, "draft")
    preview = post(
        client,
        output,
        f"drafts/{draft['draft_id']}/previews",
        {"draft_revision_id": draft["current_revision_id"]},
        "preview",
    )
    assert all(group["state"] == "ready" for group in preview["groups"]), preview
    application = post(
        client,
        output,
        f"drafts/{draft['draft_id']}/applications",
        {
            "draft_revision_id": draft["current_revision_id"],
            "preview_id": preview["preview_id"],
            "preview_fingerprint": preview["fingerprint"],
            "selected_group_ids": [group["group_id"] for group in preview["groups"]],
            "reason": body["reason"],
        },
        "application",
    )
    deadline = time.monotonic() + 900
    while True:
        response = client.get(f"jobs/{application['job']['job_id']}")
        response.raise_for_status()
        job = response.json()
        if job["execution_state"] in ("completed", "failed", "cancelled"):
            break
        assert time.monotonic() < deadline, job
        time.sleep(0.5)
    atomic_json(output / "job.json", job)
    assert job["execution_state"] == "completed" and job["result"]["outcome"] == "succeeded", job
    effects = {
        effect["op_id"]: effect
        for group in job["result"]["groups"]
        for effect in group["receipt"]["effects"]
    }
    result_groups = []
    for key, pages, label in groups:
        identity = effects[key + "-reading"]["snapshot_id"]
        snapshot = client.get(f"snapshots/{identity}")
        snapshot.raise_for_status()
        result_groups.append({"id": key, "physical_pages": pages, "snapshot": snapshot.json()})
    result = {
        "family": family,
        "groups": result_groups,
        "application": job["result"],
        "review_sha256": digest(output / "gpt-composition-review.json"),
        "classification": "machine_adopted_research_composition",
    }
    atomic_json(output / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", nargs="+")
    args = parser.parse_args()
    with httpx.Client(base_url="http://127.0.0.1:18125/api/v1/", timeout=180) as client:
        for folder in sorted((MODULE / "state/real-corpus").iterdir()):
            if not (folder / "input.json").exists() or args.ids and folder.name not in args.ids:
                continue
            result = apply_one(client, folder)
            print(
                json.dumps(
                    {
                        "family": folder.name,
                        "groups": [
                            {"id": item["id"], "snapshot_id": item["snapshot"]["snapshot_id"]}
                            for item in result["groups"]
                        ],
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
