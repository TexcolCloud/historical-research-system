"""Bounded question-driven retrieval client; deliberately never loads references.

Use score_quality.py only after this process has finished a run. Model variants
receive the same tasks and client policy. This is a tool-sequence evaluation, not
a completed generative card agent or independent blind historical review.
"""

import argparse
import hashlib
import json
import re
import time
from contextlib import closing
from pathlib import Path

from tokenizers import Tokenizer

from document_retrieval.client import AgentTools, RetrievalClient
from document_retrieval.errors import Problem
from document_retrieval.records import atomic_json, fingerprint, utc_now
from document_retrieval.settings import Settings

MODULE = Path(__file__).resolve().parents[2]
POLICY = """Use only the supplied question, fixed initial queries and source selectors,
and actual public tool responses. Search each requested source once. Read the leading
unseen content entry, preferring an actually returned table when the question asks
for tabular data, and content over headings unless a title or editorial is requested.
Read neighboring source text, including a selected whole table, with the service's
source and section boundaries. Continue incomplete reads. Follow declared dependency
links when the question or returned note markers call for them; never spend a call
on an explicitly undeclared relation. Use remaining returned entries before repeating
a query. If they are exhausted, the sole source may receive the full question as its
second query; otherwise continue its fixed result list when there is room to read it.
A literal task searches and reads its first hit. Reserve one expansion for required
media options, and record successful image bytes or the actual HTTP error separately.
Use at most two new searches, four list/read expansions and six content calls. Never
read reference files, original-stage evidence phrases, hidden candidates or upstream
databases. Stop at the call limit. Scoring is separate. This deterministic client is
not a generative card agent and does not establish an independent blind review.
"""


class TraceClient(RetrievalClient):
    def __init__(self, url, folder):
        super().__init__(url, timeout=300)
        self.folder = folder
        self.sequence = 0
        self.last_raw = ""
        self.action = "management"
        self.rows = []
        path = Settings.load(MODULE / "config/local.example.toml").model_path("BAAI/bge-m3")
        self.tokenizer = Tokenizer.from_file(str(path / "tokenizer.json"))
        self.http.event_hooks["response"] = [self.record]

    def record(self, response):
        if "json" not in response.headers.get("content-type", ""):
            return
        raw = response.read()
        value = raw.decode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        count = len(self.tokenizer.encode(value, add_special_tokens=False).ids)
        self.sequence += 1
        self.last_raw = value
        row = {
            "sequence": self.sequence,
            "action": self.action,
            "method": response.request.method,
            "url": str(response.request.url),
            "request_text": response.request.content.decode("utf-8"),
            "status": response.status_code,
            "response_text": value,
            "headers": dict(response.headers),
            "sha256": sha,
            "independent_proxy_tokens": count,
            "recorded_at": utc_now(),
        }
        atomic_json(self.folder / f"http-{self.sequence:05d}.json", row)
        self.rows.append(row)
        assert response.headers.get("X-Retrieval-Response-SHA256") == sha
        assert int(response.headers["X-Retrieval-Response-Tokens"]) == count
        assert response.headers["X-Token-Measurement"] == "proxy_estimate"

    def completed(self, value, budget):
        if not value.get("job_id"):
            return value
        job_id = value["job_id"]
        started = time.monotonic()
        previous = self.action
        while True:
            self.action = "status_wait"
            value = self.get(f"jobs/{job_id}", wait_seconds=30)
            if value["execution_state"] in ("completed", "failed", "cancelled"):
                break
            if time.monotonic() - started > 900:
                raise Problem(
                    "evaluation_wait_timeout", "Preserve the existing job identity.", status=504
                )
        self.action = previous
        if value["execution_state"] != "completed":
            raise Problem(
                "evaluation_job_failed", json.dumps(value, ensure_ascii=False), status=409
            )
        return self.post(f"jobs/{job_id}/result", {"budget": budget})


def text_of(readings):
    return "\n".join(
        unit.get("text", "") for reading in readings for unit in reading.get("units", [])
    )


def already_read(item, readings):
    excerpt = re.sub(r"\s+", "", item.get("excerpt", ""))[:80]
    actual = re.sub(r"\s+", "", text_of(readings))
    return len(excerpt) >= 12 and excerpt in actual


def evaluate_task(task, args, folder):
    folder.mkdir(parents=True, exist_ok=True)
    client = TraceClient(args.url, folder / "http")
    agent = AgentTools(client)
    start = time.monotonic()
    search_budget = {
        "max_items": 6,
        "max_excerpt_tokens": args.excerpt,
        "max_response_tokens": args.response,
        "counter_ref": "bge-m3-proxy-v1",
    }
    read_budget = {
        "max_items": 6,
        "max_excerpt_tokens": 4096,
        "max_response_tokens": args.read,
        "counter_ref": "bge-m3-proxy-v1",
    }
    output = {
        "task_id": task["id"],
        "started_at": utc_now(),
        "searches": [],
        "reads": [],
        "retentions": [],
        "media": [],
        "decisions": [],
        "errors": [],
        "new_searches": 0,
        "expansions": 0,
        "driver": "deterministic_question_and_public_response_policy_v3",
    }
    pending, related, selected = [], [], []
    expanded, related_done, used_cursors = set(), set(), set()
    retained_by_family = {}
    expansion_limit = 3 if task["requires_media"] else 4
    seeks_heading = bool(re.search("标题|题名|社论", task["question"]))
    seeks_table = bool(re.search("统计|表头|表格|数据|比例|总量", task["question"]))
    needs_notes = bool(re.search("注释|出处|来源|统计|表头|口径", task["question"]))

    def fresh_items(result):
        unseen = [
            item
            for item in result.get("items", [])
            if item["evidence_ref"] not in expanded and not already_read(item, output["reads"])
        ]
        if task["type"] != "literal" and not seeks_heading:
            unseen.sort(
                key=lambda item: (
                    item["kind"] == "heading",
                    seeks_table and item["kind"] != "table",
                )
            )
        return unseen

    def invoke(name, body, action, key=None):
        client.action = action
        raw = agent.invoke(name, body, operation_key=key)
        assert raw == client.last_raw, "Python Agent tool text differs from actual HTTP bytes"
        return json.loads(raw)

    def search(group, query, basis):
        number = output["new_searches"]
        output["new_searches"] += 1
        if task["requires_media"] and number == 0:
            query += " 合影 图注"
        body = {
            "kind": "search",
            "query": query,
            "mode": task["mode"],
            "purpose": task["purpose"],
            "scope": group["scope"],
            "intent": "supplemental" if len(group["scope"]["sources"]) > 1 else "within_source",
            "budget": search_budget,
            "retention_id": retained_by_family[group["family"]],
        }
        if args.no_rerank:
            body["rerank"] = False
        output["decisions"].append(
            {"kind": "query", "family": group["family"], "query": query, "basis": basis}
        )
        initial = invoke(
            "search_evidence",
            body,
            "search",
            args.run_id + ":" + task["id"] + ":search:" + str(number),
        )
        result = client.completed(initial, search_budget)
        output["searches"].append({"family": group["family"], "query": query, **result})
        return result

    def read_item(item, layer, *, purpose=None):
        assert output["expansions"] < 4
        output["expansions"] += 1
        result = invoke(
            "read_evidence",
            {
                "selection": {"kind": "evidence", "evidence_ref": item["evidence_ref"]},
                "purpose": purpose or task["purpose"],
                "layer": layer,
                "budget": read_budget,
            },
            "read:" + layer,
        )
        output["reads"].append({"layer": layer, "evidence_ref": item["evidence_ref"], **result})
        output["decisions"].append(
            {
                "kind": "expand",
                "layer": layer,
                "evidence_ref": item["evidence_ref"],
                "basis": "actual_returned_content_and_declared_links",
            }
        )
        if result.get("read_cursor"):
            pending.append(result["read_cursor"])
        if layer in ("neighbors", "passage"):
            expanded.add(item["evidence_ref"])
            selected.append(item)
            if (
                task["type"] != "literal"
                and not task["requires_media"]
                and item.get("read", {}).get("related") == "declared_dependencies"
                and (needs_notes or re.search(r"[①-⑳]|\[\d{1,3}\]", text_of([result])))
            ):
                related.append(item)
        return result

    def continue_read():
        cursor = pending.pop(0)
        output["expansions"] += 1
        result = invoke(
            "read_evidence",
            {
                "selection": {"kind": "continue", "read_cursor": cursor},
                "budget": read_budget,
            },
            "read:continue",
        )
        output["reads"].append({"layer": "continued", **result})
        output["decisions"].append({"kind": "continue_read", "basis": "previous_read_incomplete"})
        if result.get("read_cursor"):
            pending.insert(0, result["read_cursor"])

    try:
        scopes = task["source_scopes"]
        for group in scopes:
            retained = invoke(
                "retain_inputs",
                {
                    "kind": "create",
                    "scope": group["scope"],
                    "caller_task_ref": args.run_id + ":" + task["id"] + ":" + group["family"],
                },
                "retention",
                args.run_id + ":" + task["id"] + ":retain:" + group["family"],
            )
            output["retentions"].append(retained)
            retained_by_family[group["family"]] = retained["retention_id"]
        for group in scopes[:2]:
            result = search(
                group,
                group.get("query", task["query"]),
                "fixed_source_question" if "query" in group else "fixed_task_query",
            )
            fresh = fresh_items(result)
            if fresh and output["expansions"] < expansion_limit:
                read_item(fresh[0], "passage" if task["type"] == "literal" else "neighbors")
        while task["type"] != "literal" and output["expansions"] < expansion_limit:
            if pending:
                continue_read()
                continue
            relation = next(
                (item for item in related if item["evidence_ref"] not in related_done), None
            )
            if relation:
                related_done.add(relation["evidence_ref"])
                read_item(relation, "related")
                continue
            candidates = [item for result in output["searches"] for item in fresh_items(result)]
            if candidates:
                read_item(candidates[0], "neighbors")
                continue
            if len(scopes) == 1 and output["new_searches"] < 2:
                search(
                    scopes[0], task["question"], "task_question_after_returned_content_exhausted"
                )
                continue
            listing = next(
                (
                    result
                    for result in output["searches"]
                    if result.get("next_cursor") and result["next_cursor"] not in used_cursors
                ),
                None,
            )
            if not listing or output["expansions"] >= expansion_limit - 1:
                break
            cursor = listing["next_cursor"]
            used_cursors.add(cursor)
            output["expansions"] += 1
            more = invoke(
                "search_evidence",
                {
                    "kind": "continue",
                    "list_id": listing["list_id"],
                    "cursor": cursor,
                    "budget": search_budget,
                },
                "list_continue",
            )
            output["searches"].append(
                {"family": listing["family"], "continued": True, "consumed_cursor": cursor, **more}
            )
        if task["requires_media"] and selected and output["expansions"] < 4:
            item = next(
                (item for item in selected if "合影" in item.get("excerpt", "")), selected[0]
            )
            options = read_item(item, "media", purpose="source_reading")
            assets = [
                asset for unit in options.get("units", []) for asset in unit.get("assets", [])
            ]
            candidates = [asset for asset in assets if asset.get("kind") == "page_image"]
            if not candidates:
                candidates = [
                    asset
                    for asset in assets
                    if str(asset.get("mime_type", "")).startswith("image/")
                ]
            if candidates:
                asset = candidates[0]
                client.action = "media_bytes"
                with client.media(
                    item["evidence_ref"], asset["asset_id"], purpose="source_reading"
                ) as response:
                    payload = b"".join(response.iter_bytes())
                    mime = response.headers.get("content-type", "").split(";", 1)[0]
                    ok = response.status_code == 200 and mime.startswith("image/")
                    suffix = ".png" if mime == "image/png" else ".bin"
                    target = folder / (("returned-media" if ok else "media-error") + suffix)
                    target.write_bytes(payload)
                    output["media"].append(
                        {
                            "evidence_ref": item["evidence_ref"],
                            "asset": asset,
                            "http_status": response.status_code,
                            "headers": dict(response.headers),
                            "path": str(target),
                            "bytes": len(payload),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "visual_review": "pending_active_GPT_review"
                            if ok
                            else "no_image_returned",
                        }
                    )
                    if not ok:
                        output["errors"].append(
                            {
                                "phase": "media",
                                "status": response.status_code,
                                "detail": "Requested source image was not returned; the exact response body is preserved.",
                            }
                        )
    except Exception as error:  # noqa: BLE001 -- preserve the full trace of every failed task.
        output["errors"].append({"type": type(error).__name__, "detail": str(error)})
    finally:
        for retained in output["retentions"]:
            try:
                invoke(
                    "release_inputs",
                    {"retention_id": retained["retention_id"]},
                    "release",
                    args.run_id + ":" + task["id"] + ":release:" + retained["retention_id"],
                )
            except Exception as error:  # noqa: BLE001
                output["errors"].append(
                    {"phase": "release", "type": type(error).__name__, "detail": str(error)}
                )
        output.update(
            {
                "completed_at": utc_now(),
                "seconds": time.monotonic() - start,
                "content_calls": output["new_searches"] + output["expansions"],
                "http_responses": len(client.rows),
                "tool_text_proxy_tokens": sum(
                    row["independent_proxy_tokens"] for row in client.rows
                ),
                "tool_utf8_bytes": sum(
                    len(row["response_text"].encode("utf-8")) for row in client.rows
                ),
            }
        )
        atomic_json(folder / "result.json", output)
        client.close()
    assert (
        output["new_searches"] <= 2 and output["expansions"] <= 4 and output["content_calls"] <= 6
    )
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=MODULE / "evaluation/datasets/retrieval-representative-20260908-v6",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--task-ids", nargs="+", help="Explicit diagnostic subset; omitted for a complete split."
    )
    parser.add_argument("--split", choices=("calibration", "holdout"), default="calibration")
    parser.add_argument("--url", default="http://127.0.0.1:18130")
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--excerpt", type=int, default=128)
    parser.add_argument("--response", type=int, default=2000)
    parser.add_argument("--read", type=int, default=4000)
    args = parser.parse_args()
    lock = json.loads((args.dataset / "lock.json").read_text("utf-8"))
    task_path = args.dataset / "tasks.json"
    assert hashlib.sha256(task_path.read_bytes()).hexdigest() == lock["files"]["tasks.json"]
    tasks = [t for t in json.loads(task_path.read_text("utf-8")) if t["split"] == args.split]
    if args.task_ids:
        assert set(args.task_ids) <= {t["id"] for t in tasks}
        tasks = [t for t in tasks if t["id"] in args.task_ids]
    folder = MODULE / "evaluation/runs" / args.run_id
    folder.mkdir(parents=True, exist_ok=True)
    assert not (folder / "aborted.json").exists(), "An aborted attempt requires a new run ID"
    with closing(RetrievalClient(args.url, timeout=300)) as setup:
        status = setup.get("management/status")
        generation = setup.get("management/generations/" + status["active_generation"])
    assert generation["state"] == "active" and generation["baseline_complete"]
    assert generation["configuration_ref"] == status["configuration_ref"]
    manifest = {
        "run_id": args.run_id,
        "dataset_id": lock["dataset_id"],
        "split": args.split,
        "task_scope": "explicit_diagnostic_subset" if args.task_ids else "complete_split",
        "selected_task_ids": [task["id"] for task in tasks],
        "tasks_sha256": lock["files"]["tasks.json"],
        "policy": POLICY,
        "generative_driver_model": None,
        "reference_files_accessed_by_driver": False,
        "host_model_prompt_tokens": "not_applicable_no_generative_driver_model",
        "budget": {"excerpt": args.excerpt, "response": args.response, "read": args.read},
        "no_rerank": args.no_rerank,
        "started_at": utc_now(),
        "source_sha256": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (MODULE / "src").rglob("*.py")
        },
        "active_generation_at_start": generation,
        "service_status_at_start": status,
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    if not (folder / "run.json").exists():
        atomic_json(folder / "run.json", manifest)
    else:
        previous = json.loads((folder / "run.json").read_text("utf-8"))
        assert (
            previous["driver_sha256"] == manifest["driver_sha256"]
            and previous["source_sha256"] == manifest["source_sha256"]
        )
    progress = []
    for task in tasks:
        target = folder / task["id"]
        if (target / "result.json").exists():
            result = json.loads((target / "result.json").read_text("utf-8"))
        else:
            result = evaluate_task(task, args, target)
        row = {
            k: result[k]
            for k in (
                "task_id",
                "seconds",
                "content_calls",
                "http_responses",
                "tool_text_proxy_tokens",
                "errors",
            )
        }
        print(json.dumps(row, ensure_ascii=False), flush=True)
        progress.append(row)
        atomic_json(folder / "progress.json", progress)
    atomic_json(
        folder / "completed.json",
        {
            "completed_at": utc_now(),
            "tasks": len(progress),
            "all_task_results_sha256": fingerprint(progress),
            "quality_scoring": "pending_separate_reference_comparison",
        },
    )


if __name__ == "__main__":
    main()
