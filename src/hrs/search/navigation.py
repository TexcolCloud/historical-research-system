"""Refresh directory metadata over retained source ranges without regenerating vectors."""

from hrs.search.chunks import source_excerpt

FIELDS = ("doc_kind", "toc_entry_id", "toc_path", "printed_pages", "navigation_version")


def navigation_fields(chapters, row):
    chapter = chapters.get(str(row["chapter_id"]))
    if chapter is None:
        raise ValueError("Indexed navigation belongs to another publication")
    excerpt = source_excerpt(chapter, row["start"], row["end"])
    fields = {key: excerpt[key] for key in FIELDS}
    if "context" in row:
        fields["context"] = [
            {**extra, **navigation_fields(chapters, extra)}
            if str(extra.get("chapter_id")) in chapters
            else extra
            for extra in row["context"]
        ]
    return fields


def lexical_text(row):
    return "\n".join([*row.get("toc_path", []), row.get("retrieval_text", row["text"])])
