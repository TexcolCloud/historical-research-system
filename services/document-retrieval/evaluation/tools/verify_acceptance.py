"""Verify saved acceptance hashes; this does not rerun the acceptance experiments."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    module = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest",
        type=Path,
        nargs="?",
        default=module / "evaluation/final-acceptance-20260909-v1/manifest.json",
    )
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text("utf-8"))
    failures = []
    for entry in manifest["files"]:
        path = Path(entry["path"])
        if not path.is_absolute():
            path = module.parents[1] / path
        if not path.is_file():
            failures.append({"path": entry["path"], "reason": "missing"})
        else:
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != entry["sha256"]:
                    failures.append({"path": entry["path"], "reason": "hash_mismatch"})
    print(
        json.dumps(
            {
                "classification": "saved_artifact_hash_verification",
                "manifest": str(args.manifest.resolve()),
                "files": len(manifest["files"]),
                "failures": failures,
                "experiments_rerun": False,
                "user_acceptance_implied": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
