"""Local entry point and native Codex coordinator launcher."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from hrs.codex.execution import run_codex
from hrs.codex.mcp import create_server
from hrs.config import project_root
from hrs.processing.application import prepare
from hrs.processing.store import Store
from hrs.processing.workspace import identity, resolve_workspace

PROJECT = project_root()


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("mcp")
    commands.add_parser("doctor")
    commands.add_parser("list")
    commands.add_parser("workspace")
    commands.add_parser(
        "migrate-ledger", help="Migrate a stopped workspace and seal legacy JSON snapshots"
    )
    execution = commands.add_parser(
        "configure-execution", help="Set shared capacity and warm-worker limits"
    )
    execution.add_argument("--capacity", type=int, required=True)
    execution.add_argument("--batch-pages", type=int, default=8)
    execution.add_argument("--overlap-reading", action=argparse.BooleanOptionalAction, default=True)
    workbench_parser = commands.add_parser(
        "workbench", help="Run the unified workbench and durable executor"
    )
    workbench_parser.add_argument("--port", type=int, default=8766)
    start_parser = commands.add_parser(
        "start", help="Import and process through the background executor"
    )
    start_parser.add_argument("source")
    start_parser.add_argument("--title", default="")
    resume_parser = commands.add_parser(
        "resume", help="Explicitly resume a queued or failed document"
    )
    resume_parser.add_argument("document_id")
    review_parser = commands.add_parser("review", help="Open the local human review workspace")
    review_parser.add_argument("--port", type=int, default=8766)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("source")
    prepare_parser.add_argument("--title", default="")
    for name in ["status", "publish", "run", "retry"]:
        command = commands.add_parser(name)
        command.add_argument("document_id")
        if name == "run":
            command.add_argument("--model")
        if name == "retry":
            command.add_argument("task_id")
    search = commands.add_parser("search")
    search.add_argument("query")
    args = parser.parse_args()
    root = resolve_workspace(args.root)
    if args.command == "mcp":
        create_server(root).run(transport="stdio")
        return
    store = Store(root)
    if args.command in {"review", "workbench"}:
        from hrs.codex.workbench import serve

        serve(store, args.port)
        return
    if args.command == "configure-execution":
        from hrs.processing.execution_policy import configure_execution

        result = configure_execution(
            root,
            capacity=args.capacity,
            batch_pages=args.batch_pages,
            overlap_reading=args.overlap_reading,
        )
    elif args.command == "migrate-ledger":
        result = store.checkpoints.migrate(store.verify_evidence)
    elif args.command == "prepare":
        result = prepare(store, args.source, args.title)
    elif args.command == "status":
        result = store.status(args.document_id)
    elif args.command == "publish":
        result = store.publish(args.document_id)
    elif args.command == "retry":
        result = store.retry_task(args.document_id, args.task_id)
    elif args.command == "search":
        result = store.search(args.query)
    elif args.command == "list":
        result = store.list_documents()
    elif args.command == "workspace":
        result = identity(store)
    elif args.command in {"start", "resume"}:
        from hrs.codex.workbench_lifecycle import ensure_workbench

        doc = (
            prepare(store, args.source, args.title)
            if args.command == "start"
            else store.status(args.document_id)
        )
        request = store.request_run(doc["id"], resume=args.command == "resume")
        result = {
            "document_id": doc["id"],
            "run_request": request,
            "workspace": identity(store),
            "workbench": ensure_workbench(store, document_id=doc["id"]),
        }
    elif args.command == "run":
        from hrs.processing.runner import Runner

        store.request_run(args.document_id, resume=True)
        executor = Runner(
            store, execute=lambda s, d, **options: run_codex(s, d, model=args.model, **options)
        )
        executor.tick(document_id=args.document_id)
        result = {"status": store.status(args.document_id), "workspace": identity(store)}
    else:
        executable = shutil.which("codex")
        login = (
            subprocess.run(
                [executable, "login", "status"], capture_output=True, text=True, check=False
            )
            if executable
            else None
        )
        result = {
            "root": str(root),
            "codex": executable,
            "logged_in": bool(login and login.returncode == 0),
            "storage": root.is_dir(),
            "runtime": "native Codex subagents + local MCP",
            "docker_required": False,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "run" and result["status"].get("state") != "published":
        raise SystemExit(2)
    if args.command == "doctor" and not result["logged_in"]:
        raise SystemExit(1)


def main() -> None:
    try:
        _main()
    except KeyboardInterrupt:
        print(
            json.dumps(
                {
                    "error": "KeyboardInterrupt",
                    "message": "Interrupted; committed evidence retained.",
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(130) from None
    except (ValueError, RuntimeError, OSError, KeyError, StopIteration) as error:
        print(
            json.dumps({"error": type(error).__name__, "message": str(error)}, ensure_ascii=False),
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
