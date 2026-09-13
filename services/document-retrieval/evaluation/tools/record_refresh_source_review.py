"""Persist the active GPT's original-page comparison, before fresh retrieval."""

import hashlib
import json
from pathlib import Path

from document_retrieval.records import atomic_json, utc_now

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_text("utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    original_path = ROOT / "evaluation/development/refresh-original-tasks-v1.json"
    original = read(original_path)
    registrations = read(ROOT / "evaluation/development/refresh-details-v1-rendered.json")
    selected = {
        "family-18": {
            "r8125-8214",
            "r8216-8489",
            "r8491-8663",
            "r8665-8840",
            "r8842-8927",
            "r8929-9081",
            "r35278-35325",
            "r12595-12616",
            "r12618-12734",
            "r12736-12946",
            "r12948-13242",
            "r13244-13341",
            "r13343-13427",
            "r13429-13649",
            "r35786-35837",
            "r35839-35883",
            "r15728-15991",
            "r15993-16361",
            "r16363-16503",
            "r16505-16682",
            "r36023-36071",
            "r36073-36120",
            "r36122-36165",
            "r17015-17030",
            "r17032-17585",
            "r17587-17764",
            "r36232-36293",
        },
        "family-19": {
            "r8937-8961",
            "r8963-9318",
            "r9320-9806",
            "r9808-10085",
            "r52695-52767",
            "r10085-10250",
            "r10252-10276",
            "r10278-10323",
            "r10325-10348",
            "r10350-10378",
            "r10380-10399",
            "r10409-10462",
            "r52785-52834",
            "r25262-26202",
            "r36384-36665",
            "r36667-36697",
            "r36699-36974",
            "r36976-37474",
            "r38964-38982",
            "r38984-39403",
        },
    }
    for family, accepted in selected.items():
        folder = ROOT / "state/real-corpus" / family
        candidates_path = folder / "qualification-candidates-v3.json"
        candidates = read(candidates_path)["candidates"]
        ranges = {}
        for c in candidates:
            if c["id"] not in accepted:
                continue
            start, end = c["source"]["start"], c["source"]["end"]
            if c["text"].startswith("#"):
                start += len(c["text"]) - len(c["text"].lstrip("# "))
            if c["id"] == "r17587-17764":
                end = c["source"]["start"] + c["text"].index("此外，")
            ranges[c["id"]] = [{"start": start, "end": end}]
            if c["id"] == "r15993-16361":
                mismatch = c["source"]["start"] + c["text"].index("管办")
                ranges[c["id"]] = [
                    {"start": start, "end": mismatch},
                    {"start": mismatch + 1, "end": end},
                ]
        assert accepted == set(ranges)
        registered = next(r for r in registrations if r["id"] == family + "-detail")
        evidence = [e for e in original["original_evidence"] if family in e["path"]]
        assert all(sha(Path(e["path"])) == e["sha256"] for e in evidence)
        verdict = {
            "classification": "machine_review",
            "reviewer": "active Codex GPT vision model",
            "original_first": True,
            "retrieval_candidates_seen": False,
            "source_text_candidates_compared": True,
            "human_review": False,
            "sealed_gold": False,
            "historical_truth_approved": False,
            "confidence": "high",
            "reviewed_at": utc_now(),
            "original_source_sha256": registered["source_sha256"],
            "candidates_sha256": sha(candidates_path),
            "original_evidence": evidence,
            "original_stage": {"path": str(original_path), "sha256": sha(original_path)},
            "accepted_ids": sorted(accepted),
            "source_ranges_by_id": ranges,
            "fields_by_id": {identity: ["text", "provenance"] for identity in accepted},
            "rejected_ids": [],
            "unreviewed_ids": [c["id"] for c in candidates if c["id"] not in accepted],
            "observations": [
                "Original PDF pages were inspected before source-text candidates. The selected text was then compared to those originals; detailed originals were revisited to resolve uncertain characters before any fresh retrieval query.",
                "Approve only the listed Unicode ranges for faithful source text and PDF/page provenance. Markdown heading prefixes are excluded. Unlisted fragments, whole-page text, bibliographic inference and historical truth are not approved.",
                "Xinjiang p16 visibly reads 和平友谊; the first original-stage transcription 和平安立 was mistaken. The frozen source candidate is correct. P20's second occurrence of 管办 differs from original 官办: exclude only that character, preserving the source error as a gap. The clothing paragraph is bounded before the incomplete final sentence on the next unreviewed page.",
                "Lanzhou originals show 陇海大旅社, 内外的联络工作 and 七十二个分团. Their printed wording and the author's grammatical or historical statements are preserved without correction. The paired p12/p13 paragraph is reviewed on both original pages. This is a modern thesis, not wartime primary evidence or an English-body source.",
            ],
        }
        path = folder / "gpt-source-review-v3.json"
        assert not path.exists(), path
        atomic_json(path, verdict)
        print(
            json.dumps(
                {
                    "family": family,
                    "accepted_blocks": len(accepted),
                    "qualified_ranges": sum(map(len, ranges.values())),
                    "review_sha256": sha(path),
                }
            )
        )
    revised = {
        **original,
        "reviewed_at": utc_now(),
        "classification": "machine_reference_original_preflight_revision",
    }
    revised["source_text_candidates_compared"] = True
    revised["previous_original_stage"] = {"path": str(original_path), "sha256": sha(original_path)}
    revised["preflight_corrections"] = [
        {
            "task_id": "fresh-r02",
            "before": "和平安立",
            "after": "和平友谊",
            "basis": "Original p16 reinspection resolves the first-stage transcription error.",
        },
        {
            "task_id": "fresh-r05",
            "before": "对外的联络工作",
            "after": "内外的联络工作",
            "basis": "Original p13 states 内外的联络工作.",
        },
    ]
    for task in revised["tasks"]:
        for correction in revised["preflight_corrections"]:
            if task["id"] == correction["task_id"]:
                task["evidence"] = [
                    correction["after"] if e == correction["before"] else e
                    for e in task["evidence"]
                ]
    final = ROOT / "evaluation/development/refresh-original-tasks-v2.json"
    assert not final.exists()
    atomic_json(final, revised)


if __name__ == "__main__":
    main()
