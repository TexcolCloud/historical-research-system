"""Canonical intervals for an explicitly selected, published directory subtree."""

import re
from dataclasses import dataclass

from hrs_platform.domain.errors import ServiceError
from hrs_platform.services.documents.footnotes import resolve_footnotes
from hrs_platform.services.documents.review import digest
from hrs_platform.services.retrieval.chunks import context_blocks, source_excerpt

LINKED_CONTEXT = {"footnote", "note_owner", "table_header", "table_note", "table_intro", "section_intro"}


@dataclass
class DirectoryScope:
    ranges: dict[str, tuple[int, int]]

    @classmethod
    def resolve(cls, library, book_id, entry_id):
        if not book_id:
            raise ServiceError(422, "选择目录范围时必须指定本书。")
        entries = library.reading_navigation(book_id).get("entries", [])
        index = next((i for i, row in enumerate(entries) if row["id"] == entry_id), None)
        if index is None:
            raise ServiceError(409, "所选目录不属于当前阅读版本，请重新选择范围。")
        selected = entries[index]
        following = next((e for e in entries[index + 1 :] if e["level"] <= selected["level"]), None)
        if (
            selected.get("status") not in {"matched", "matched_manual"}
            or not selected.get("reading_target")
            or (following and not following.get("reading_target"))
        ):
            raise ServiceError(409, "这条目录的起止位置尚未可靠关联，暂不能限定检索范围。")
        chapters = library.chapters(book_id)
        identities = [str(row["id"]) for row in chapters]

        def boundary(entry):
            target = entry["reading_target"]
            identity = str(target["chapter_id"])
            if identity not in identities:
                raise ServiceError(409, "目录对应的正文版本已更新，请重新选择范围。")
            body = library.chapter(identity)
            if str(body["book_id"]) != str(book_id) or digest(body["text"]) != target["text_sha256"]:
                raise ServiceError(409, "目录与当前正文版本不符，请重新选择范围。")
            if not 0 <= target["start"] < len(body["text"]):
                raise ServiceError(409, "目录位置已失效，请重新核对跳转。")
            start = target["start"]
            line_start = body["text"].rfind("\n", 0, start) + 1
            prefix = body["text"][line_start:start]
            heading = None
            if re.fullmatch(r" {0,3}#{1,6}[ \t]+", prefix):
                heading = next(
                    (
                        block
                        for block in context_blocks(body["text"])
                        if block["kind"] == "heading" and block["start"] <= line_start < start < block["end"]
                    ),
                    None,
                )
            if heading:
                # Reader marks omit Markdown syntax; section ownership starts
                # at the actual heading, including its canonical prefix.
                start = heading["start"]
            return identities.index(identity), start

        first, start = boundary(selected)
        last, end = boundary(following) if following else (len(chapters) - 1, None)
        if (last, end if end is not None else float("inf")) <= (first, start):
            raise ServiceError(409, "目录顺序与正文位置不符，请先核对目录跳转。")
        ranges = {}
        for position in range(first, last + 1):
            identity = identities[position]
            left = start if position == first else 0
            right = end if position == last and end is not None else len(library.chapter(identity)["text"])
            if left < right:
                ranges[identity] = (left, right)
        return cls(ranges)

    def query_filter(self):
        return {
            "bool": {
                "should": [
                    {
                        "bool": {
                            "filter": [
                                {"term": {"chapter_id": identity}},
                                {"range": {"start": {"lt": end}}},
                                {"range": {"end": {"gt": start}}},
                            ]
                        }
                    }
                    for identity, (start, end) in self.ranges.items()
                ],
                "minimum_should_match": 1,
            }
        }

    def clip_candidates(self, rows, load_chapter):
        result = []
        for row in rows:
            bounds = self.ranges.get(str(row["chapter_id"]))
            if bounds is None:
                continue
            start, end = max(row["start"], bounds[0]), min(row["end"], bounds[1])
            if start < end:
                chapter = load_chapter(row["chapter_id"])
                item = {**row, **source_excerpt(chapter, start, end)}
                if (start, end) != (row["start"], row["end"]):
                    notes = chapter.get("footnotes")
                    if notes is None:
                        notes = resolve_footnotes(chapter["text"], chapter["parts"])
                    context = []
                    for extra in item.get("context", []):
                        role = extra.get("role")
                        if role == "footnote" and any(
                            note["note"]["start"] < extra["end"]
                            and note["note"]["end"] > extra["start"]
                            and any(start <= ref["start"] < end for ref in note["references"])
                            for note in notes
                        ):
                            context.append(extra)
                        elif role == "note_owner" and any(
                            note["note"]["start"] < end
                            and note["note"]["end"] > start
                            and any(
                                extra["start"] <= ref["start"] < extra["end"] for ref in note["references"]
                            )
                            for note in notes
                        ):
                            context.append(extra)
                        elif role == "neighbor":
                            context.append(extra)
                        # Table/section supplements are rebuilt from the clipped
                        # source by expand_hits, never inherited from excluded text.
                    item["context"] = context
                item["retrieval_text"] = "\n".join(
                    [
                        item.get("book_title", ""),
                        *item.get("toc_path", []),
                        item["text"],
                        *(
                            extra["text"]
                            for extra in item.get("context", [])
                            if extra.get("role") in LINKED_CONTEXT
                        ),
                    ]
                )
                result.append(item)
        return result

    def clip_results(self, rows, load_chapter):
        result = self.clip_candidates(rows, load_chapter)
        for row in result:
            chapter = load_chapter(row["chapter_id"])
            bounds = self.ranges[str(row["chapter_id"])]
            context = []
            for extra in row.get("context", []):
                if extra.get("chapter_id", row["chapter_id"]) != row["chapter_id"]:
                    continue
                start, end = extra["start"], extra["end"]
                if not 0 <= start < end <= len(chapter["text"]):
                    continue
                linked = extra.get("role") in LINKED_CONTEXT
                outside = not bounds[0] <= start < end <= bounds[1]
                if not linked:
                    start, end = max(start, bounds[0]), min(end, bounds[1])
                if start < end:
                    context.append(
                        {
                            **source_excerpt(chapter, start, end),
                            "role": extra["role"],
                            "outside_scope": linked and outside,
                        }
                    )
            row["context"] = context
        return result
