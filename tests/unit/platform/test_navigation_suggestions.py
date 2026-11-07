from copy import deepcopy
from types import SimpleNamespace

import pytest

from hrs.library.errors import Problem
from hrs.library.navigation_suggestions import supplemental_candidates


def page(number, text, *, kind="text", approved=True, headings=None):
    return {
        "page": number,
        "image_sha256": f"image-{number}",
        "approved": {"markdown": text, "sha256": f"text-{number}"} if approved else None,
        "observation": {"kind": kind, "headings": headings or [], "sequence": "body"},
    }


def document(*pages, count=1):
    return {
        "pages": [page(1, "目录\nMemo 1", kind="toc"), *pages],
        "navigation": {
            "entries": [
                {
                    "id": f"entry-{i}",
                    "title": "Memo",
                    "source_anchor": "Memo 1",
                    "source_page": 1,
                    "source_sha256": "text-1",
                    "status": "not_found",
                }
                for i in range(count)
            ]
        },
    }


def settings(tmp_path):
    return SimpleNamespace(models_root=tmp_path, retrieval_device="cpu", retrieval_endpoint="")


def test_unlocated_source_is_not_repeatedly_sent_to_semantic_recall(tmp_path):
    doc = document(page(2, "Memo", headings=["Memo"]))
    doc["navigation"]["entries"][0]["source_anchor"] = "not retained in the original"
    backend = Compute()
    assert (
        supplemental_candidates(doc, tmp_path / "missing.pdf", settings(tmp_path), compute=backend)
        == {}
    )
    assert backend.calls == []


class Compute:
    def __init__(self):
        self.calls = []

    def compute(self, operation, texts, **options):
        self.calls.append((operation, list(texts), options))
        return {"vectors": [[1.0, 0.0] if "memo" in text.lower() else [0.0, 1.0] for text in texts]}


def bookmark_pdf(path):
    """A real two-page PDF with an outline pointing at its second physical page."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R /Outlines 7 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 5 0 R /Resources << >> >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 6 0 R /Resources << >> >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
        b"<< /Length 0 >>\nstream\n\nendstream",
        b"<< /Type /Outlines /First 8 0 R /Last 8 0 R /Count 1 >>",
        b"<< /Title (Memo) /Parent 7 0 R /Dest [4 0 R /Fit] >>",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, value in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(data)


def test_real_pdf_bookmark_becomes_a_unique_approved_anchor(tmp_path):
    source = tmp_path / "source.pdf"
    bookmark_pdf(source)
    doc = document(page(2, "# Memo\n正文开头", headings=["Memo"]))
    before = deepcopy(doc)
    result = supplemental_candidates(doc, source, settings(tmp_path), compute=Compute())
    candidate = result["entry-0"][0]
    assert candidate["physical_page"] == 2
    assert candidate["anchor"] == "Memo"
    assert candidate["match_method"] == "bookmark"
    assert candidate["source_sha256"] == "text-1"
    assert candidate["markdown_sha256"] == "text-2"
    assert candidate["image_sha256"] == "image-2"
    assert 0 <= candidate["match_score"] <= 1
    assert doc == before


def test_semantic_recall_excludes_toc_unapproved_and_duplicate_anchors(tmp_path):
    doc = document(
        page(2, "# Memo\n正确正文", headings=["Memo"]),
        page(3, "# Memo\n待核内容", approved=False, headings=["Memo"]),
        page(4, "# Memo\n重复 Memo", headings=["Memo"]),
        page(5, "# Memo\n另一目录", kind="toc", headings=["Memo"]),
    )
    compute = Compute()
    result = supplemental_candidates(doc, tmp_path / "no.pdf", settings(tmp_path), compute=compute)
    assert [(c["physical_page"], c["anchor"]) for c in result["entry-0"]] == [(2, "Memo")]
    assert result["entry-0"][0]["match_method"] == "semantic"
    assert any(call[2].get("query") is True for call in compute.calls)
    assert any(call[2].get("query") is False for call in compute.calls)


def test_budgets_limit_entries_and_page_anchors_and_cache_repeated_embeddings(tmp_path):
    text = "\n".join(f"# Memo {i}" for i in range(10))
    doc = document(page(2, text, headings=[f"Memo {i}" for i in range(10)]), count=20)
    compute = Compute()
    first = supplemental_candidates(doc, tmp_path / "no.pdf", settings(tmp_path), compute=compute)
    calls = len(compute.calls)
    assert len(first) == 12
    assert all(len(candidates) <= 3 for candidates in first.values())
    document_texts = [
        text for _, texts, options in compute.calls if not options.get("query") for text in texts
    ]
    assert len(document_texts) <= 4
    assert (
        supplemental_candidates(doc, tmp_path / "no.pdf", settings(tmp_path), compute=compute)
        == first
    )
    assert len(compute.calls) == calls


def test_valid_supplements_advance_the_next_budget_and_stale_source_is_skipped(tmp_path):
    doc = document(page(2, "# Memo\n正文", headings=["Memo"]), count=14)
    compute = Compute()
    doc["navigation"]["supplemental_candidates"] = supplemental_candidates(
        doc, tmp_path / "no.pdf", settings(tmp_path), compute=compute
    )
    second = supplemental_candidates(doc, tmp_path / "no.pdf", settings(tmp_path), compute=compute)
    assert set(second) == {"entry-12", "entry-13"}
    doc["navigation"]["entries"][12]["source_sha256"] = "old-source"
    third = supplemental_candidates(doc, tmp_path / "no.pdf", settings(tmp_path), compute=compute)
    assert set(third) == {"entry-13"}


def test_a_changed_target_hash_invalidates_a_supplement_and_matched_entries_are_ignored(tmp_path):
    doc = document(page(2, "# Memo\n正文", headings=["Memo"]), count=2)
    compute = Compute()
    prior = supplemental_candidates(doc, tmp_path / "no.pdf", settings(tmp_path), compute=compute)
    doc["navigation"]["supplemental_candidates"] = prior
    doc["pages"][1]["image_sha256"] = "new-image"
    doc["navigation"]["entries"][1]["status"] = "matched_manual"
    result = supplemental_candidates(doc, tmp_path / "no.pdf", settings(tmp_path), compute=compute)
    assert set(result) == {"entry-0"}
    assert result["entry-0"][0]["image_sha256"] == "new-image"


def test_unavailable_or_invalid_embeddings_are_a_recoverable_problem(tmp_path):
    class Broken:
        def compute(self, *args, **kwargs):
            raise OSError("private upstream details")

    doc = document(page(2, "# Memo\n正文", headings=["Memo"]))
    with pytest.raises(Problem) as failure:
        supplemental_candidates(doc, tmp_path / "no.pdf", settings(tmp_path), compute=Broken())
    assert failure.value.status == 503
    assert failure.value.retryable
    assert "private" not in str(failure.value)


@pytest.mark.parametrize("vectors", [[], [[float("nan"), 1]], [[0, 0]], [[1] * 2049]])
def test_bad_embeddings_do_not_become_navigation_evidence(tmp_path, vectors):
    class Broken:
        def compute(self, *args, **kwargs):
            return {"vectors": vectors}

    doc = document(page(2, "# Memo\n正文", headings=["Memo"]))
    with pytest.raises(Problem):
        supplemental_candidates(doc, tmp_path / "no.pdf", settings(tmp_path), compute=Broken())


def test_waiting_pages_are_retried_after_approval_without_negative_cache(tmp_path):
    doc = document(page(2, "# Memo\n正文", headings=["Memo"], approved=False))
    compute = Compute()
    assert (
        supplemental_candidates(doc, tmp_path / "no.pdf", settings(tmp_path), compute=compute) == {}
    )
    assert compute.calls == []
    doc["pages"][1]["approved"] = {"markdown": "# Memo\n正文", "sha256": "text-2"}
    assert supplemental_candidates(doc, tmp_path / "no.pdf", settings(tmp_path), compute=compute)[
        "entry-0"
    ]


@pytest.mark.parametrize(
    "status", ["matched", "matched_manual", "matched_verified", "unlinked_manual"]
)
def test_completed_or_manually_unlinked_entries_do_not_run_models(tmp_path, status):
    doc = document(page(2, "# Memo\n正文", headings=["Memo"]))
    doc["navigation"]["entries"][0]["status"] = status
    compute = Compute()
    assert (
        supplemental_candidates(doc, tmp_path / "no.pdf", settings(tmp_path), compute=compute) == {}
    )
    assert compute.calls == []
