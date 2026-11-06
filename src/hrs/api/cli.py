import argparse

from hrs.config import Settings
from hrs.search.indexing import BookIndexer
from hrs.storage.db import engine_for, migrate


def main():
    parser = argparse.ArgumentParser(description="Unified historical research platform")
    parser.add_argument(
        "command",
        choices=[
            "migrate",
            "api",
            "doctor",
            "backup",
            "restore",
            "reindex",
            "amend-library",
            "evaluate",
            "evaluate-offline",
        ],
    )
    parser.add_argument(
        "--manifest-reference",
        help="Backup manifest path or JSON receipt; historical S3 references remain readable",
    )
    parser.add_argument("--destination", help="New independent local backup directory")
    parser.add_argument(
        "--additional-native-root",
        action="append",
        default=[],
        help="Additional stopped native workspace required by SQL; repeat for multiple roots",
    )
    parser.add_argument(
        "--restore-bucket", help="Empty object-store bucket for independent restoration"
    )
    parser.add_argument("--restore-suffix", help="New isolated destination database suffix")
    parser.add_argument(
        "--restore-native-root",
        help="Absent restore directory; multiple workspaces restore under workspace-ID subdirectories",
    )
    parser.add_argument("--run-id", help="Published book run to reindex without repeating OCR")
    parser.add_argument(
        "--cases", help="Evaluation JSON: query and expected chapter_id/start/end ranges"
    )
    parser.add_argument(
        "--amendments", help="JSON list of located, same-length published-source errata"
    )
    parser.add_argument(
        "--semantic", action="store_true", help="Evaluate semantic retrieval and reranking"
    )
    parser.add_argument("--limit", default=10, type=int, help="Evaluation result limit")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=18170, type=int)
    args = parser.parse_args()
    if args.command == "amend-library":
        import json
        from pathlib import Path
        from uuid import UUID

        from hrs.library.catalog import Library

        if not args.run_id or not args.amendments:
            parser.error("amend-library requires --run-id and --amendments")
        settings = Settings.load()
        engine = engine_for(settings)
        try:
            result = Library(settings, engine).amend(
                str(UUID(args.run_id)), json.loads(Path(args.amendments).read_text("utf-8-sig"))
            )
            print(json.dumps(result, ensure_ascii=False, default=str))
        finally:
            engine.dispose()
        return
    if args.command == "evaluate-offline":
        import json
        from pathlib import Path

        from hrs.search.offline import evaluate_offline

        if not args.cases:
            parser.error("evaluate-offline requires --cases dataset.json")
        settings = Settings.load()
        engine = engine_for(settings)
        try:
            print(
                json.dumps(
                    evaluate_offline(
                        settings, engine, json.loads(Path(args.cases).read_text("utf-8-sig"))
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        finally:
            engine.dispose()
        return
    if args.command in {"reindex", "evaluate"}:
        import json
        from uuid import UUID

        from hrs.search.search import Search

        if not args.run_id:
            parser.error(f"{args.command} requires --run-id")
        if args.command == "evaluate" and not args.cases:
            parser.error("evaluate requires --cases")
        try:
            run_id = str(UUID(args.run_id))
        except ValueError:
            parser.error("--run-id must be a UUID")
        settings = Settings.load()
        engine = engine_for(settings)
        try:
            if args.command == "evaluate":
                from pathlib import Path

                from hrs.search.evaluation import evaluate

                result = evaluate(
                    Search(settings, engine),
                    run_id,
                    json.loads(Path(args.cases).read_text(encoding="utf-8-sig")),
                    semantic=args.semantic,
                    limit=args.limit,
                )
            else:
                result = BookIndexer(settings, engine).index(run_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            engine.dispose()
        return
    if args.command in {"backup", "restore"}:
        import json
        from pathlib import Path

        from hrs.backups import backup, restore

        if args.command == "restore" and (not args.manifest_reference or not args.restore_suffix):
            parser.error("restore requires --manifest-reference and --restore-suffix")
        if args.command == "restore" and args.additional_native_root:
            parser.error("--additional-native-root is a backup option")
        if args.command == "backup":
            result = backup(
                Settings.load(),
                destination=args.destination,
                native_roots=args.additional_native_root,
            )
        else:
            reference = args.manifest_reference
            if not reference.lstrip().startswith(("{", '"')) and Path(reference).is_file():
                selected = reference
            else:
                selected = json.loads(reference)
            settings = Settings.load()
            if args.restore_bucket:
                if isinstance(selected, dict) and "key" in selected:
                    parser.error("Historical S3 references require their original retained bucket.")
                settings = settings.model_copy(update={"s3_bucket": args.restore_bucket})
            result = restore(
                settings,
                selected,
                args.restore_suffix,
                native_destination=args.restore_native_root,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "doctor":
        from hrs.api.doctor import run

        raise SystemExit(run(Settings.load()))
    if args.command == "migrate":
        engine = engine_for(Settings.load())
        try:
            migrate(engine)
        finally:
            engine.dispose()
    elif args.command == "api":
        import uvicorn

        from hrs.api.main import create_app
        from hrs.api.server import Server

        Server(
            uvicorn.Config(
                create_app(),
                host=args.host,
                port=args.port,
                timeout_graceful_shutdown=10,
            )
        ).run()


if __name__ == "__main__":
    main()
