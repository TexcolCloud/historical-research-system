"""Assemble final acceptance evidence only when every selected lane actually passed."""

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from document_ingestion.database import schema_head


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while part := stream.read(1024 * 1024):
            digest.update(part)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("evaluation/v1-20260907"))
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--baseline-source", type=Path)
    args = parser.parse_args()
    if bool(args.baseline) != bool(args.baseline_source):
        parser.error("--baseline and --baseline-source must be supplied together")
    module = Path(__file__).resolve().parents[2]
    root = (module / args.output).resolve()
    source_hashes = {
        path.relative_to(module).as_posix(): sha(path) for path in (module / "src").rglob("*.py")
    }
    packages = {
        name: importlib.metadata.version(name)
        for name in ("fastapi", "pydantic", "SQLAlchemy", "psycopg", "boto3", "uvicorn")
    }
    baseline_path = (module / args.baseline).resolve() if args.baseline else None
    carried = {}
    revalidation = None
    if baseline_path:
        assert root != baseline_path.parent, "Never overwrite the preserved baseline"
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        assert baseline["status"] == "first_version_machine_acceptance_complete"
        assert baseline["uv_lock_sha256"] == sha(module / "uv.lock")
        assert baseline["independent_tus_lock_sha256"] == sha(
            module / "evaluation/tools/package-lock.json"
        )
        assert baseline["runtime"]["packages"] == packages
        assert baseline["runtime"]["python"] == platform.python_version()
        previous_sources = baseline["source_files_sha256"]
        assert set(previous_sources) == set(source_hashes)
        changed = sorted(
            key for key in source_hashes if previous_sources[key] != source_hashes[key]
        )
        assert changed == ["src/document_ingestion/partitioning.py"], changed
        snapshot = (module / args.baseline_source).resolve()
        for name, expected in previous_sources.items():
            assert sha(snapshot / Path(name).relative_to("src")) == expected, name

        # Reuse only transport and real-provider evidence, never changed candidate behavior.
        allowed_changes = {"partition_group", "non_item_role", "run_partition"}

        def unchanged_statements(path):
            return [
                ast.dump(node, include_attributes=False)
                for node in ast.parse(path.read_text(encoding="utf-8")).body
                if not (isinstance(node, ast.FunctionDef) and node.name in allowed_changes)
            ]

        assert unchanged_statements(snapshot / "document_ingestion/partitioning.py") == (
            unchanged_statements(module / changed[0])
        ), "A change outside rule generation requires fresh provider evidence"
        carried_names = [
            "large.xml",
            "live.xml",
            "live-deepseek.json",
            *[
                f"transport-{backend}-{size}.json"
                for backend in ("local", "s3")
                for size in ("small", "large")
            ],
        ]
        for name in carried_names:
            path = baseline_path.parent / name
            assert sha(path) == baseline["evidence_sha256"][name], name
            carried[name] = path
        revalidation = {
            "baseline": baseline_path.relative_to(module).as_posix(),
            "baseline_sha256": sha(baseline_path),
            "changed_source_files": changed,
            "allowed_changed_symbols": sorted(allowed_changes),
            "freshly_executed": ["core", "linux-local", "linux-s3", "original-first GPT review"],
            "carried_forward_not_rerun": list(carried),
            "reuse_basis": "All other source files, provider-call statements, Python/dependency versions and both lockfiles match the hash-verified baseline; current core and Linux workflows exercise the updated partitioning.",
        }

    def read(name):
        return json.loads(carried.get(name, root / name).read_text(encoding="utf-8"))

    if baseline_path:
        execution = read("core-runtime.json")
        assert execution["exit_code"] == 0 and execution["sources_unchanged_during_run"]
        assert execution["source_files_sha256"] == source_hashes
        red = ET.parse(root / "ownership-baseline-red.xml").getroot().find("testsuite")
        green = ET.parse(root / "ownership-green.xml").getroot().find("testsuite")
        assert red is not None and green is not None
        assert {
            key: int(red.attrib[key]) for key in ("tests", "failures", "errors", "skipped")
        } == {"tests": 8, "failures": 6, "errors": 0, "skipped": 0}
        assert int(green.attrib["tests"]) == 17 and not any(
            int(green.attrib[key]) for key in ("failures", "errors", "skipped")
        )
        revalidation["test_sensitivity"] = (
            "Old image source: 6 expected failures and 2 compatibility passes; current code: all 17 focused checks pass (also included in core)."
        )

    lanes = {}
    for lane in ("core", "large", "live"):
        path = carried.get(lane + ".xml", root / (lane + ".xml"))
        suite = ET.parse(path).getroot().find("testsuite")
        assert suite is not None
        counts = {key: int(suite.attrib[key]) for key in ("tests", "failures", "errors", "skipped")}
        assert counts["tests"] and not any(
            counts[key] for key in ("failures", "errors", "skipped")
        ), (lane, counts)
        lanes[lane] = {**counts, "seconds": float(suite.attrib["time"]), "sha256": sha(path)}
        if baseline_path:
            lanes[lane].update(
                evidence_status="carried_forward_not_rerun" if lane != "core" else "freshly_passed",
                path=path.relative_to(module).as_posix(),
            )

    image_id = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            "historical-document-ingestion:acceptance-v1",
            "--format",
            "{{.Id}}",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    image_sources = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "python",
            image_id,
            "-c",
            "import hashlib,json,pathlib; root=pathlib.Path('/app'); print(json.dumps({p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'src').rglob('*.py')}))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(image_sources.stdout) == source_hashes, (
        "Linux image differs from current source"
    )
    linux = {}
    for backend in ("local", "s3"):
        runtime = read(f"linux-{backend}/runtime.json")
        pipeline = read(f"linux-{backend}/pipeline.json")
        assert runtime["outcome"] == "passed" and runtime["image_id"] == image_id
        assert pipeline["restart_readback"] == "passed"
        linux[backend] = {
            "outcome": "passed",
            "image_id": image_id,
            "migration": schema_head(),
            "pipeline_sha256": sha(root / f"linux-{backend}/pipeline.json"),
        }

    transport = {}
    for backend in ("local", "s3"):
        report = read(f"transport-{backend}-large.json")
        assert report["memory_measurement"] == "sum_process_tree_peak_working_sets_v1"
        assert report["package"]["member_bytes"] > 2**32 and report["package"]["zip_bytes"] > 2**32
        assert report["paused"]["accepted"] > 2**32
        assert report["preparation"]["execution_state"] == "completed"
        if backend == "s3":
            assert (
                report["s3_volume_restart"]["full_reverification"]["execution_state"] == "completed"
            )
        transport[backend] = {
            key: report[key]
            for key in (
                "memory_measurement",
                "http_peak_rss_bytes",
                "worker_peak_rss_bytes",
                "preparation_seconds",
                "total_seconds",
                "storage_range_reads",
            )
        }
        transport[backend]["package"] = report["package"]

    samples = {}
    for sample in ("five-page", "nine-page-artifacts"):
        for backend in ("local", "s3"):
            report = read(f"real-{sample}-{backend}.json")
            assert report["application"]["outcome"] == "succeeded"
            assert report["human_review"] is False and report["sealed_gold"] is False
            samples[sample + ":" + backend] = {
                "mapped_spans": report["mapped_spans"],
                "readiness": report["import"]["receipt"]["readiness_summary"],
                "images": report["import"]["receipt"]["image_count"],
                "table_evidence": report["import"]["receipt"]["table_evidence_count"],
                "candidate_count": report["partition"]["candidate_count"],
                "pending_range_count": len(report["partition"]["unassigned_ranges"]),
            }

    review = read("gpt-original-first-review.json")
    assert not review["human_review"] and not review["sealed_gold"]
    if baseline_path:
        assert review["review_date"] >= "2026-09-08" and review["evidence_hashes"]
        for name, expected in review["evidence_hashes"].items():
            assert sha(module / name) == expected, name
    evidence = {
        path.relative_to(root).as_posix(): sha(path)
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in {".json", ".xml"}
        and path.name != "acceptance-summary.json"
        and "workflow" not in path.parts
    }
    evidence.update(
        {Path(os.path.relpath(path, root)).as_posix(): sha(path) for path in carried.values()}
    )
    source_digest = hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest()
    for documentation in [module / "README.md", *sorted((module / "docs").glob("*.md"))]:
        for match in re.finditer(r"\]\(([^)]+)\)", documentation.read_text(encoding="utf-8")):
            link = match.group(1).split("#")[0]
            if not link or "://" in link:
                continue
            assert (documentation.parent / link).exists(), (documentation, link)
    report = {
        "status": "first_version_machine_acceptance_complete",
        "generated_at": datetime.now(UTC).isoformat(),
        "user_final_acceptance": "pending",
        "human_review": False,
        "sealed_gold": False,
        "schema_head": schema_head(),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": packages,
        },
        "lanes": lanes,
        "linux": linux,
        "transport": transport,
        "real_sources": samples,
        "source_code_sha256": source_digest,
        "source_files_sha256": source_hashes,
        "verification_files_sha256": {
            path.relative_to(module).as_posix(): sha(path)
            for directory in (module / "tests", module / "evaluation/tools")
            for path in directory.glob("*.py")
        },
        "uv_lock_sha256": sha(module / "uv.lock"),
        "independent_tus_lock_sha256": sha(module / "evaluation/tools/package-lock.json"),
        "evidence_sha256": evidence,
        "known_warnings": [
            "Starlette httpx compatibility path deprecation",
            "AnyIO BlockingPortal alias deprecation",
        ],
        "original_first_review": {
            "path": "gpt-original-first-review.json",
            "verdict": review["verdict"],
            "bibliographic_holds": [
                check.get("bibliographic_hold")
                for check in review["checks"]
                if check.get("bibliographic_hold")
            ],
        },
        "knowledge_closeout": {
            "code": "changed-and-verified",
            "runtime": "verified-current: Windows development and Linux acceptance only",
            "docs": "changed-and-verified",
            "rules": "verified-current: root AGENTS.md unchanged",
            "memory": "not-applicable: no authorized writes",
            "workspace": "retained-for-review: source uncommitted, evaluation files and caches retained",
        },
        "not_claimed": [
            "Public or cloud production deployment",
            "Project-wide boundary hardening",
            "Unified frontend",
            "OCR tuning or full historical-text correctness",
            "User-human approval or sealed gold",
        ],
        "historical_failures": [
            "large-transport.xml records a superseded run: one S3 worker restart rejected the earlier schema after a migration was added",
            "Linux replay initially rejected an existing engineering archive; subsequent runs use new test-owned directories",
        ],
    }
    if revalidation:
        report["integration_revalidation"] = revalidation
    (root / "acceptance-summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "tests": sum(lane["tests"] for lane in lanes.values()),
                "fresh_tests": lanes["core"]["tests"]
                if revalidation
                else sum(lane["tests"] for lane in lanes.values()),
                "carried_forward_tests": sum(lanes[name]["tests"] for name in ("large", "live"))
                if revalidation
                else 0,
                "linux_backends": list(linux),
                "source_code_sha256": source_digest,
            }
        )
    )


if __name__ == "__main__":
    main()
