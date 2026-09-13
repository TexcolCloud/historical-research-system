"""Explicit, reproducible acceptance lanes; defaults never call a model or restart S3."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("lane", choices=["core", "large"])
    parser.add_argument("--restart-acceptance-s3", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("evaluation/v1-20260907"))
    args = parser.parse_args()
    module = Path(__file__).resolve().parents[2]
    output = (module / args.output).resolve()

    def sources():
        return {
            path.relative_to(module).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((module / "src").rglob("*.py"))
        }

    before = sources()
    started_at = datetime.now(UTC).isoformat()
    environment = {**os.environ, "INGEST_ACCEPTANCE_REPORT_DIR": str(output)}
    if args.lane == "core":
        # Real-source acceptance is deliberately limited to this machine, never cloud storage.
        destination = dotenv_values(module.parents[1] / ".env").get("INGEST_S3_ENDPOINT_URL", "")
        assert urlparse(destination).hostname in {"127.0.0.1", "localhost", "::1"}, (
            "Real-source acceptance requires a loopback S3 endpoint"
        )
        sample = json.loads((module / "evaluation/real-inputs.json").read_text(encoding="utf-8"))[
            "samples"
        ][0]
        environment.update(
            INGEST_REAL_ACCEPTANCE="1",
            INGEST_REAL_PACKAGE=str((module / sample["package"]).resolve()),
            INGEST_REAL_SOURCE=sample["original"],
        )
        selected = [
            "tests",
            "--ignore=tests/test_large_transport.py",
            "--ignore=tests/test_live_assistance.py",
        ]
    else:
        environment["INGEST_LARGE_ACCEPTANCE"] = "1"
        if args.restart_acceptance_s3:
            environment["INGEST_RESTART_ACCEPTANCE_S3"] = "1"
        selected = ["tests/test_large_transport.py"]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *selected,
            "-q",
            "--tb=short",
            f"--junitxml={output / (args.lane + '.xml')}",
        ],
        cwd=module,
        env=environment,
        check=False,
    )
    unchanged = before == sources()
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{args.lane}-runtime.json").write_text(
        json.dumps(
            {
                "started_at": started_at,
                "completed_at": datetime.now(UTC).isoformat(),
                "exit_code": result.returncode,
                "sources_unchanged_during_run": unchanged,
                "source_files_sha256": before,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return result.returncode or (0 if unchanged else 1)


if __name__ == "__main__":
    raise SystemExit(main())
