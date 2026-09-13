"""Public upstream changes and durable local receipt/readiness observations."""

import argparse
import hashlib
import time

import httpx
from engineering_support import Engineering, digest, load

from document_retrieval.records import atomic_json, new_id


def evidence(run):
    return [{"classification": "synthetic_protocol_only", "fixture": str(run.fixture_path)}]


def reading_operation(run, parts):
    ids = run.fixture["allocated_ids"]
    return {
        "op": "set_reading",
        "op_id": "reading",
        "target": {"target_type": "occurrence", "target": {"id": ids["occurrence-one"]}},
        "organization": {"id": ids["organization"]},
        "segments": [
            {
                "kind": "source",
                "source": part["source"],
                "usage_kind": "primary" if i == 0 else "continuation",
                "source_edition": {"id": ids["edition-one"]},
                "evidence": evidence(run),
            }
            for i, part in enumerate(parts)
        ],
    }


def adoption(run):
    checkpoint = run.root / "adoption-before-upstream.json"
    if checkpoint.exists():
        saved = load(checkpoint)
        original, original_read, pending, marker, fixed = (
            saved[name] for name in ("original", "original_read", "pending", "marker", "fixed")
        )
    else:
        original = run.search("VERSION_ONE_ONLY", scope=run.current)
        assert original["items"]
        original_read = run.read(original["items"][0])
        run.arm("current-changes-before-first-result", "before_step", type="search", phase="finish")
        pending = run.client.post(
            "searches",
            {
                "query": "VERSION_ONE_ONLY",
                "mode": "exact_quote",
                "scope": run.current,
                "purpose": "card_input",
                "budget": run.budget,
            },
            run.run_id + ":current-first-change",
        )
        assert pending.get("job_id")
        marker = run.marker("current-changes-before-first-result")
        fixed = run.store().get("scope", marker["claimed_job"]["payload"]["scope_ref"])
        run.save(
            "adoption-before-upstream",
            {
                "original": original,
                "original_read": original_read,
                "pending": pending,
                "marker": marker,
                "fixed": fixed,
            },
        )
    assert fixed["members"][0]["snapshot_id"] == run.main["snapshot_id"]
    changed = run.upstream_apply(
        "change-current-reading-v2",
        [reading_operation(run, [run.parts["unicode"]])],
        [run.main["snapshot_id"], run.fixture["carrier_snapshot"]],
    )
    run.disarm()
    run.wait(pending["job_id"])
    denied = run.expect_problem(
        lambda: run.client.post("jobs/" + pending["job_id"] + "/result", {"budget": run.budget}),
        {"current_input_changed"},
    )
    old_list = run.client.post(
        "searches/" + original["list_id"] + "/results", {"budget": run.budget}
    )
    assert old_list["items"] == original["items"]
    explicit = run.search(
        "VERSION_ONE_ONLY", scope={"kind": "scope", "scope_ref": fixed["scope_ref"]}
    )
    assert (
        explicit["items"]
        and explicit["items"][0]["citation"]["snapshot_id"] == run.main["snapshot_id"]
    )
    not_ready = run.search("甲𠀀乙", scope=run.current)
    assert not_ready["outcome"] == "input_not_ready" and not not_ready["items"]
    restored = run.upstream_apply(
        "restore-current-reading",
        [reading_operation(run, [p for p in run.fixture["parts"] if p["id"] != "version-two"])],
    )
    return run.finish(
        "06b-current-adoption",
        {
            "original": original,
            "original_read": original_read,
            "pause": marker,
            "application": changed,
            "first_result_rejection": denied,
            "old_list": old_list,
            "explicit_fixed": explicit,
            "new_current_not_indexed": not_ready,
            "restoration": restored,
        },
    )


def purpose_changes(run):
    key = run.run_id + ":purpose-replay"
    query = {
        "query": "甲𠀀乙",
        "mode": "exact_quote",
        "scope": run.main,
        "purpose": "card_input",
        "budget": run.budget,
    }
    original = run.client.completed(run.client.post("searches", query, key), run.budget)
    item = original["items"][0]
    before_read = run.read(item)
    media = run.read(item, layer="media", purpose="source_reading")
    assets = [a for u in media["units"] for a in u.get("assets", [])]
    assert assets, media

    def stream(asset, byte_range=None):
        with run.client.media(
            item["evidence_ref"], asset["asset_id"], byte_range=byte_range
        ) as response:
            raw = b"".join(response.iter_bytes())
            return {
                "asset_id": asset["asset_id"],
                "status": response.status_code,
                "headers": dict(response.headers),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "problem": response.json() if response.is_error else None,
            }

    bytes_before = [stream(a) for a in assets]
    local_permission_only = None
    image_qualification = None
    if not all(a["status"] == 200 for a in bytes_before):
        assert all(
            a["status"] == 409 and a["problem"]["code"] == "asset_usage_excluded"
            for a in bytes_before
        ), bytes_before
        local_permission_only = bytes_before
        source = run.parts["gap-denied"]["source"]
        response = httpx.post(
            "http://127.0.0.1:18125/api/v1/source-qualification-context",
            json=source,
            timeout=180,
        )
        response.raise_for_status()
        context = response.json()
        assert context["reviewable"]
        original_review = run.fixture_path.parent / "gpt-protocol-review-v1.json"
        review = load(original_review)
        for image in review["original_evidence"]:
            assert digest(__import__("pathlib").Path(image["path"])) == image["sha256"]
        image_qualification = run.upstream_apply(
            "qualify-synthetic-image-only",
            [
                {
                    "op": "qualify_source",
                    "op_id": "image",
                    "client_ref": "image",
                    "source": source,
                    "context_sha256": context["context_sha256"],
                    "purposes": ["source_reading"],
                    "verified_fields": ["image"],
                    "review": {
                        "reviewer": "active Codex GPT vision model",
                        "original_first": True,
                        "verdict": "verified",
                        "confidence": "high",
                        "original_assets": context["original_assets"],
                        "observations": [
                            "The already viewed original is the hash-verified one-pixel synthetic image. This qualifies its image association for byte-transport evaluation only.",
                            "The constructed DENIED_MIDDLE characters are not verified by this image. Their text, provenance and card-input qualification remain unchanged.",
                        ],
                        "evidence": [
                            {
                                "record": str(original_review),
                                "sha256": digest(original_review),
                                "classification": "synthetic_image_transport_only",
                            }
                        ],
                    },
                }
            ],
        )
        run.save(
            "synthetic-image-qualification",
            {
                "local_permission_did_not_authorize_asset": local_permission_only,
                "context": context,
                "application": image_qualification,
            },
        )
        bytes_before = [stream(a) for a in assets]
    assert all(a["status"] == 200 and a["bytes"] > 0 for a in bytes_before)
    ranged = stream(assets[0], "bytes=0-0")
    assert ranged["status"] == 206 and ranged["bytes"] == 1
    assert ranged["headers"]["etag"] == bytes_before[0]["headers"]["etag"]
    source = run.parts["unicode"]["source"]
    operations = []
    for label, purposes, fields in (
        ("text", ["card_input"], ["text"]),
        ("image", ["source_reading", "card_input"], ["image"]),
    ):
        operations.append(
            {
                "op": "restrict_source",
                "op_id": label,
                "client_ref": label,
                "source": source,
                "purposes": purposes,
                "restricted_fields": fields,
                "effect": "blocked",
                "confirmation_kind": "machine_review",
                "evidence": evidence(run),
            }
        )
    restricted = run.upstream_apply("restrict-purpose", operations)
    replay = run.client.completed(run.client.post("searches", query, key), run.budget)
    assert replay["list_id"] == original["list_id"] and not replay["items"]
    assert any(i["code"] == "current_usage_excluded" for i in replay["issues"])
    reading = run.read(item)
    assert not reading["units"]
    allowed = run.read(item, purpose="source_reading")
    assert [u["source_anchor"] for u in allowed["units"]] == [
        u["source_anchor"] for u in before_read["units"]
    ]
    media_after = run.read(item, layer="media", purpose="source_reading")
    bytes_after = [stream(a) for a in assets]
    assert all(
        a["status"] == 409 and a["problem"]["code"] == "asset_usage_excluded" for a in bytes_after
    ), bytes_after
    restored = run.upstream_apply(
        "restore-purpose",
        [
            {
                "op": "restore_source_restriction",
                "op_id": label,
                "client_ref": label,
                "restriction": {"id": restricted["allocated"][label]},
                "source": source,
                "confirmation_kind": "machine_review",
                "evidence": evidence(run),
            }
            for label in ("text", "image")
        ],
    )
    after = run.client.completed(run.client.post("searches", query, key), run.budget)
    assert after["items"] == original["items"]
    bytes_restored = [stream(a) for a in assets]
    assert [a["sha256"] for a in bytes_restored] == [a["sha256"] for a in bytes_before]
    run.arm(
        "upstream-usage-unavailable",
        "before_upstream_usage",
        type="upstream_usage",
        mode="error",
        error_code="source_unavailable",
    )
    unavailable = run.expect_problem(lambda: run.read(item), {"source_unavailable"})
    run.disarm()
    recovered = run.read(item)
    assert recovered["units"]
    return run.finish(
        "06c-current-purpose",
        {
            "original": original,
            "before_read": before_read,
            "before_media": media,
            "local_permission_did_not_authorize_asset": local_permission_only,
            "image_only_qualification": image_qualification,
            "actual_streams_before": bytes_before,
            "actual_range": ranged,
            "restriction": restricted,
            "idempotent_replay": replay,
            "denied_read": reading,
            "allowed_other_purpose": allowed,
            "denied_media_options": media_after,
            "actual_denied_streams": bytes_after,
            "restoration": restored,
            "restored_replay": after,
            "restored_streams": bytes_restored,
            "upstream_failure": unavailable,
            "recovered": recovered,
            "historical_image_quality": False,
            "media_assets_returned_before_change": assets,
        },
    )


def events(run):
    initial = run.client.get("management/status")
    assert initial["received_cursor"] is not None, (
        "Explicit initial activation must initialize receipt"
    )
    baseline = run.client.post("readiness-baselines", {}, new_id("readybase"))
    directory = run.client.get(
        "readiness-baselines/" + baseline["readiness_baseline_id"] + "/targets"
    )
    run.arm(
        "events-received-before-parse",
        "events_persisted_before_reconciliation",
        require_events=True,
    )
    stopped = run.stop(
        reason="Enable real upstream change following for the isolated scoped generation"
    )
    config = run.config.read_text("utf-8").replace("auto_sync = false", "auto_sync = true")
    run.config.write_text(config, encoding="utf-8")
    run.start()
    marker = run.marker("events-received-before-parse")
    page = marker["claimed_job"]["page"]
    store = run.store()
    generation = initial["active_generation"]
    received = store.events_after(0, limit=10000)
    row = store.get("generation", generation)
    assert received and row["parsed_sequence"] < received[-1]["sequence"]
    assert store.meta("received_cursor") == page["next_cursor"]
    count_before = len(received)
    store.receive_events(page)
    count_after = len(store.events_after(0, limit=10000))
    assert count_after == count_before
    run.disarm()
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        row = store.get("generation", generation)
        jobs = [
            j
            for j in store.jobs(limit=10000)
            if j["execution_state"] in ("queued", "running", "cancel_requested")
        ]
        if (
            row["parsed_sequence"] >= received[-1]["sequence"]
            and not row.get("reconciliation_pending")
            and not jobs
        ):
            break
        time.sleep(2)
    else:
        raise TimeoutError("Received events did not finish scoped reconciliation")
    assert row["scope"] == run.scope
    resolutions = [
        r["body"]
        for r in store.find("event_resolution", limit=10000)
        if r["id"].startswith(generation + "|")
    ]
    indexed = {r["change_event_id"]: r for r in resolutions}
    for event in received:
        body = event["body"]
        assert indexed[body["change_event_id"]]["effects"] == body["payload"].get(
            "effects", [body["payload"]]
        )
    after = run.client.get("readiness-changes", after=baseline["baseline_change_cursor"], limit=200)
    repeated = run.client.get(
        "readiness-changes", after=baseline["baseline_change_cursor"], limit=200
    )
    assert after["items"] == repeated["items"]
    assert len({i["event_id"] for i in after["items"]}) == len(after["items"])
    fixed_directory = run.client.get(
        "readiness-baselines/" + baseline["readiness_baseline_id"] + "/targets"
    )
    assert directory["items"] == fixed_directory["items"]
    status = run.client.post("index/status", {"scope": run.main, "budget": run.budget})
    assert status["ready_for_auto_card"]
    run.stop(reason="Return the isolated fixture to explicit scheduling after event verification")
    run.config.write_text(config.replace("auto_sync = true", "auto_sync = false"), encoding="utf-8")
    run.start()
    return run.finish(
        "07-events-and-readiness",
        {
            "initial": initial,
            "baseline": baseline,
            "directory": directory,
            "stop_to_enable_following": stopped,
            "received_before_parse": marker,
            "deduplication_counts": [count_before, count_after],
            "resolved_generation": row,
            "complete_event_effect_resolutions": resolutions,
            "readiness_changes": after,
            "replayed_changes_same_identity": True,
            "fixed_directory_after_events": fixed_directory,
            "ready": status,
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--fixture", default="state/engineering-protocol-20260908-v2/fixture.json")
    parser.add_argument("--groups", default="adoption,purpose_changes,events")
    args = parser.parse_args()
    run = Engineering(args.run_id, args.fixture)
    run.start()
    for group in args.groups.split(","):
        globals()[group](run)
    atomic_json(
        run.root / "changes-driver-completed.json",
        {"groups": args.groups.split(","), "state": "passed"},
    )
    run.client.close()


if __name__ == "__main__":
    main()
