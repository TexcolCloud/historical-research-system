"""Repair pending review scopes; optionally recheck them with the local vision runtime."""

import argparse
import json

from hrs_platform.core.db import engine_for
from hrs_platform.services.review import Review
from hrs_platform.services.review_repair import recheck_pending
from hrs_platform.services.review_repair import repair_scopes
from hrs_platform.core.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--recheck", action="store_true")
    parser.add_argument("--pages", type=int, nargs="+")
    args = parser.parse_args()
    settings = Settings.load()
    engine = engine_for(settings)
    try:
        review = Review(settings, engine)
        print(json.dumps(repair_scopes(review, args.run_id)), flush=True)
        if args.recheck and review.status(args.run_id)["pending_count"]:
            from document_extraction.settings import Settings as ExtractionSettings

            config = ExtractionSettings.load(
                settings.project_root / "services/document-extraction/config/default.json"
            )
            result = recheck_pending(
                review,
                args.run_id,
                config.vision_review,
                settings.cache_root / args.run_id / "review-policy-recheck",
                pages=set(args.pages) if args.pages else None,
            )
            print(json.dumps(result), flush=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
