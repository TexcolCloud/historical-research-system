"""Markdown export resolves note-internal links without changing source ownership."""

import re
from copy import deepcopy

from hrs.library.footnotes import export_footnotes, resolve_footnotes


def parts(*pages):
    return [{"text": text, "source": {"pages": [number]}} for number, text in enumerate(pages, 1)]


def assert_closed_markdown(text):
    definitions = re.findall(r"(?m)^ {0,3}\[\^([^\]\n]+)\]:", text)
    references = re.findall(r"(?<!\\)\[\^([^\]\n]+)\](?!:)", text)
    assert len(definitions) == len(set(definitions))
    assert set(references) <= set(definitions)


def test_internal_references_follow_same_page_when_local_labels_repeat():
    pages = (
        "First[^1] and[^2].\n\n[^1]: First source, same[^2].\n[^2]: First followup, same[^1].\n\n",
        "Second[^1] and[^2].\n\n[^1]: Second source, same[^2].\n[^2]: Second followup, same[^1].\n\n",
    )
    text = "".join(pages)
    links = resolve_footnotes(text, parts(*pages))
    original = deepcopy(links)
    exported = export_footnotes(text, links, "c1", parts=parts(*pages))
    assert "[^c1-1]: First source, same[^c1-2]." in exported
    assert "[^c1-2]: First followup, same[^c1-1]." in exported
    assert "[^c1-3]: Second source, same[^c1-4]." in exported
    assert "[^c1-4]: Second followup, same[^c1-3]." in exported
    assert_closed_markdown(exported)
    assert text == "".join(pages) and links == original


def test_export_keeps_definitions_reachable_only_from_inside_other_notes():
    text = "Body[^1].\n\n[^1]: See[^2].\n[^2]: See[^3].\n[^3]: Back to[^2].\n\n"
    exported = export_footnotes(
        text, resolve_footnotes(text, parts(text)), "chapter", parts=parts(text)
    )
    assert "[^chapter-2]: See[^chapter-3]." in exported
    assert "[^chapter-3]: Back to[^chapter-2]." in exported
    assert_closed_markdown(exported)


def test_duplicate_same_page_targets_are_exported_as_literal_references():
    text = "Body[^1].\n\n[^1]: Ambiguous[^2] and missing[^x].\n[^2]: One.\n[^2]: Two.\n\n"
    exported = export_footnotes(text, resolve_footnotes(text, parts(text)), "c", parts=parts(text))
    assert "Ambiguous\\[^2] and missing\\[^x]" in exported
    assert "One." in exported and "Two." in exported
    assert not re.search(r"(?<!\\)\[\^[2x]\](?!:)", exported)


def test_internal_reference_does_not_guess_a_unique_target_on_another_page():
    pages = ("First[^a].\n\n[^a]: Same[^b].\n\n", "Second[^b].\n\n[^b]: Other page.\n\n")
    text = "".join(pages)
    exported = export_footnotes(
        text, resolve_footnotes(text, parts(*pages)), "c", parts=parts(*pages)
    )
    assert "[^c-1]: Same\\[^b]." in exported
    assert "[^c-2]: Other page." in exported
    assert_closed_markdown(exported)


def test_indented_note_continuation_uses_the_reference_physical_page():
    pages = (
        "First[^1].\n\n[^1]: Note starts on page one.\n\n",
        "    Continued on page two, same[^2].\n\nSecond[^2].\n\n[^2]: Page two target.\n\n",
    )
    text = "".join(pages)
    exported = export_footnotes(
        text, resolve_footnotes(text, parts(*pages)), "c", parts=parts(*pages)
    )
    assert "Continued on page two, same[^c-2]." in exported
    assert_closed_markdown(exported)


def test_export_preserves_literal_code_and_existing_printed_marker_behavior():
    text = "Body① and[^2].\n\n① Same[^2], code `[^2]`.\n\n[^2]: Target.\n\n"
    exported = export_footnotes(text, resolve_footnotes(text, parts(text)), "c", parts=parts(text))
    assert "Body[^c-1] and[^c-2]." in exported
    assert "Same[^c-2], code `[^2]`." in exported


def test_export_without_page_context_keeps_internal_reference_as_literal():
    text = "Body[^1] and[^2].\n\n[^1]: Same[^2].\n[^2]: Target.\n\n"
    exported = export_footnotes(text, resolve_footnotes(text, parts(text)), "c")
    assert "[^c-1]: Same\\[^2]." in exported
    assert_closed_markdown(exported)


def test_export_rejects_source_context_that_does_not_match_reading_text():
    import pytest

    text = "Body[^1].\n\n[^1]: Note.\n"
    with pytest.raises(ValueError, match="source parts differ"):
        export_footnotes(
            text, resolve_footnotes(text, parts(text)), "c", parts=parts("Different text")
        )


def test_exported_reachable_ids_do_not_collide_with_skipped_empty_definition():
    text = "Body[^0] and[^1].\n\n[^0]:\n[^1]: See[^2].\n[^2]: Target.\n\n"
    exported = export_footnotes(text, resolve_footnotes(text, parts(text)), "c", parts=parts(text))
    assert_closed_markdown(exported)
    assert "[^c-3]: Target." in exported
