"""Small immutable publication locators, separate from rendered chapter bodies."""

import json
from copy import deepcopy
from functools import lru_cache

from hrs.library.chapter_content import read_json, storage_identity
from hrs.library.runs.outputs import fingerprint

POLICY = "book-navigation-v1"
MAX_CACHED_BYTES = 2 * 1024 * 1024


def chapter_signature(rows):
    return fingerprint(
        [
            {"id": str(row["id"]), "content": row["content"], "pages": row["pages"]}
            for row in sorted(rows, key=lambda row: str(row["id"]))
        ]
    )


def current_projection(run, rows):
    result = run.get("result") or {}
    projection = result.get("codex_reading_projection") or {}
    if (
        projection.get("policy") == POLICY
        and projection.get("publication_sha256") == result.get("codex_publication_sha256")
        and projection.get("navigation_sha256") == result.get("codex_projected_navigation_sha256")
        and projection.get("chapters_sha256") == chapter_signature(rows)
    ):
        return projection
    return None


@lru_cache(maxsize=8)
def cached_projection(storage, reference):
    return read_json(storage, reference)


def projection_json(settings, reference):
    loader = cached_projection if reference["byte_length"] <= MAX_CACHED_BYTES else read_json
    return loader(storage_identity(settings), json.dumps(reference, sort_keys=True))


def locator_chapter(row, parts):
    return {
        "id": str(row["id"]),
        "run_id": str(row["run_id"]),
        "parts": parts,
        "text": "".join(part["text"] for part in parts),
    }


def book_locators(settings, run, rows):
    """Validate SQL ownership on every call; only immutable source bytes are cached."""
    projection = current_projection(run, rows)
    if projection and projection.get("locators"):
        saved = projection_json(settings, projection["locators"])
        by_id = {str(row["id"]): row for row in rows}
        if len(saved["chapters"]) == len(rows) and {str(c["id"]) for c in saved["chapters"]} == set(
            by_id
        ):
            return [locator_chapter(by_id[str(c["id"])], c["parts"]) for c in saved["chapters"]]
    # Retained publications remain readable before an explicit/normal projection upgrade.
    storage = storage_identity(settings)
    return [
        locator_chapter(
            row, read_json(storage, json.dumps(row["content"], sort_keys=True))["parts"]
        )
        for row in rows
    ]


def book_navigation(settings, run, rows):
    projection = current_projection(run, rows)
    if projection and projection.get("navigation"):
        body = projection_json(settings, projection["navigation"])
        if (
            body.get("publication_sha256") == projection["publication_sha256"]
            and body.get("navigation_sha256") == projection["navigation_sha256"]
        ):
            return deepcopy(body["navigation"])
    if not rows:
        return {}
    body = read_json(storage_identity(settings), json.dumps(rows[0]["content"], sort_keys=True))
    return deepcopy(body.get("navigation", {}))


def prepare_projection(objects, run, doc, rows, *, groups=None, previous=None):
    """Prepare references; the caller commits them atomically with chapter SQL visibility."""
    publication_sha = doc["publication"]["markdown_sha256"]
    navigation = doc.get("navigation", {})
    navigation_sha = navigation.get("sha256")
    signature = chapter_signature(rows)
    previous = previous or {}
    unchanged_content = (
        previous.get("policy") == POLICY
        and previous.get("publication_sha256") == publication_sha
        and previous.get("chapters_sha256") == signature
    )
    locators = previous.get("locators") if unchanged_content else None
    if not locators:
        if groups is None:
            parts = [json.loads(objects.read_bytes(row["content"]))["parts"] for row in rows]
        else:
            parts = [group["parts"] for group in groups]
        locators = objects.put_bytes(
            json.dumps(
                {
                    "chapters": [
                        {"id": str(row["id"]), "parts": source_parts}
                        for row, source_parts in zip(rows, parts, strict=True)
                    ]
                },
                ensure_ascii=False,
            ).encode(),
            run_id=run["id"],
        )
    navigation_ref = (
        previous.get("navigation")
        if unchanged_content and previous.get("navigation_sha256") == navigation_sha
        else None
    )
    if not navigation_ref:
        navigation_ref = objects.put_bytes(
            json.dumps(
                {
                    "publication_sha256": publication_sha,
                    "navigation_sha256": navigation_sha,
                    "navigation": navigation,
                },
                ensure_ascii=False,
            ).encode(),
            run_id=run["id"],
        )
    return {
        "policy": POLICY,
        "publication_sha256": publication_sha,
        "organization_sha256": doc["publication"].get("organization_sha256"),
        "navigation_sha256": navigation_sha,
        "chapters_sha256": signature,
        "locators": locators,
        "navigation": navigation_ref,
    }
