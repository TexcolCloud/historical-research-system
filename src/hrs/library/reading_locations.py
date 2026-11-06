"""Resolve published source coordinates without changing canonical Markdown."""

import hashlib
import re


def text_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def part_ranges(chapter):
    offset = 0
    for part in chapter["parts"]:
        end = offset + len(part["text"])
        yield offset, end, part
        offset = end


def reading_target(chapter, start, end, physical_page=None):
    """Create one canonical, hash-bound target for a visible source range."""
    text = chapter["text"]
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        return None
    # A range beginning at real heading syntax cannot place its opening marker
    # before '#': that would change the shadow Markdown parse. Start at the
    # visible title, retaining the whole body for a multi-block search excerpt.
    value = text[start:end]
    line_start = text.rfind("\n", 0, start) + 1
    before = text[line_start:start]
    prefix = re.match(r" {0,3}#{1,6}[ \t]+(?=\S)", value)
    indent = len(value) - len(value.lstrip(" "))
    if prefix and not before.strip(" ") and len(before) + indent <= 3:
        heading = re.fullmatch(r" {0,3}#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*", value)
        if heading:
            start += heading.start(1)
            end = start + len(heading[1])
        else:
            start += prefix.end()
    owner = next(
        (part for left, right, part in part_ranges(chapter) if left <= start < right), None
    )
    if owner is None:
        return None
    source = owner["source"]
    pages = source.get("pages", [source.get("page")])
    page = physical_page if physical_page is not None else pages[0]
    if type(page) is not int or page < 1 or page not in pages:
        return None
    return {
        "chapter_id": str(chapter["id"]),
        "run_id": str(chapter["run_id"]),
        "physical_page": page,
        "start": start,
        "end": end,
        "text_sha256": text_digest(text),
        "anchor": text[start:end],
        "image_sha256": source.get("image_sha256", ""),
    }


def target_in_chapter(chapter, target):
    """Translate a verified page-local navigation target to its owning article."""
    page, page_start, anchor = (
        target.get("physical_page"),
        target.get("start"),
        target.get("anchor"),
    )
    if type(page_start) is not int or not isinstance(anchor, str) or not anchor:
        return None
    matches = []
    for start, _, part in part_ranges(chapter):
        source = part["source"]
        if source.get("page") != page:
            continue
        if (
            not target.get("markdown_sha256")
            or source.get("markdown_sha256") != target["markdown_sha256"]
        ):
            continue
        if target.get("image_sha256") and source.get("image_sha256") != target["image_sha256"]:
            continue
        original_start = source.get("original_start")
        original_end = source.get("original_end")
        if type(original_start) is not int or type(original_end) is not int:
            continue
        if not original_start <= page_start < original_end:
            continue
        local = start + page_start - original_start
        stop = local + len(anchor)
        if chapter["text"][local:stop] != anchor:
            continue
        resolved = reading_target(chapter, local, stop, page)
        if resolved:
            matches.append(resolved)
    return matches[0] if len(matches) == 1 else None


def toc_links(chapter, targets):
    """Only turn a uniquely retained directory source range into a link."""
    links = []
    for entry in chapter.get("navigation", {}).get("entries", []):
        target = targets.get(entry["id"])
        anchor = entry.get("source_anchor")
        if not target or not isinstance(anchor, str) or not anchor:
            continue
        matches = []
        for left, right, part in part_ranges(chapter):
            if entry["source_page"] not in part["source"].get("pages", []):
                continue
            cursor = left
            while (at := chapter["text"].find(anchor, cursor, right)) >= 0:
                matches.append((at, at + len(anchor)))
                cursor = at + 1
        if len(matches) == 1:
            start, end = matches[0]
            links.append(
                {
                    "id": entry["id"],
                    "title": entry["title"],
                    "level": entry["level"],
                    "source_start": start,
                    "source_end": end,
                    "target": target,
                }
            )
    return links
