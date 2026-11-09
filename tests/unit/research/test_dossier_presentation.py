"""Synthetic reader metadata and derived discovery contracts, without live services."""

import json
import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from hrs.library.records import text_digest
from hrs.research.api import CardReadingIdentity
from hrs.research.discovery import discovery_records
from hrs.research.dossier_export import export_dossier
from hrs.research.models import DOSSIER_SOURCE_FIELDS
from hrs.research.projection import card_summary, located_card, reading_identity

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "web/src/features/research/dossier-revision-contract.fixture.json"
)


@pytest.fixture
def source_card():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    card = fixture["initial"]
    card.update(
        id="0123456789abcdef0123456789abcdef",
        book_id="synthetic-book",
        run_id="synthetic-research",
        state="verified",
        version=1,
        card_only=True,
    )
    card["metadata"]["archive_id"] = {
        "value": "档号-9876",
        "certainty": "stated",
        "evidence_ids": ["bib"],
        "note": "",
    }
    article = fixture["article"]
    parts = [
        {
            "text": article["text"][part["start"] : part["end"]],
            "source": {"pages": [7], "image_sha256": "first-page-image"},
        }
        for part in article["parts"]
    ]
    # All quotations are on page 7; the same article continues on page 8.
    continuation = "附记：同篇未引用的后续内容。\n"
    article.update(
        text=article["text"] + continuation,
        parts=[*parts, {"text": continuation, "source": {"pages": [8]}}],
    )
    article["text_sha256"] = text_digest(article["text"])
    snapshot = {
        "articles": [article],
        "source_run_id": "synthetic-publication",
        "book_id": "synthetic-book",
        "book_title": "合成测试资料",
        "publication_sha256": "publication-hash",
    }
    return card, snapshot


def test_reader_summary_uses_the_whole_article_and_distinguishes_card_id_from_archive(source_card):
    card, snapshot = source_card
    projected = located_card(card, snapshot)
    summary = card_summary(projected)

    assert {span["reading_target"]["physical_page"] for span in projected["evidence"]} == {7}
    assert summary["pages"] == [7, 8]
    assert summary["display_id"] == "CARD-0123456789AB"
    assert summary["display_id"] != card["metadata"]["archive_id"]["value"]
    assert summary["authors"] == "李四"
    assert summary["formation_date"] == card["formation_date"]
    assert summary["publication_date"] == card["dossier"]["citation"]["publication_date"]
    assert summary["source_citation"] == card["dossier"]["citation"]["full"]["value"]
    assert summary["printed_pages"] == "第12—14页"
    assert summary["citation_completeness"] == "complete"
    assert summary["dossier_available"] is True
    identity = CardReadingIdentity.model_validate(summary)
    assert identity.pages == [7, 8]
    assert identity.formation_date.normalized == "1936-05"
    assert identity.publication_date.normalized == "1936"


@pytest.mark.parametrize("printed_pages", ["第12—14页", ""])
def test_printed_pages_are_source_values_and_never_pdf_ordinals(source_card, printed_pages):
    card, snapshot = source_card
    citation = card["dossier"]["citation"]
    citation["printed_pages"] = {
        "value": printed_pages,
        "certainty": "stated" if printed_pages else "unknown",
        "evidence_ids": ["bib"] if printed_pages else [],
        "note": "",
    }

    summary = card_summary(located_card(card, snapshot))

    assert summary["pages"] == [7, 8]
    assert summary["printed_pages"] == printed_pages


def test_unknown_citation_preserves_unknown_date_and_uses_only_known_publication(source_card):
    card, snapshot = source_card
    citation = card["dossier"]["citation"]
    for field in ("full", "short", "printed_pages"):
        citation[field] = {"value": "", "certainty": "unknown", "evidence_ids": [], "note": ""}
    citation.update(publication_date={"certainty": "unknown"}, completeness="unknown")

    summary = card_summary(located_card(card, snapshot))

    assert summary["source_citation"] == "调查通讯"
    assert summary["publication_date"] == {"certainty": "unknown"}
    assert summary["citation_completeness"] == "unknown"
    assert summary["printed_pages"] == ""
    identity = CardReadingIdentity.model_validate(summary)
    assert identity.publication_date.normalized is None
    assert identity.publication_date.text == ""


def test_locating_and_summarizing_never_mutates_native_card_or_source_snapshot(source_card):
    card, snapshot = source_card
    card["evidence"][0]["sources"] = [{"pages": [7], "text": "retained native source text"}]
    native_card, native_snapshot = deepcopy(card), deepcopy(snapshot)

    projected = located_card(card, snapshot)
    card_summary(projected)

    assert card == native_card
    assert snapshot == native_snapshot
    assert "reading_target" not in card["evidence"][0]
    assert "text" not in projected["evidence"][0]["sources"][0]
    assert projected["dossier"] == card["dossier"]
    assert projected["dossier"] is not card["dossier"]


@pytest.mark.parametrize("has_article_id", [False, True])
def test_sparse_legacy_cards_remain_readable_without_id_or_dossier(source_card, has_article_id):
    _, snapshot = source_card
    legacy = {"metadata": {"publication": {"value": "旧刊物"}}}
    if has_article_id:
        legacy["article_id"] = snapshot["articles"][0]["id"]

    summary = card_summary(located_card(legacy, snapshot))

    assert summary["display_id"] == ""
    assert summary["pages"] == ([7, 8] if has_article_id else [])
    assert summary["source_citation"] == "旧刊物"
    assert summary["printed_pages"] == ""
    assert summary["dossier_available"] is False
    assert summary["citation_completeness"] == "unknown"
    assert summary["processing_mode"] == "legacy_research"
    assert reading_identity(legacy)["formation_date"] == {"certainty": "unknown"}
    assert CardReadingIdentity.model_validate(summary).publication_date.certainty == "unknown"


def test_dossier_discovery_indexes_every_interpretation_but_never_personal_notes(source_card):
    card, _ = source_card
    dossier = card["dossier"]
    expected = []
    for field in DOSSIER_SOURCE_FIELDS:
        for key in ("value", "note"):
            text = f"dossier-{field}-{key}"
            dossier[field][key] = text
            expected.append(text)
    for key in ("title", "claim", "applicability_limit", "corroboration_needed"):
        text = f"unit-{key}-text"
        dossier["argument_units"][0][key] = text
        expected.append(text)
    for key in ("full", "short", "printed_pages"):
        text = f"citation-{key}-text"
        dossier["citation"][key]["value"] = text
        expected.append(text)
    card.update(human_notes="PRIVATE-READING-NOTES", notes_version=21, notes_updated_at=123.0)
    native = deepcopy(card)
    pending = {**deepcopy(card), "id": "pending-card", "state": "human_pending"}

    rows = discovery_records(
        [card, pending], [], workspace_id="synthetic-workspace", generation="synthetic-generation"
    )

    text = "\n".join(row["text"] for row in rows)
    assert all(value in text for value in expected)
    assert "PRIVATE-READING-NOTES" not in text
    assert {row["record_id"] for row in rows} == {card["id"]}
    assert all(row["record_kind"] == "card" for row in rows)
    assert all(row["derived"] is True and row["source_evidence"] is False for row in rows)
    assert all("human_notes" not in row and "notes_version" not in row for row in rows)
    assert card == native


def readable_export(source_card, **flags):
    card, snapshot = source_card
    card = located_card(card, snapshot)
    card.update(source_run_id=snapshot["source_run_id"], **flags)
    return export_dossier(
        card,
        {article["id"]: article for article in snapshot["articles"]},
        base_url="http://localhost:18170",
        render_evidence=lambda span, _article, _prefix: span["quote"],
    )


@pytest.mark.parametrize(
    ("flags", "warning"),
    [
        ({"source_current": False}, "历史卡片：来源已换版"),
        ({"needs_update": True}, "来源正在修订"),
        ({"superseded": True}, "此研究修订已有后续版本"),
        ({"research_current": False}, "此研究修订已有后续版本"),
    ],
)
def test_dossier_export_exposes_historical_status_before_the_readable_body(
    source_card, flags, warning
):
    markdown = readable_export(source_card, **flags)
    assert warning in markdown[: markdown.index("## 一、基本信息")]


def test_readable_export_keeps_source_viewpoints_and_disputed_status_explicit(source_card):
    card, _ = source_card
    card["items"][0].update(kind="argument", epistemic_state="disputed", attribution="")
    markdown = readable_export(source_card)
    readable = markdown[: markdown.index("<details>")]
    core = readable.split("## 四、核心内容", 1)[1].split("## 五、", 1)[0]
    assert "**内容性质**：原作者观点" in core
    assert "**认识状态**：有争议" in core


def test_dossier_export_uses_normalized_reader_target_for_exact_highlighting(source_card):
    card, snapshot = source_card
    card = located_card(card, snapshot)
    span = card["evidence"][0]
    span["reading_target"] = {
        "chapter_id": "canonical-chapter",
        "physical_page": 7,
        "start": span["start"] + 2,
        "end": span["end"] - 1,
    }
    span["text_sha256"] = snapshot["articles"][0]["text_sha256"]
    card["source_run_id"] = snapshot["source_run_id"]
    markdown = export_dossier(
        card,
        {article["id"]: article for article in snapshot["articles"]},
        base_url="http://localhost:18170",
        render_evidence=lambda item, _article, _prefix: item["quote"],
    )
    link = re.search(r"\[对照原文\]\(([^)]+)\)", markdown).group(1)
    query = parse_qs(urlsplit(link).fragment.split("?", 1)[1])
    assert query["chapter"] == ["canonical-chapter"]
    assert query["start"] == [str(span["reading_target"]["start"])]
    assert query["end"] == [str(span["reading_target"]["end"])]
    assert query["textSha"] == [span["text_sha256"]]
