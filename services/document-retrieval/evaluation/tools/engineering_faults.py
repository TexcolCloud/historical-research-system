"""Directed failures on real storage, including two actual process interruptions."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from engineering_support import MODULE, Engineering, digest, load
from run_quality import TraceClient

from document_retrieval.engine import OpenSearch
from document_retrieval.records import atomic_json, new_id, utc_now


def public_retry(run, job):
    return run.client.post(
        "jobs/" + job["job_id"] + "/retry",
        {
            "expected_execution_epoch": job["execution_epoch"],
            "expected_attempt_id": job["attempt_id"],
        },
        new_id("retry"),
    )


def partial_vectors(run):
    identity = "partial-vectors"
    run.arm(
        identity,
        "before_step",
        type="build",
        phase="vectors",
        payload_min={"vector_offset": 1},
        mode="error",
    )
    index = run.index(identity, scope=run.main, kind="repair", wait=False)
    marker = run.marker(identity)
    build = run.wait(marker["claimed_job"]["job_id"], allow_failed=True)
    assert build["execution_state"] == "failed"
    generation = marker["claimed_job"]["payload"]["generation_id"]
    binding = run.store().snapshot_binding(generation, run.main["snapshot_id"])
    assert 0 < binding["published_vector_records"] < binding["expected_vector_records"]
    assert binding["published_text_records"] == binding["expected_text_records"]
    assert not binding["ready_for_auto_card"] and not binding["build_complete"]
    status = run.client.post("index/status", {"scope": run.main, "budget": run.budget})
    assert not status["ready_for_auto_card"]
    lookup = run.search("ROW_TAIL_ZEBRA", mode="hybrid", purpose="source_reading")
    assert lookup["items"] and lookup["stages"]["vector"]["state"] == "partial"
    failed_parent = run.wait(index["job_id"], allow_failed=True)
    run.disarm()
    retried = public_retry(run, build)
    finished_build = run.wait(build["job_id"])
    assert finished_build["execution_epoch"] == build["execution_epoch"] + 1
    parent_retry = public_retry(run, failed_parent)
    run.wait(index["job_id"])
    restored = run.client.post("index/status", {"scope": run.main, "budget": run.budget})
    assert restored["ready_for_auto_card"]
    stale = run.expect_problem(
        lambda: run.client.post(
            "jobs/" + build["job_id"] + "/cancel",
            {
                "expected_execution_epoch": build["execution_epoch"],
                "expected_attempt_id": build["attempt_id"],
            },
            new_id("stale"),
        ),
        {"job_control_conflict"},
    )
    return run.finish(
        "08a-partial-vectors",
        {
            "fault": marker,
            "binding": binding,
            "status": status,
            "partial_query": lookup,
            "retried": retried,
            "parent_retry": parent_retry,
            "completed_build": finished_build,
            "restored_status": restored,
            "stale_control": stale,
        },
    )


def engine_kill(run):
    identity = "engine-written-before-publish"
    run.arm(identity, "engine_verified_before_publish", type="build", phase="text_publish")
    request = run.index(identity, scope=run.main, kind="repair", wait=False)
    marker = run.marker(identity)
    claimed = marker["claimed_job"]
    p = claimed["payload"]
    engine = OpenSearch(run.settings)
    count = engine.count_projection(p["generation_id"], p["text_projection"])
    engine.close()
    assert count == p["expected_text_records"] and count > 0
    before = run.store().snapshot_binding(p["generation_id"], p["snapshot_id"])
    assert before is None or before["build_id"] != p["build_id"]
    fixed_status = run.client.post("index/status", {"scope": run.main, "budget": run.budget})
    status = run.client.post("index/status", {"scope": run.current, "budget": run.budget})
    assert not status["ready_for_auto_card"]
    killed = run.stop(
        reason="Actual termination after engine verification and before control publication"
    )
    assert killed["actual_terminated"]
    run.save(
        "engine-kill-receipt",
        {"marker": marker, "before": before, "engine_records": count, "termination": killed},
    )
    run.disarm()
    run.start()
    completed = run.wait(request["job_id"])
    after = run.store().snapshot_binding(p["generation_id"], p["snapshot_id"])
    assert after["build_id"] == p["build_id"] and after["build_complete"]
    new_attempt = run.store().job(claimed["job_id"])
    assert new_attempt["attempt_id"] != claimed["attempt_id"]
    revision = run.store().revision()
    stale = run.expect_problem(
        lambda: run.store().publish(
            claimed, p["generation_id"], p["target_key"], p["snapshot_id"], after
        ),
        {"stale_attempt"},
    )
    assert run.store().revision() == revision
    return run.finish(
        "08b-engine-kill",
        {
            "marker": marker,
            "before_status": status,
            "older_fixed_projection_status": fixed_status,
            "engine_records_before_publication": count,
            "actual_termination": killed,
            "completed": completed,
            "after_binding": after,
            "late_old_attempt": stale,
        },
    )


def reply_kill(run):
    identity = "committed-reply-not-delivered"
    run.arm(identity, "response_committed_before_delivery", type="http_response")
    body = {
        "query": "甲𠀀乙",
        "mode": "exact_quote",
        "scope": run.main,
        "purpose": "card_input",
        "budget": run.budget,
    }
    key = run.run_id + ":" + identity
    client = TraceClient(run.url, run.root / "lost-reply-http")

    def request():
        return client.completed(client.post("searches", body, key), run.budget)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(request)
        marker = run.marker(identity)
        assert not pending.done(), "The client received a reply before interruption"
        listing = run.store().get("list", marker["claimed_job"]["list_id"])
        assert listing["evidence_refs"]
        killed = run.stop(
            reason="Actual termination after list commit but before HTTP reply delivery"
        )
        try:
            pending.result(timeout=30)
        except Exception as error:  # noqa: BLE001 -- record the actual transport failure.
            transport = {"exception": type(error).__name__, "detail": str(error)}
        else:
            raise AssertionError("The deliberately undelivered reply unexpectedly arrived")
    client.close()
    run.save(
        "reply-kill-receipt",
        {"marker": marker, "listing": listing, "termination": killed, "transport": transport},
    )
    run.disarm()
    run.start()
    replay = run.client.completed(run.client.post("searches", body, key), run.budget)
    assert replay["list_id"] == listing["list_id"]
    assert replay["items"][0]["evidence_ref"] == listing["evidence_refs"][0]
    read = run.read(replay["items"][0])
    assert "甲𠀀乙" in read["units"][0]["text"]
    return run.finish(
        "08c-reply-kill",
        {
            "marker": marker,
            "transport": transport,
            "actual_termination": killed,
            "committed_list": listing,
            "same_key_replay": replay,
            "original_read": read,
        },
    )


def stage_errors(run):
    observations = {}
    for name, point in (("vector", "before_model_embed"), ("rerank", "before_model_rerank")):
        run.arm(
            new_id("stage-" + name),
            point,
            type="search",
            mode="error",
            error_code="evaluation_" + name + "_unavailable",
        )
        result = run.search("BODY_NOTE_TARGET 37 engineering units", mode="hybrid")
        assert result["stages"][name]["state"] == "failed" and result["items"]
        run.disarm()
        same = run.client.post(f"searches/{result['list_id']}/results", {"budget": run.budget})
        assert same["stages"] == result["stages"]
        fresh = run.search("BODY_NOTE_TARGET 37 engineering units", mode="hybrid")
        assert fresh["stages"][name]["state"] == "complete"
        observations[name] = {"partial": result, "fixed_replay": same, "new_query": fresh}
    return run.finish("06a-stage-errors", observations)


def engine_missing(run):
    preparation = None
    initial = run.client.post("index/status", {"scope": run.main, "budget": run.budget})
    if not initial["ready_for_auto_card"]:
        preparation = run.index(new_id("prepare-missing-record"), scope=run.main, kind="repair")
        usable = run.client.post("index/status", {"scope": run.main, "budget": run.budget})
        run.save(
            "missing-record-prior-attempt-recovery",
            {
                "before": initial,
                "repair": preparation,
                "after": usable,
            },
        )
        assert usable["ready_for_auto_card"]
    status = run.client.get("management/status")
    generation = status["active_generation"]
    binding = run.store().snapshot_binding(generation, run.main["snapshot_id"])
    records = load(run.settings.state_root / binding["records_ref"]["path"])
    record = records[0]
    stop = run.stop(
        reason="Stop the isolated instance before a deliberate single-record engine fault"
    )
    engine = OpenSearch(run.settings)
    removed = engine.request(
        "DELETE", f"{engine.index_name(generation)}/_doc/{record['id']}"
    ).json()
    engine.refresh(generation)
    engine.close()
    run.start()
    verification = run.store().meta("startup_verification")
    assert any(b["build_id"] == binding["build_id"] for b in verification["failed_builds"])
    before = run.client.post("index/status", {"scope": run.main, "budget": run.budget})
    assert not before["ready_for_auto_card"]
    repair = run.index(new_id("repair-missing-record"), scope=run.main, kind="repair")
    after = run.client.post("index/status", {"scope": run.main, "budget": run.budget})
    assert after["ready_for_auto_card"]
    return run.finish(
        "08d-missing-engine-record",
        {
            "prior_attempt_recovery": preparation,
            "stop": stop,
            "removed": removed,
            "record": record,
            "startup_verification": verification,
            "before": before,
            "repair": repair,
            "after": after,
        },
    )


def retention(run):
    run.clock(0)
    main = run.client.post(
        "retentions",
        {"scope": run.main, "caller_task_ref": run.run_id + ":retention-main"},
        new_id("main"),
    )
    other = run.client.post(
        "retentions",
        {"scope": run.other, "caller_task_ref": run.run_id + ":retention-supplement"},
        new_id("supplement"),
    )
    result = run.search("甲𠀀乙", retention_id=main["retention_id"])
    extra = run.search("VERSION_TWO_ONLY", scope=run.other, retention_id=other["retention_id"])
    main_bound = run.client.get("retentions/" + main["retention_id"])
    other_bound = run.client.get("retentions/" + other["retention_id"])
    assert main_bound["list_ids"] == [result["list_id"]] and other_bound["list_ids"] == [
        extra["list_id"]
    ]
    independent = run.client.post(
        "retentions",
        {
            "scope": run.main,
            "caller_task_ref": run.run_id + ":other-dependent",
            "list_ids": [result["list_id"]],
        },
        new_id("dependency"),
    )
    ordinary = run.search("VERSION_ONE_ONLY")
    anchor = run.read(ordinary["items"][0])["units"][0]["source_anchor"]

    def expiry(kind, identity):
        with run.store().connect() as db:
            return db.execute(
                "SELECT expires FROM objects WHERE kind=? AND id=?", (kind, identity)
            ).fetchone()[0]

    before = expiry("list", ordinary["list_id"])
    run.clock(6 * 86400)
    run.client.get("searches/" + ordinary["list_id"])
    status_only = expiry("list", ordinary["list_id"])
    assert status_only == before
    run.client.post("searches/" + ordinary["list_id"] + "/results", {"budget": run.budget})
    renewed = expiry("list", ordinary["list_id"])
    assert renewed - before >= 6 * 86400 - 5
    released = run.client.post(
        "retentions/" + main["retention_id"] + "/release", {}, new_id("release")
    )
    release_expiry = expiry("retention", main["retention_id"])
    run.clock(12 * 86400)
    again = run.client.post(
        "retentions/" + main["retention_id"] + "/release", {}, new_id("release")
    )
    assert released["released_at"] == again["released_at"]
    assert expiry("retention", main["retention_id"]) == release_expiry
    run.clock(14 * 86400)
    still_bound = run.client.post(
        "searches/" + result["list_id"] + "/results", {"budget": run.budget}
    )
    assert still_bound["items"]
    expired = run.expect_problem(
        lambda: run.client.post(
            "searches/" + ordinary["list_id"] + "/results", {"budget": run.budget}
        ),
        {"reference_expired"},
    )
    recovered = run.client.post(
        "reads",
        {"selection": {"kind": "anchors", "source_anchors": [anchor]}, "purpose": "card_input"},
    )
    assert recovered["units"][0]["source_anchor"] == anchor
    cleanup = run.client.post("management/cleanup-jobs", {"dry_run": True}, new_id("cleanup"))
    cleanup_done = run.wait(cleanup["job_id"])
    assert run.store().live("list", result["list_id"])
    execute = run.client.post("management/cleanup-jobs", {"dry_run": False}, new_id("cleanup"))
    executed = run.wait(execute["job_id"])
    assert executed["progress"]["deleted"] > 0
    missing = run.expect_problem(
        lambda: run.client.get("searches/" + ordinary["list_id"]), {"reference_not_found"}
    )
    protected = run.client.post(
        "searches/" + result["list_id"] + "/results", {"budget": run.budget}
    )
    assert protected["items"]
    after_cleanup = run.client.post(
        "reads",
        {"selection": {"kind": "anchors", "source_anchors": [anchor]}, "purpose": "card_input"},
    )
    assert after_cleanup["units"][0]["source_anchor"] == anchor
    fresh = run.search("甲𠀀乙")
    assert fresh["items"]
    ready = run.client.post("index/status", {"scope": run.main, "budget": run.budget})
    assert ready["ready_for_auto_card"]
    run.clock(0)
    return run.finish(
        "10-retention",
        {
            "main": main_bound,
            "supplement": other_bound,
            "independent": independent,
            "ordinary_expiry": before,
            "status_expiry": status_only,
            "renewed_expiry": renewed,
            "released": released,
            "repeat_release": again,
            "release_expiry": release_expiry,
            "dependency_keeps_list": still_bound,
            "expired_list": expired,
            "full_anchor_read": recovered,
            "cleanup": cleanup_done,
            "executed_cleanup": executed,
            "expired_list_removed": missing,
            "dependent_list_after_cleanup": protected,
            "full_anchor_after_cleanup": after_cleanup,
            "new_query_after_cleanup": fresh,
            "readiness_after_cleanup": ready,
            "clock_kind": "simulated offsets; actual persistent object dependencies",
        },
    )


def paired_recovery(run):
    run.clock(0)
    original = run.search("甲𠀀乙")
    reading = run.read(original["items"][0])
    retained = run.client.post(
        "retentions",
        {
            "scope": run.main,
            "caller_task_ref": run.run_id + ":backup",
            "list_ids": [original["list_id"]],
        },
        new_id("backup"),
    )
    readiness = run.client.post("readiness-baselines", {}, new_id("baseline"))
    run.arm("maintenance-observation", "before_step", type="recovery_create", phase="quiesce")
    create = run.client.post(
        "management/recovery-packages", {"maintenance": True}, new_id("recovery")
    )
    marker = run.marker("maintenance-observation")
    denied = run.expect_problem(lambda: run.search("甲"), {"maintenance"})
    run.disarm()
    done = run.wait(create["job_id"])
    package = run.client.get("management/recovery-packages/" + done["result_ref"]["package_id"])
    manifest = load(package["manifest_path"])
    verify = run.client.post(
        "management/recovery-packages/" + package["package_id"] + "/verify", {}, new_id("verify")
    )
    verified = run.wait(verify["job_id"])
    later = run.search("VERSION_ONE_ONLY")
    saved = run.save(
        "paired-recovery-before",
        {
            "original": original,
            "reading": reading,
            "retention": retained,
            "readiness": readiness,
            "package": package,
            "manifest_sha256": digest(package["manifest_path"]),
            "later_result": later,
            "received_cursor": run.client.get("management/status")["received_cursor"],
        },
    )
    stopped = run.stop(reason="Required offline paired restore of the isolated retrieval module")
    negative_receipts = []
    for label in ("missing-stage", "mismatched-control"):
        folder = run.root / ("invalid-package-" + label)
        folder.mkdir(exist_ok=True)
        shutil.copy2(
            __import__("pathlib").Path(package["manifest_path"]).parent / "control.sqlite3",
            folder / "control.sqlite3",
        )
        invalid = dict(manifest)
        assert invalid["files"]
        if label == "mismatched-control":
            invalid["control_sha256"] = "0" * 64
        atomic_json(folder / "manifest.json", invalid)
        before_hash = digest(run.settings.state_root / "control.sqlite3")
        negative_command = [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "document_retrieval.cli",
            "--config",
            str(run.config),
            "recovery",
            "restore",
            str(folder / "manifest.json"),
        ]
        negative = subprocess.run(
            negative_command,
            cwd=MODULE,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert negative.returncode != 0
        assert digest(run.settings.state_root / "control.sqlite3") == before_hash
        expected_code = (
            "recovery_stage_mismatch" if label == "missing-stage" else "recovery_control_mismatch"
        )
        assert expected_code in negative.stderr + negative.stdout
        negative_receipts.append(
            {
                "case": label,
                "command": negative_command,
                "returncode": negative.returncode,
                "stdout": negative.stdout,
                "stderr": negative.stderr,
                "existing_control_unchanged_sha256": before_hash,
            }
        )
    output = run.root / "offline-restore-result.json"
    command = [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "document_retrieval.cli",
        "--config",
        str(run.config),
        "--output",
        str(output),
        "recovery",
        "restore",
        package["manifest_path"],
    ]
    process = subprocess.run(
        command, cwd=MODULE, capture_output=True, text=True, encoding="utf-8", check=False
    )
    assert process.returncode == 0, process.stderr
    receipt = load(output)
    run.start()
    replay = run.client.post("searches/" + original["list_id"] + "/results", {"budget": run.budget})
    replay_read = run.read(replay["items"][0])
    assert replay["items"] == original["items"]
    assert [u["source_anchor"] for u in replay_read["units"]] == [
        u["source_anchor"] for u in reading["units"]
    ]
    lost_later = run.expect_problem(
        lambda: run.client.get("searches/" + later["list_id"]), {"reference_not_found"}
    )
    old_cursor = run.expect_problem(
        lambda: run.client.get("readiness-changes", after=readiness["baseline_change_cursor"]),
        {"readiness_cursor_invalid"},
    )
    restored_retention = run.client.get("retentions/" + retained["retention_id"])
    assert restored_retention["list_ids"] == retained["list_ids"]
    status = run.client.post("index/status", {"scope": run.main, "budget": run.budget})
    assert status["ready_for_auto_card"]
    assert run.client.get("management/status")["received_cursor"] == manifest["received_cursor"]
    fresh = run.client.post("readiness-baselines", {}, new_id("fresh"))
    changes = run.client.get("readiness-changes", after=fresh["baseline_change_cursor"])
    external = run.settings.state_root / "recovery" / (receipt["restore_id"] + ".json")
    assert load(external) == receipt
    return run.finish(
        "11-paired-recovery",
        {
            "maintenance_marker": marker,
            "maintenance_rejection": denied,
            "verified": verified,
            "before": saved,
            "actual_stop": stopped,
            "incomplete_and_mismatched_package_rejections": negative_receipts,
            "offline_command": command,
            "offline_receipt": receipt,
            "external_receipt": {"path": str(external), "sha256": digest(external)},
            "replay": replay,
            "read": replay_read,
            "post_backup_list": lost_later,
            "old_consumer_cursor": old_cursor,
            "retention": restored_retention,
            "coverage": status,
            "fresh_baseline": fresh,
            "fresh_changes": changes,
            "finished_at": utc_now(),
            "restored_original_text_sha256": hashlib.sha256(
                json.dumps(replay_read["units"], ensure_ascii=False).encode()
            ).hexdigest(),
        },
    )


def maintenance_outcomes(run):
    observations = {}
    for mode in ("pause", "error"):
        identity = new_id("maintenance-" + mode)
        run.arm(identity, "before_step", type="recovery_create", phase="quiesce", mode=mode)
        receipt = run.client.post(
            "management/recovery-packages", {"maintenance": True}, new_id("recovery")
        )
        marker = run.marker(identity)
        if mode == "pause":
            job = run.client.get("jobs/" + receipt["job_id"])
            denied = run.expect_problem(lambda: run.search("甲"), {"maintenance"})
            cancellation = run.client.post(
                "jobs/" + job["job_id"] + "/cancel",
                {
                    "expected_execution_epoch": job["execution_epoch"],
                    "expected_attempt_id": job["attempt_id"],
                },
                new_id("cancel"),
            )
            run.disarm()
        else:
            denied, cancellation = None, None
            run.disarm()
        done = run.wait(receipt["job_id"], allow_failed=True)
        assert done["execution_state"] == ("cancelled" if mode == "pause" else "failed")
        assert not run.client.get("management/status")["maintenance"]
        package = run.client.get(
            "management/recovery-packages/" + marker["claimed_job"]["payload"]["package_id"]
        )
        assert package["state"] == "failed"
        if mode == "pause":
            assert package["issue"]["code"] == "recovery_creation_cancelled"
        usable = run.search("甲𠀀乙")
        assert usable["items"]
        observations[mode] = {
            "marker": marker,
            "maintenance_rejection": denied,
            "cancel_receipt": cancellation,
            "terminal_job": done,
            "package_receipt": package,
            "resumed_query": usable,
        }
    return run.finish("11a-maintenance-outcomes", observations)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--fixture", default="state/engineering-protocol-20260908-v2/fixture.json")
    parser.add_argument(
        "--groups",
        default="partial_vectors,engine_kill,reply_kill,stage_errors,engine_missing,retention,maintenance_outcomes,paired_recovery",
    )
    args = parser.parse_args()
    run = Engineering(args.run_id, args.fixture)
    run.start()
    for group in args.groups.split(","):
        try:
            globals()[group](run)
        except Exception as error:
            if group == "retention":
                run.clock(0)
            atomic_json(
                run.root / (group + "-failure-" + new_id("attempt") + ".json"),
                {
                    "at": utc_now(),
                    "exception": type(error).__name__,
                    "detail": str(error),
                    "classification": "unfinished_acceptance_not_a_pass",
                },
            )
            raise
    run.client.close()


if __name__ == "__main__":
    main()
