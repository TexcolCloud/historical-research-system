"""Cancellation, fixed generations and late work on real persistent inputs."""

import argparse

from engineering_changes import reading_operation
from engineering_faults import public_retry
from engineering_support import Engineering, load

from document_retrieval.records import new_id


def background_switch(run):
    old = run.search("VERSION_ONE_ONLY")
    old_generation = run.client.get("management/status")["active_generation"]
    old_config = run.store().get("generation", old_generation)["configuration_ref"]
    stopped = run.stop(
        reason="Register changed chunk configuration for an isolated background generation"
    )
    config = run.config.read_text("utf-8")
    assert "chunk_target" not in config
    run.config.write_text(
        config + "chunk_target = 512\nchunk_max = 1024\nchunk_overlap = 64\n", encoding="utf-8"
    )
    started = run.start()
    assert started["configuration_ref"] != old_config
    run.arm(
        "background-build-cancel",
        "before_step",
        type="build",
        phase="vectors",
        payload_min={"vector_offset": 2},
    )
    parent = run.index("background-build-cancel", activate=False, wait=False)
    marker = run.marker("background-build-cancel")
    job = marker["claimed_job"]
    generation = job["payload"]["generation_id"]
    assert generation != old_generation
    pending = run.client.post(
        "searches",
        {
            "query": "VERSION_ONE_ONLY",
            "mode": "exact_quote",
            "scope": run.main,
            "purpose": "card_input",
            "budget": run.budget,
        },
        run.run_id + ":queued-search",
    )
    assert pending["scope_state"] == "scope_pending" and not pending["scope_ref"]
    immediate = run.client.wait_job(pending["job_id"], timeout=0)
    assert immediate["execution_state"] in ("queued", "running")
    cancelled = run.client.post(
        "jobs/" + job["job_id"] + "/cancel",
        {
            "expected_execution_epoch": job["execution_epoch"],
            "expected_attempt_id": job["attempt_id"],
        },
        new_id("cancel"),
    )
    assert cancelled["job"]["execution_state"] == "cancel_requested"
    run.disarm()
    child_done = run.wait(job["job_id"], allow_failed=True)
    assert child_done["execution_state"] == "cancelled"
    parent_done = run.wait(parent["job_id"], allow_failed=True)
    assert parent_done["execution_state"] == "failed"
    assert any(i["code"] == "index_builds_failed" for i in parent_done["issues"])
    completed_search = run.wait(pending["job_id"])
    assert completed_search["scope_state"] == "fixed"
    queued_result = run.client.post("jobs/" + pending["job_id"] + "/result", {"budget": run.budget})
    assert run.store().get("scope", queued_result["scope_ref"])["generation_id"] == old_generation
    assert run.client.get("management/status")["active_generation"] == old_generation
    before_retry = run.store().snapshot_binding(generation, job["payload"]["snapshot_id"])
    assert before_retry["published_text_records"] > 0 and not before_retry["build_complete"]
    other = run.store().snapshot_binding(generation, run.other["snapshot_id"])
    assert other["ready_for_auto_card"], other
    early = run.client.post(
        "management/generations/" + generation + "/activate",
        {
            "expected_active_generation": old_generation,
        },
        new_id("early"),
    )
    early_done = run.wait(early["job_id"], allow_failed=True)
    assert early_done["execution_state"] == "failed"
    assert any(i["code"] == "generation_not_ready" for i in early_done["issues"])
    child_retry = public_retry(run, child_done)
    run.wait(job["job_id"])
    parent_retry = public_retry(run, parent_done)
    run.wait(parent["job_id"])
    assert run.client.get("management/status")["active_generation"] == old_generation
    run.arm("activation-after-watermark", "before_step", type="activate", phase="verify_members")
    activate = run.client.post(
        "management/generations/" + generation + "/activate",
        {
            "expected_active_generation": old_generation,
        },
        new_id("activate"),
    )
    boundary = run.marker("activation-after-watermark")
    assert boundary["claimed_job"]["payload"]["switch_cursor"] is not None
    assert run.client.get("management/status")["active_generation"] == old_generation
    run.disarm()
    activated = run.wait(activate["job_id"])
    assert run.client.get("management/status")["active_generation"] == generation
    replay = run.client.post("searches/" + old["list_id"] + "/results", {"budget": run.budget})
    assert run.store().get("scope", replay["scope_ref"])["generation_id"] == old_generation
    assert replay["items"] == old["items"]
    fresh = run.search("VERSION_ONE_ONLY")
    assert run.store().get("scope", fresh["scope_ref"])["generation_id"] == generation
    assert fresh["configuration_ref"] != old_config
    before_revision = run.store().revision()
    stale = run.expect_problem(
        lambda: run.store().publish(
            job,
            generation,
            job["payload"]["target_key"],
            job["payload"]["snapshot_id"],
            before_retry,
        ),
        {"stale_attempt"},
    )
    assert run.store().revision() == before_revision
    return run.finish(
        "09-background-switch",
        {
            "old_list": old,
            "configuration_restart": stopped,
            "new_start": started,
            "cancel_marker": marker,
            "cancel_receipt": cancelled,
            "cancelled_child": child_done,
            "failed_parent": parent_done,
            "queued_receipt": pending,
            "timeout_keeps_identity": immediate,
            "queued_result": queued_result,
            "published_text_preserved": before_retry,
            "other_source_already_ready": other,
            "early_activation_rejected": early_done,
            "child_retry": child_retry,
            "parent_retry": parent_retry,
            "switch_watermark": boundary,
            "activation": activated,
            "old_list_replay": replay,
            "new_list": fresh,
            "late_old_attempt": stale,
            "scheduler_and_real_corpus_overlap": "Recorded separately by the fixed real-source 60-request performance run.",
        },
    )


def late_input(run):
    old_generation = run.client.get("management/status")["active_generation"]
    run.arm(
        "superseded-input-build", "before_step", type="build", phase="text_publish", mode="error"
    )
    parent = run.index("superseded-input-build", scope=run.current, kind="baseline", wait=False)
    marker = run.marker("superseded-input-build")
    old_job = run.wait(marker["claimed_job"]["job_id"], allow_failed=True)
    assert old_job["execution_state"] == "failed"
    run.wait(parent["job_id"], allow_failed=True)
    run.disarm()
    changed = run.upstream_apply(
        "late-work-new-adoption", [reading_operation(run, [run.parts["unicode"]])]
    )
    replacement = run.index("late-work-replacement", scope=run.current, kind="baseline")
    current = run.search("甲𠀀乙", scope=run.current)
    assert current["items"]
    expected = run.store().get(
        "expected", old_generation + "|" + marker["claimed_job"]["payload"]["target_key"]
    )
    assert expected["snapshot_id"] != marker["claimed_job"]["payload"]["snapshot_id"]
    revision = run.store().revision()
    retry = public_retry(run, old_job)
    rejected = run.wait(old_job["job_id"], allow_failed=True)
    assert rejected["execution_state"] == "failed"
    assert any(i["code"] == "input_superseded" for i in rejected["issues"])
    assert run.store().revision() == revision
    after = run.search("甲𠀀乙", scope=run.current)
    assert (
        after["items"][0]["citation"]["snapshot_id"]
        == current["items"][0]["citation"]["snapshot_id"]
    )
    restored = run.upstream_apply(
        "late-work-restore-adoption",
        [reading_operation(run, [p for p in run.fixture["parts"] if p["id"] != "version-two"])],
    )
    restored_index = run.index("late-work-restored-index", scope=run.current, kind="baseline")
    return run.finish(
        "08e-late-input",
        {
            "old_failed_work": marker,
            "new_adoption": changed,
            "replacement": replacement,
            "new_current": current,
            "expected_new_input": expected,
            "retry_old_work": retry,
            "rejected_late_input": rejected,
            "current_after_rejection": after,
            "restored_adoption": restored,
            "restored_index": restored_index,
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--fixture", default="state/engineering-protocol-20260908-v2/fixture.json")
    parser.add_argument("--groups", default="background_switch,late_input")
    args = parser.parse_args()
    run = Engineering(args.run_id, args.fixture)
    run.start()
    assert load(run.control).get("clock_offset_seconds", 0) == 0
    for group in args.groups.split(","):
        globals()[group](run)
    run.client.close()


if __name__ == "__main__":
    main()
