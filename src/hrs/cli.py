"""One command entry point for the application, maintenance and native MCP."""

import argparse
import json
import sys
import time

from hrs import __version__

NATIVE_COMMANDS = {
    "mcp",
    "start",
    "resume",
    "workbench",
    "review",
    "workspace",
    "list",
    "prepare",
    "publish",
    "run",
    "retry",
    "search",
    "migrate-ledger",
    "configure-execution",
}
MAINTENANCE_COMMANDS = {
    "api",
    "migrate",
    "doctor",
    "backup",
    "restore",
    "reindex",
    "amend-library",
    "evaluate",
    "evaluate-offline",
}


def _models(action):
    from hrs.config import load_environment, project_root, resolve_path
    from hrs.search.settings import MODEL_REVISIONS

    project = project_root()
    root = resolve_path(
        project, load_environment(project).get("PLATFORM_MODELS_ROOT", "models/document-retrieval")
    )
    paths = {
        name: root / name.split("/")[-1] / revision for name, revision in MODEL_REVISIONS.items()
    }
    if action == "prepare":
        from huggingface_hub import snapshot_download

        for name, revision in MODEL_REVISIONS.items():
            snapshot_download(repo_id=name, revision=revision, local_dir=paths[name])
    report = {
        name: {"path": str(path), "present": (path / "config.json").is_file()}
        for name, path in paths.items()
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not all(item["present"] for item in report.values()):
        raise SystemExit(1)


def _test_infrastructure():
    from botocore.exceptions import BotoCoreError, ClientError

    from hrs.config import Settings
    from hrs.storage.service import objects_for

    settings = Settings.load()
    if settings.s3_endpoint != "http://127.0.0.1:58339" or settings.s3_bucket != "platform-ci":
        raise ValueError("This check requires the isolated CI storage endpoint and bucket.")
    objects = objects_for(settings)
    for attempt in range(45):
        try:
            objects.client.head_bucket(Bucket=objects.bucket)
            print("Isolated test storage is ready.")
            return
        except (BotoCoreError, ClientError):
            if attempt == 44:
                raise RuntimeError("Isolated test storage did not become ready.") from None
            time.sleep(2)


def main():
    arguments = sys.argv[1:]
    position = 2 if len(arguments) > 2 and arguments[0] == "--root" else 0
    command = arguments[position] if len(arguments) > position else None
    aliases = {"document-status": "status", "native-doctor": "doctor"}
    if command in NATIVE_COMMANDS or command in aliases:
        from hrs.codex.cli import main as native_main

        if command in aliases:
            sys.argv[position + 1] = aliases[command]
        return native_main()
    if command in MAINTENANCE_COMMANDS:
        from hrs.api.cli import main as maintenance_main

        return maintenance_main()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "up", "stop", "status"):
        child = subparsers.add_parser(name)
        if name in {"init", "up"}:
            child.add_argument(
                "--existing-services",
                action="store_true",
                help="Use already-running SQL/S3/search/upload services",
            )
        if name == "up":
            child.add_argument("--build", action="store_true")
    model_parser = subparsers.add_parser("models", help="Prepare or inspect pinned BGE artifacts")
    model_parser.add_argument("action", choices=("prepare", "check"))
    subparsers.add_parser("contracts", help="Export the public OpenAPI contract")
    subparsers.add_parser("check-test-infra", help="Wait for isolated CI object storage")
    # Show independently usable native/maintenance commands in the top-level help.
    for name in sorted(NATIVE_COMMANDS | MAINTENANCE_COMMANDS | aliases.keys()):
        subparsers.add_parser(name)
    args = parser.parse_args()
    if args.command in {"init", "up", "stop", "status"}:
        from hrs.app import command as application_command

        return application_command(
            args.command,
            build=getattr(args, "build", False),
            existing_services=getattr(args, "existing_services", False),
        )
    if args.command == "models":
        return _models(args.action)
    if args.command == "contracts":
        from hrs.api.export_openapi import main as export_contract

        return export_contract()
    return _test_infrastructure()
