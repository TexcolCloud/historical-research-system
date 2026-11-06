"""Save human-edited positions while retaining the original issue/evidence identity."""

from dataclasses import asdict

from hrs.library.errors import ServiceError
from hrs.processing.review_locations import locate_issues


def normalize_draft_locations(text, locations, originals):
    if locations is None:
        return None
    expected = {location["id"]: location for location in originals}
    incoming = [asdict(location) for location in locations]
    identities = [location["id"] for location in incoming]
    if len(set(identities)) != len(identities) or set(identities) != set(expected):
        raise ServiceError(422, "草稿定位必须保留本页全部疑点，不能增加、遗漏或替换疑点身份。")
    result = []
    for location in incoming:
        original = expected[location["id"]]
        if location["page"] != original["page"]:
            raise ServiceError(422, "草稿定位不能改变疑点所属原件页。")
        raw = {
            "anchor": location["anchor"],
            "focus": location["focus"],
            "explanation": original["message"],
            "source_quote": original["source_reading"],
            "suggestion": original["suggestion"],
            "boxes": original["boxes"],
            "image_precision": original["image_precision"],
        }
        if location.get("insertion_offset") is not None:
            raw["insertion_offset"] = location["insertion_offset"]
        raw["unlocated_reason"] = location.get("unlocated_reason", "")
        if location.get("text_spans"):
            occurrences = [
                {
                    k: v
                    for k, v in span.items()
                    if k in {"anchor", "focus", "insertion_offset"} and v is not None
                }
                for span in location["text_spans"]
            ]
            # The top-level position is the first span's compatibility projection.
            # Old clients may edit it without replacing the duplicated single span.
            # Never let that stale duplicate override the current draft anchor.
            occurrences[0] = {
                key: raw[key]
                for key in ("anchor", "focus", "insertion_offset", "unlocated_reason")
                if key in raw
            }
            raw["occurrences"] = occurrences
        normalized = locate_issues(
            text, [raw], page=original["page"], image_sha256=original["image_sha256"]
        )[0]
        normalized["id"] = original["id"]
        result.append(normalized)
    return result
