"""Exercise public receipts on both sides of an actual paired offline restore."""

import argparse
import json
from pathlib import Path

from document_retrieval.client import RetrievalClient
from document_retrieval.errors import Problem
from document_retrieval.records import atomic_json, fingerprint, new_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "verify"))
    args = parser.parse_args()
    directory = Path("evaluation/development")
    client = RetrievalClient()
    old = json.loads((directory / "engineering-three-modes.json").read_text(encoding="utf-8"))[0]
    try:
        if args.phase == "prepare":
            source = old["read"]["units"][0]
            request = {
                "query": source["text"],
                "mode": "exact_quote",
                "purpose": "source_reading",
                "scope": {
                    "kind": "snapshot",
                    "snapshot_id": source["source_anchor"]["snapshot_id"],
                },
            }
            result = client.search(request, key=new_id("recovery_probe"), wait=True)
            assert result["items"][0]["excerpt"] == source["text"]
            baseline = client.post("readiness-baselines", {}, new_id("recovery_probe"))
            atomic_json(
                directory / "recovery-after-package-receipt.json",
                {"new_result": result, "baseline": baseline},
            )
            print(
                json.dumps(
                    {
                        "new_list": result["list_id"],
                        "response_tokens": result["budget"]["response_tokens"],
                    }
                )
            )
            return
        later = json.loads(
            (directory / "recovery-after-package-receipt.json").read_text(encoding="utf-8")
        )
        restored = client.post(f"searches/{old['result']['list_id']}/results", {})
        assert restored["items"][0]["evidence_ref"] == old["result"]["items"][0]["evidence_ref"]
        reading = client.read(
            {
                "selection": {
                    "kind": "evidence",
                    "evidence_ref": restored["items"][0]["evidence_ref"],
                },
                "purpose": "source_reading",
            }
        )
        assert reading["units"][0]["source_anchor"] == old["read"]["units"][0]["source_anchor"]
        assert reading["units"][0]["text"] == old["read"]["units"][0]["text"]
        failures = {}
        for name, path, params in (
            ("newer_external_list", f"searches/{later['new_result']['list_id']}", {}),
            (
                "previous_readiness_cursor",
                "readiness-changes",
                {"after": later["baseline"]["baseline_change_cursor"]},
            ),
        ):
            try:
                client.get(path, **params)
            except Problem as error:
                failures[name] = {"code": error.code, "status": error.status}
            else:
                raise AssertionError(name + " unexpectedly survived the earlier control backup")
        fresh = client.post("readiness-baselines", {}, new_id("recovery_probe"))
        changes = client.get("readiness-changes", after=fresh["baseline_change_cursor"])
        result = {
            "state": "passed",
            "package_id": "recovery_952008c9a9274feca352ac7ef6ae7dfb",
            "old_list": restored["list_id"],
            "original_anchor_sha256": fingerprint(old["read"]["units"][0]["source_anchor"]),
            "restored_anchor_sha256": fingerprint(reading["units"][0]["source_anchor"]),
            "original_text_unchanged": True,
            "later_receipts": failures,
            "fresh_baseline": fresh,
            "fresh_changes": changes,
        }
        atomic_json(directory / "actual-paired-restore.json", result)
        print(json.dumps(result))
    finally:
        client.close()


if __name__ == "__main__":
    main()
