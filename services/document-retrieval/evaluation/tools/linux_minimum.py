"""Actual Linux CPU service, real inputs, three clients, byte accounting and restart."""

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

from tokenizers import Tokenizer

from document_retrieval.client import AgentTools, RetrievalClient
from document_retrieval.records import atomic_json, fingerprint, new_id, text_hash, utc_now
from document_retrieval.settings import Settings

MODULE = Path(__file__).resolve().parents[2]
TASKS = [
    ("linux-01", "family-04", "exact_quote", "保卫中国同盟", ["保卫中国同盟"]),
    ("linux-02", "family-04", "compatible_text", "保衛中國同盟", ["保卫中国同盟"]),
    (
        "linux-03",
        "family-04",
        "hybrid",
        "宋庆龄 香港 八路军 新四军 援助",
        ["香港", "八路军", "新四军"],
    ),
    ("linux-04", "family-05", "exact_quote", "被服供应", ["被服供应"]),
    ("linux-05", "family-05", "compatible_text", "被服供應", ["被服供应"]),
    ("linux-06", "family-05", "hybrid", "第四师骑兵团 被服厂 家属 裁剪 缝纫机", ["刘凹", "缝纫机"]),
]


class RecordedClient(RetrievalClient):
    def __init__(self, folder):
        super().__init__("http://127.0.0.1:18132", timeout=180)
        self.folder, self.sequence, self.last_raw = folder, 0, ""
        self.tokenizer = Tokenizer.from_file(
            str(
                Settings.load(MODULE / "config/local.example.toml").model_path("BAAI/bge-m3")
                / "tokenizer.json"
            )
        )
        self.http.event_hooks["response"] = [self.record]

    def record(self, response):
        if "json" not in response.headers.get("content-type", ""):
            return
        raw = response.read()
        digest = hashlib.sha256(raw).hexdigest()
        decoded = raw.decode("utf-8")
        tokens = len(self.tokenizer.encode(decoded, add_special_tokens=False).ids)
        self.sequence += 1
        self.last_raw = decoded
        row = {
            "method": response.request.method,
            "url": str(response.request.url),
            "request_text": response.request.content.decode("utf-8"),
            "status": response.status_code,
            "headers": dict(response.headers),
            "response_text": decoded,
            "sha256": digest,
            "independent_proxy_tokens": tokens,
            "observed_at": utc_now(),
        }
        atomic_json(self.folder / f"http-{self.sequence:05d}.json", row)
        assert response.headers["X-Retrieval-Response-SHA256"] == digest
        assert int(response.headers["X-Retrieval-Response-Tokens"]) == tokens
        assert response.headers["X-Token-Measurement"] == "proxy_estimate"

    def completed(self, value):
        if value.get("job_id"):
            identity = value["job_id"]
            deadline = time.monotonic() + 600
            while True:
                job = self.get(f"jobs/{identity}")
                if job["execution_state"] in ("completed", "failed", "cancelled"):
                    break
                assert time.monotonic() < deadline, job
                time.sleep(0.5)
            assert job["execution_state"] == "completed", job
            value = self.post(f"jobs/{identity}/result", {})
        return value


def prepare(folder):
    tasks, references = [], []
    originals = json.loads(
        (MODULE / "evaluation/development/corpus-originals-rendered.json").read_text("utf-8")
    )
    for identity, family, mode, query, required in TASKS:
        source = MODULE / "state/real-corpus" / family
        role = json.loads((source / "research-role-v1/result.json").read_text("utf-8"))
        old = json.loads((source / "fixed-inputs.json").read_text("utf-8"))
        main = next(
            item
            for item in old
            if item["snapshot"].get("occurrence_id") == role["snapshot"]["occurrence_id"]
        )
        witnesses = []
        for needle in required:
            matches = [item for item in main["sources"] if needle in item["text"]]
            assert matches, (identity, needle)
            witnesses.append(
                {
                    "needle": needle,
                    "alternatives": [
                        {
                            "source_span_ref": item["source_span_ref"],
                            "start": item["start"],
                            "end": item["end"],
                            "text_sha256": item["text_sha256"],
                            "physical_page": item["physical_page"],
                        }
                        for item in matches
                    ],
                }
            )
        tasks.append(
            {
                "id": identity,
                "family": family,
                "request": {
                    "query": query,
                    "mode": mode,
                    "purpose": "source_reading",
                    "scope": {"kind": "snapshot", "snapshot_id": role["snapshot"]["snapshot_id"]},
                },
            }
        )
        references.append(
            {
                "id": identity,
                "required": required,
                "witnesses": witnesses,
                "source_evidence": next(item for item in originals if item["id"] == family),
                "role_review_sha256": role["role_review_sha256"],
            }
        )
    atomic_json(folder / "tasks.json", tasks)
    atomic_json(
        folder / "references.json",
        {
            "classification": "machine_reference",
            "scope": "Linux functional minimum, separate from held-out quality scoring",
            "reviewer": "active Codex GPT vision model",
            "original_first": True,
            "human_review": False,
            "sealed_gold": False,
            "tasks": references,
        },
    )
    atomic_json(
        folder / "prequery-lock.json",
        {
            "tasks_sha256": fingerprint(tasks),
            "references_sha256": hashlib.sha256(
                (folder / "references.json").read_bytes()
            ).hexdigest(),
            "locked_at": utc_now(),
            "retrieval_results_seen": False,
        },
    )


def run(folder):
    if not (folder / "prequery-lock.json").exists():
        prepare(folder)
    tasks = json.loads((folder / "tasks.json").read_text("utf-8"))
    references = json.loads((folder / "references.json").read_text("utf-8"))["tasks"]
    client = RecordedClient(folder / "run-trace")
    results = []
    try:
        capabilities = client.get("capabilities")
        atomic_json(folder / "capabilities.json", capabilities)
        for task in tasks:
            started = time.monotonic()
            result = client.completed(
                client.search(task["request"], key=folder.name + ":" + task["id"])
            )
            assert result["items"], result
            reading = client.read(
                {
                    "selection": {
                        "kind": "evidence",
                        "evidence_ref": result["items"][0]["evidence_ref"],
                    },
                    "purpose": "source_reading",
                }
            )
            for unit in reading["units"]:
                assert text_hash(unit["text"]) == unit["source_anchor"]["text_sha256"]
                assert (
                    len(unit["text"])
                    == unit["source_anchor"]["end"] - unit["source_anchor"]["start"]
                )
            actual = "\n".join(unit["text"] for unit in reading["units"])
            reference = next(item for item in references if item["id"] == task["id"])
            result_row = {
                "id": task["id"],
                "result": result,
                "reading": reading,
                "required_text_found": all(needle in actual for needle in reference["required"]),
                "seconds": time.monotonic() - started,
            }
            if task["request"]["mode"] == "hybrid":
                assert result["stages"]["vector"]["executed"]
                assert result["stages"]["rerank"]["executed"]
                assert result["coverage"]["published_vectors"] > 0
            atomic_json(folder / (task["id"] + ".json"), result_row)
            results.append(result_row)
            print(
                json.dumps(
                    {
                        "task": task["id"],
                        "evidence_found": result_row["required_text_found"],
                        "seconds": result_row["seconds"],
                    }
                ),
                flush=True,
            )

        first_task, first = tasks[0], results[0]
        tool = AgentTools(client)
        forwarded = tool.invoke(
            "search_evidence", {"kind": "continue", "list_id": first["result"]["list_id"]}
        )
        assert forwarded == client.last_raw
        assert (
            json.loads(forwarded)["items"][0]["evidence_ref"]
            == first["result"]["items"][0]["evidence_ref"]
        )
        cli_root = MODULE / "state/linux-acceptance/验收请求"
        cli_root.mkdir(exist_ok=True)
        atomic_json(cli_root / "精确查找.json", first_task["request"])
        completed = subprocess.run(
            [
                "docker",
                "exec",
                "historical-retrieval-linux-acceptance",
                "document-retrieval",
                "--wait",
                "--key",
                folder.name + ":" + first_task["id"],
                "--output",
                "/data/验收请求/结果.json",
                "search",
                "--request",
                "/data/验收请求/精确查找.json",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        atomic_json(
            folder / "native-cli.json",
            {
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            },
        )
        assert completed.returncode == 0, completed.stderr
        native = json.loads((cli_root / "结果.json").read_text("utf-8"))
        assert native["list_id"] == first["result"]["list_id"]
        assert native["items"][0]["evidence_ref"] == first["result"]["items"][0]["evidence_ref"]
        atomic_json(
            folder / "summary-before-restart.json",
            {
                "state": "queries_complete_media_and_restart_pending",
                "platform": "Linux Docker CPU FP32",
                "six_tasks_found": all(row["required_text_found"] for row in results),
                "source_families": ["family-04", "family-05"],
                "three_clients_agree": True,
                "python_tool_equals_http_bytes": True,
                "http_responses_counted": client.sequence,
                "old_list_id": first["result"]["list_id"],
                "old_evidence_ref": first["result"]["items"][0]["evidence_ref"],
                "source_anchor": first["reading"]["units"][0]["source_anchor"],
                "source_text_sha256": text_hash(first["reading"]["units"][0]["text"]),
            },
        )
    finally:
        client.close()


def verify_restart(folder):
    prior = json.loads((folder / "summary-before-restart.json").read_text("utf-8"))
    client = RecordedClient(folder / "restart-trace")
    try:
        result = client.post(f"searches/{prior['old_list_id']}/results", {})
        assert result["items"][0]["evidence_ref"] == prior["old_evidence_ref"]
        reading = client.read(
            {
                "selection": {"kind": "evidence", "evidence_ref": prior["old_evidence_ref"]},
                "purpose": "source_reading",
            }
        )
        assert reading["units"][0]["source_anchor"] == prior["source_anchor"]
        assert text_hash(reading["units"][0]["text"]) == prior["source_text_sha256"]
        atomic_json(
            folder / "actual-restart.json",
            {"state": "passed", "old_list": result, "reading": reading},
        )
        print(json.dumps({"restart": "passed", "list_id": prior["old_list_id"]}), flush=True)
    finally:
        client.close()


def verify_media(folder):
    client = RecordedClient(folder / new_id("media-trace"))
    try:
        prior = json.loads((folder / "linux-06.json").read_text("utf-8"))
        evidence_ref = prior["result"]["items"][0]["evidence_ref"]
        options = client.read(
            {
                "selection": {"kind": "evidence", "evidence_ref": evidence_ref},
                "layer": "media",
                "purpose": "source_reading",
            }
        )
        atomic_json(folder / "media-options-v2.json", options)
        results = []
        for asset in {
            asset["asset_id"]: asset
            for unit in options["units"]
            for asset in unit.get("assets", [])
        }.values():
            url = "http://127.0.0.1:18132" + asset["content_route"]
            with client.http.stream("GET", url) as response:
                if response.status_code != 200:
                    response.read()
                    results.append(
                        {
                            "asset_id": asset["asset_id"],
                            "status": response.status_code,
                            "detail": response.text,
                        }
                    )
                    continue
                digest, count, prefix = hashlib.sha256(), 0, b""
                for part in response.iter_bytes():
                    digest.update(part)
                    count += len(part)
                    if len(prefix) < 32:
                        prefix = (prefix + part)[:32]
                headers = dict(response.headers)
            assert digest.hexdigest() == asset["sha256"]
            assert count == asset["byte_length"]
            with client.http.stream("HEAD", url) as head:
                assert head.status_code == 200 and not head.read()
                assert int(head.headers["content-length"]) == count
            with client.http.stream(
                "GET", url, headers={"If-None-Match": headers["etag"]}
            ) as cached:
                assert cached.status_code == 304 and not cached.read()
            with client.http.stream("GET", url, headers={"Range": "bytes=0-31"}) as partial:
                assert partial.status_code == 206 and partial.read() == prefix
                assert partial.headers["content-range"] == f"bytes 0-31/{count}"
            results.append(
                {
                    "asset_id": asset["asset_id"],
                    "status": 200,
                    "streamed_sha256": digest.hexdigest(),
                    "bytes": count,
                    "headers": headers,
                    "head_empty": True,
                    "etag_304": True,
                    "range_206_bytes_match": True,
                }
            )
        assert any(row["status"] == 200 for row in results), results
        atomic_json(folder / "actual-media.json", {"state": "passed", "assets": results})
        print(
            json.dumps(
                {"media": "passed", "streamed_assets": sum(row["status"] == 200 for row in results)}
            ),
            flush=True,
        )
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("run", "verify-restart", "verify-media"))
    parser.add_argument("--run-id", default="linux-minimum-20260908-v1")
    args = parser.parse_args()
    folder = MODULE / "evaluation/runs" / args.run_id
    folder.mkdir(parents=True, exist_ok=True)
    {"run": run, "verify-restart": verify_restart, "verify-media": verify_media}[args.phase](folder)


if __name__ == "__main__":
    main()
