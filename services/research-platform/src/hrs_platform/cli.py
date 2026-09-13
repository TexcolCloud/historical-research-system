import argparse

from .database import engine_for, migrate
from .settings import Settings


def main():
    parser = argparse.ArgumentParser(description="Unified historical research platform")
    parser.add_argument(
        "command",
        choices=[
            "migrate",
            "api",
            "namespace",
            "worker",
            "gpu-worker",
            "doctor",
            "backup",
            "restore",
            "reindex",
            "evaluate",
            "evaluate-offline",
        ],
    )
    parser.add_argument(
        "--manifest-reference", help="JSON S3 reference returned by backup; contains no credentials"
    )
    parser.add_argument("--restore-suffix", help="New isolated destination database suffix")
    parser.add_argument("--run-id", help="Published book run to reindex without repeating OCR")
    parser.add_argument("--cases", help="Evaluation JSON: query and expected chapter_id/start/end ranges")
    parser.add_argument("--semantic", action="store_true", help="Evaluate semantic retrieval and reranking")
    parser.add_argument("--limit", default=10, type=int, help="Evaluation result limit")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=18170, type=int)
    args = parser.parse_args()
    if args.command == 'evaluate-offline':
        import json
        from pathlib import Path

        from .retrieval_offline import evaluate_offline
        if not args.cases:
            parser.error('evaluate-offline requires --cases dataset.json')
        settings = Settings.load()
        engine = engine_for(settings)
        try:
            print(json.dumps(evaluate_offline(settings, engine, json.loads(Path(args.cases).read_text('utf-8-sig'))), ensure_ascii=False, indent=2))
        finally:
            engine.dispose()
        return
    if args.command in {"reindex", "evaluate"}:
        import json
        from uuid import UUID

        from .search import Search

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
            search = Search(settings, engine)
            if args.command == "evaluate":
                from pathlib import Path

                from .retrieval_evaluation import evaluate

                result = evaluate(
                    search,
                    run_id,
                    json.loads(Path(args.cases).read_text(encoding="utf-8-sig")),
                    semantic=args.semantic,
                    limit=args.limit,
                )
            else:
                result = search.index(run_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            engine.dispose()
        return
    if args.command in {"backup", "restore"}:
        import json

        from .backups import backup, restore

        if args.command == "restore" and (not args.manifest_reference or not args.restore_suffix):
            parser.error("restore requires --manifest-reference and --restore-suffix")
        result = (
            backup(Settings.load())
            if args.command == "backup"
            else restore(Settings.load(), json.loads(args.manifest_reference), args.restore_suffix)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "doctor":
        from .doctor import run

        raise SystemExit(run(Settings.load()))
    if args.command == "migrate":
        engine = engine_for(Settings.load())
        try:
            migrate(engine)
        finally:
            engine.dispose()
    elif args.command == "api":
        import uvicorn

        uvicorn.run(
            "hrs_platform.api:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            timeout_graceful_shutdown=10,
        )
    else:
        import asyncio

        from .worker import register_namespace, run_worker

        settings = Settings.load()
        import signal

        def interrupt(*_):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, interrupt)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, interrupt)
        try:
            asyncio.run(
                register_namespace(settings)
                if args.command == "namespace"
                else run_worker(settings, gpu=args.command == "gpu-worker")
            )
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    main()
