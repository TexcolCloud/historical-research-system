from uuid import uuid4

from hrs_platform.search import chapter_chunks


def test_chunk_source_ranges_cover_the_exact_reviewed_text_across_physical_pages():
    parts = [
        {"span_id": "a", "start": 0, "text": "原书第一部分。\n", "source": {"pages": [8]}},
        {"span_id": "b", "start": 9, "text": "原书第二部分。\n", "source": {"pages": [9]}},
    ]
    chapter = {
        "id": str(uuid4()),
        "book_id": str(uuid4()),
        "run_id": str(uuid4()),
        "title": "章",
        "text": "".join(part["text"] for part in parts),
        "parts": parts,
    }
    chunks = list(chapter_chunks(chapter, size=10, overlap=0))
    assert "".join(row["text"] for row in chunks) == chapter["text"]
    assert {page for row in chunks for page in row["pages"]} == {8, 9}
    assert all(chapter["text"][row["start"] : row["end"]] == row["text"] for row in chunks)
