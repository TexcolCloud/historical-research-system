"""Apply bounded business-card deltas without granting workflow or source authority."""

import math
import re
from copy import deepcopy

from hrs.processing.errors import EvidenceMismatch
from hrs.processing.hashing import digest
from hrs.research.models import CardDraft, DossierCardDraft

_READONLY = {
    "state",
    "version",
    "approval_type",
    "checks",
    "human_decision",
    "revision_versions",
    "book_id",
    "book_title",
    "run_id",
    "source_run_id",
    "publication_sha256",
    "article_sha256",
    "original_article_id",
    "document_id",
    "lineage_id",
    "replaces_card_id",
    "linked_card_id",
    "sources",
    "pages",
    "reading_target",
    "text_sha256",
    "content_sha256",
    "image_sha256",
    "markdown_sha256",
    "source_current",
    "source_status",
    "needs_update",
    "research_current",
    "superseded",
    "canonical_id",
    "canonical_version",
    "source_mention_id",
    "created_at",
    "updated_at",
    "article_key",
    "local_id",
    "article_title",
    "source_file",
    "conversion",
}


def _json_value(value, ancestors=None):
    """Clone JSON values while rejecting nonfinite numbers and hidden projection fields."""
    if value is None or type(value) in {str, int, bool}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) not in {dict, list}:
        raise ValueError("Card changes require finite JSON values")
    ancestors = set() if ancestors is None else ancestors
    marker = id(value)
    if marker in ancestors:
        raise ValueError("Card changes cannot contain circular values")
    ancestors.add(marker)
    try:
        if type(value) is list:
            return [_json_value(item, ancestors) for item in value]
        if any(type(key) is not str or key in _READONLY for key in value):
            raise ValueError("Card changes cannot write readonly projection or approval fields")
        return {key: _json_value(item, ancestors) for key, item in value.items()}
    finally:
        ancestors.remove(marker)


def _pointer(path):
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 512:
        raise ValueError("Card change paths require a JSON pointer of at most 512 characters")
    parts = path[1:].split("/")
    if any(re.search(r"~(?![01])", part) for part in parts):
        raise ValueError("Card change paths contain an invalid JSON pointer escape")
    return [part.replace("~1", "/").replace("~0", "~") for part in parts]


def _index(part, size, *, add=False):
    if add and part == "-":
        return size
    if not re.fullmatch(r"0|[1-9][0-9]*", part):
        raise ValueError("Card array indices require canonical nonnegative integers")
    index = int(part)
    if index > size if add else index >= size:
        raise ValueError("Card change array index is outside its container")
    return index


def apply_card_changes(
    baseline: list[dict], base_sha256: str, changes: list[dict], *, dossier=False
) -> list[dict]:
    """Materialize a CAS-bound proposal atomically; callers still validate and review the full card."""
    if digest(baseline) != base_sha256:
        raise EvidenceMismatch("Card change baseline digest does not match the frozen candidate")
    if (
        not isinstance(baseline, list)
        or not baseline
        or any(not isinstance(card, dict) for card in baseline)
    ):
        raise ValueError("Card change baseline must contain existing cards")
    if not isinstance(changes, list) or not 1 <= len(changes) <= 128:
        raise ValueError("Card changes require between 1 and 128 operations")
    result = {"cards": deepcopy(baseline)}
    model = DossierCardDraft if dossier else CardDraft
    fields = model.model_fields.keys() - {"id", "article_id"}
    for change in changes:
        if (
            not isinstance(change, dict)
            or not isinstance(change.get("op"), str)
            or change["op"] not in {"add", "replace", "remove"}
        ):
            raise ValueError("Card changes support only add, replace and remove")
        operation = change["op"]
        if set(change) != ({"op", "path"} if operation == "remove" else {"op", "path", "value"}):
            raise ValueError("Card change operation has unexpected or missing fields")
        parts = _pointer(change["path"])
        if (
            len(parts) < 3
            or parts[0] != "cards"
            or parts[2] not in fields
            or any(part in _READONLY for part in parts[2:])
        ):
            raise ValueError(
                "Card changes may write business fields only, retaining card/source identities"
            )
        _index(parts[1], len(baseline))
        try:
            value = _json_value(change["value"]) if operation != "remove" else None
        except RecursionError as error:
            raise ValueError("Card change JSON value is nested too deeply") from error
        parent = result
        for part in parts[:-1]:
            if isinstance(parent, list):
                parent = parent[_index(part, len(parent))]
            elif isinstance(parent, dict) and part in parent:
                parent = parent[part]
            else:
                raise ValueError("Card change parent path does not exist")
        key = parts[-1]
        if isinstance(parent, list):
            position = _index(key, len(parent), add=operation == "add")
            if operation == "add":
                parent.insert(position, value)
            elif operation == "remove":
                parent.pop(position)
            else:
                parent[position] = value
        elif isinstance(parent, dict):
            if operation != "add" and key not in parent:
                raise ValueError("Card change target does not exist")
            if operation == "remove":
                del parent[key]
            else:
                parent[key] = value
        else:
            raise ValueError("Card change parent must be an object or array")
    return result["cards"]
