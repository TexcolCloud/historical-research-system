"""Real PDF object fixtures exercise CPU routing without a recognizer/model."""

import hashlib
import json

import pytest
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from hrs_codex import render
from hrs_codex.render import render_document


def pdf_fixture(path, specs):
    writer = PdfWriter()
    for spec in specs:
        page = writer.add_blank_page(width=200, height=200)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
        if spec.get("bad_unicode"):
            cmap = DecodedStreamObject()
            cmap.set_data(
                b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
                b"1 begincodespacerange <00> <FF> endcodespacerange "
                b"1 beginbfchar <41> <E000> endbfchar endcmap end end"
            )
            font[NameObject("/ToUnicode")] = writer._add_object(cmap)
        resources = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        content = spec.get("prefix_content", b"")
        if spec.get("image"):
            image = DecodedStreamObject()
            image.set_data(bytes([0, 0, 0]) * 20 * 20)
            image.update(
                {
                    NameObject("/Type"): NameObject("/XObject"),
                    NameObject("/Subtype"): NameObject("/Image"),
                    NameObject("/Width"): NumberObject(20),
                    NameObject("/Height"): NumberObject(20),
                    NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                    NameObject("/BitsPerComponent"): NumberObject(8),
                }
            )
            resources[NameObject("/XObject")] = DictionaryObject(
                {NameObject("/Im1"): writer._add_object(image)}
            )
            matrix = b"200 0 0 200 0 0" if spec.get("full_image") else b"80 0 0 40 100 20"
            content += b"q " + matrix + b" cm /Im1 Do Q\n"
        if spec.get("text"):
            content += (
                b"BT /F1 14 Tf "
                + (b"3 Tr " if spec.get("hidden") else b"")
                + (b"1 1 1 rg " if spec.get("white") else b"")
                + (b"195 150 Td " if spec.get("offpage") else b"10 150 Td ")
                + b"("
                + spec["text"].encode("ascii")
                + b") Tj ET\n"
            )
        content += spec.get("extra_content", b"")
        if spec.get("form"):
            form = DecodedStreamObject()
            form.set_data(
                spec.get("form_content", b"BT /F1 12 Tf 10 50 Td (Form text) Tj ET 100 10 5 5 re f")
            )
            form.update(
                {
                    NameObject("/Type"): NameObject("/XObject"),
                    NameObject("/Subtype"): NameObject("/Form"),
                    NameObject("/BBox"): ArrayObject(
                        [NumberObject(n) for n in spec.get("form_bbox", [0, 0, 150, 100])]
                    ),
                    NameObject("/Resources"): resources,
                }
            )
            inner_reference = writer._add_object(form)
            if spec.get("nested"):
                outer = DecodedStreamObject()
                outer.set_data(b"q 1 0 0 1 5 5 cm /Inner Do Q")
                outer.update(
                    {
                        NameObject("/Type"): NameObject("/XObject"),
                        NameObject("/Subtype"): NameObject("/Form"),
                        NameObject("/BBox"): ArrayObject(
                            [NumberObject(n) for n in [0, 0, 160, 110]]
                        ),
                        NameObject("/Resources"): DictionaryObject(
                            {
                                NameObject("/XObject"): DictionaryObject(
                                    {NameObject("/Inner"): inner_reference}
                                )
                            }
                        ),
                    }
                )
                inner_reference = writer._add_object(outer)
            # Give the page separate resources to avoid recursively including the Form in itself.
            resources = DictionaryObject(dict(resources))
            resources[NameObject("/XObject")] = DictionaryObject(
                {NameObject("/Form1"): inner_reference}
            )
            content += b"q 1.2 0 0 1.2 10 20 cm /Form1 Do Q"
        if spec.get("annotation"):
            subtype = spec["annotation"]
            annotation = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Annot"),
                    NameObject("/Subtype"): NameObject(subtype),
                    NameObject("/Rect"): ArrayObject([NumberObject(n) for n in [10, 10, 30, 30]]),
                    NameObject("/Border"): ArrayObject(
                        [
                            NumberObject(0),
                            NumberObject(0),
                            NumberObject(spec.get("annotation_border", 0)),
                        ]
                    ),
                }
            )
            page[NameObject("/Annots")] = ArrayObject([writer._add_object(annotation)])
        if spec.get("crop"):
            page[NameObject("/CropBox")] = ArrayObject([NumberObject(n) for n in [0, 10, 200, 200]])
        if spec.get("rotate"):
            page.rotate(90)
        stream = DecodedStreamObject()
        stream.set_data(content)
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = resources
    writer.write(path)
    return path


def plans(tmp_path, specs):
    source = pdf_fixture(tmp_path / "source.pdf", specs)
    manifest = render_document(source, tmp_path / "render")
    return [page["extraction_plan"] for page in manifest["pages"]]


def test_native_scan_hidden_ocr_and_blank_are_classified_per_physical_page(tmp_path):
    result = plans(
        tmp_path,
        [
            {"text": "A"},
            {"image": True, "full_image": True},
            {"image": True, "full_image": True, "text": "Hidden OCR", "hidden": True},
            {},
        ],
    )
    assert [p["route"] for p in result] == ["native", "ocr", "ocr", "blank_candidate"]
    assert result[0]["native_markdown"] == "A"
    assert result[0]["native_sha256"] == hashlib.sha256(b"A").hexdigest()
    assert "hidden_or_unproven_text_visibility" in result[2]["reason_codes"]
    assert all(not p["machine_approval"] for p in result)
    # Even a single visible character is a candidate; there is no text-count approval shortcut.
    assert result[0]["probe_evidence"]["characters"][0]["unicode"] == ord("A")


def test_mixed_regions_have_normalized_top_left_coordinates(tmp_path):
    plan = plans(tmp_path, [{"text": "Caption", "image": True}])[0]
    assert plan["route"] == "mixed"
    assert plan["native_markdown"] == "Caption"
    assert plan["regions"] == [
        {"region_id": "image-1", "bbox": [0.5, 0.7, 0.9, 0.9], "kind": "image"}
    ]
    for char in plan["probe_evidence"]["characters"]:
        left, top, right, bottom = char["bbox"]
        assert 0 <= left < right <= 1 and 0 <= top < bottom <= 1


def test_multiline_columns_remain_native_candidate_without_approval(tmp_path):
    plan = plans(
        tmp_path,
        [
            {
                "text": "Left",
                "extra_content": b"BT /F1 14 Tf 100 150 Td (Right) Tj 0 -25 Td (Next line) Tj ET",
            }
        ],
    )[0]
    assert plan["route"] == "native"
    assert all(word in plan["native_markdown"] for word in ["Left", "Right", "Next line"])
    assert plan["machine_approval"] is False


def test_localized_path_outlines_are_grouped_as_unknown_regions(tmp_path):
    plan = plans(
        tmp_path,
        [{"text": "Body", "extra_content": b"100 20 5 10 re f 110 20 5 10 re f 120 20 5 10 re f"}],
    )[0]
    assert plan["route"] == "mixed"
    assert plan["regions"] == [
        {"region_id": "path-1", "bbox": [0.5, 0.85, 0.625, 0.9], "kind": "unknown"}
    ]
    assert len(plan["probe_evidence"]["path_boxes"]) == 3
    assert sorted(plan["probe_evidence"]["path_groups"][0]["path_indices"]) == [0, 1, 2]


@pytest.mark.parametrize(
    "content,reason",
    [
        (b"10 150 20 10 re f", "path_native_text_overlap"),
        (b"0 0 200 200 re f", "path_region_not_localized"),
    ],
)
def test_ambiguous_or_overlapping_paths_require_full_page_ocr(tmp_path, content, reason):
    plan = plans(tmp_path, [{"text": "Body", "extra_content": content}])[0]
    assert plan["route"] == "ocr"
    assert reason in plan["reason_codes"]


def test_straight_table_chart_lines_preserved_for_structure_review(tmp_path):
    plan = plans(
        tmp_path,
        [{"text": "Body", "extra_content": b"0.5 w 10 100 m 150 100 l S 50 50 m 50 120 l S"}],
    )[0]
    assert plan["route"] == "native"
    assert plan["regions"] == []
    assert len(plan["probe_evidence"]["structural_lines"]) == 2
    assert plan["probe_evidence"]["full_page_structure_review_required"] is True
    assert all(
        line["role"] == "unclassified" for line in plan["probe_evidence"]["structural_lines"]
    )


@pytest.mark.parametrize("nested", [False, True])
def test_form_translation_scale_and_nested_parent_coordinates(tmp_path, nested):
    plan = plans(tmp_path, [{"form": True, "nested": nested}])[0]
    assert plan["route"] == "mixed"
    assert plan["native_markdown"] == "Form text"
    assert len(plan["probe_evidence"]["form_objects"]) == (2 if nested else 1)
    shift = 6 if nested else 0
    expected = [
        (130 + shift) / 200,
        (200 - 38 - shift) / 200,
        (136 + shift) / 200,
        (200 - 32 - shift) / 200,
    ]
    assert plan["regions"][0]["bbox"] == pytest.approx(expected)


@pytest.mark.parametrize("annotation,route", [("/Link", "native"), ("/Text", "ocr")])
def test_invisible_links_do_not_invalidate_native_but_visible_notes_do(tmp_path, annotation, route):
    plan = plans(tmp_path, [{"text": "Body", "annotation": annotation}])[0]
    assert plan["route"] == route
    assert plan["probe_evidence"]["annotations"][0]["invisible_link"] == (annotation == "/Link")


def test_off_page_text_and_printer_marks_are_excluded_with_evidence(tmp_path):
    plan = plans(
        tmp_path,
        [
            {
                "text": "Visible",
                "extra_content": b"BT /F1 12 Tf -150 50 Td (Offpage) Tj ET -100 10 5 5 re f",
            }
        ],
    )[0]
    assert plan["route"] == "native"
    assert plan["native_markdown"] == "Visible"
    assert len(plan["probe_evidence"]["cropped_characters"]) == len("Offpage")
    assert len(plan["probe_evidence"]["cropped_objects"]) == 2


def test_form_bbox_clipping_excludes_invisible_text_without_losing_visible_text(tmp_path):
    plan = plans(
        tmp_path,
        [
            {
                "form": True,
                "form_bbox": [0, 0, 120, 40],
                "form_content": b"BT /F1 12 Tf 10 20 Td (Kept) Tj 0 50 Td (Clipped) Tj ET",
            }
        ],
    )[0]
    assert plan["route"] == "native"
    assert plan["native_markdown"] == "Kept"
    assert len(plan["probe_evidence"]["cropped_characters"]) == len("Clipped")


def test_visible_link_border_and_unproven_curved_clip_stay_ocr(tmp_path):
    plan = plans(tmp_path, [{"text": "Body", "annotation": "/Link", "annotation_border": 1}])[0]
    assert plan["route"] == "ocr"
    assert "annotations_require_full_page_ocr" in plan["reason_codes"]
    source = pdf_fixture(
        tmp_path / "curve.pdf",
        [
            {
                "extra_content": b"q 10 10 m 10 100 150 100 150 10 c h W n BT /F1 12 Tf 20 20 Td (Body) Tj ET Q"
            }
        ],
    )
    clipped = render_document(source, tmp_path / "curve")["pages"][0]["extraction_plan"]
    assert clipped["route"] == "ocr"
    assert "nonrectangular_clipping_unproven" in clipped["reason_codes"]


def test_pdfium_line_end_hyphen_is_recorded_normalization_not_bad_unicode(tmp_path):
    plan = plans(
        tmp_path, [{"extra_content": b"BT /F1 12 Tf 10 150 Td (cross-) Tj 0 -20 Td (word) Tj ET"}]
    )[0]
    assert plan["route"] == "native"
    assert "cross-" in plan["native_markdown"] and "word" in plan["native_markdown"]
    assert "\x02" not in plan["native_markdown"]
    assert any(
        row["reason"] == "pdfium_hyphen_marker"
        for row in plan["probe_evidence"]["text_normalizations"]
    )


def test_white_background_before_text_is_native_but_occluding_rectangle_is_not(tmp_path):
    plan = plans(tmp_path, [{"text": "Body", "prefix_content": b"q 1 1 1 rg 0 0 200 200 re f Q "}])[
        0
    ]
    assert plan["route"] == "native"
    assert len(plan["probe_evidence"]["white_rectangles"]) == 1
    source = pdf_fixture(
        tmp_path / "occluded.pdf",
        [{"text": "Body", "extra_content": b"q 1 1 1 rg 0 0 200 200 re f Q"}],
    )
    occluded = render_document(source, tmp_path / "occluded")["pages"][0]["extraction_plan"]
    assert occluded["route"] == "ocr"
    assert "white_rectangle_may_occlude_native_text" in occluded["reason_codes"]


def test_disconnected_printer_strokes_do_not_create_a_false_full_page_region(tmp_path):
    plan = plans(
        tmp_path, [{"text": "Body", "extra_content": b"0.5 w 1 1 m 10 1 l 190 190 m 199 190 l S"}]
    )[0]
    assert plan["route"] == "native"
    assert plan["regions"] == []
    assert len(plan["probe_evidence"]["structural_lines"]) == 2


@pytest.mark.parametrize(
    "spec,reason",
    [
        ({"text": "A", "image": True, "full_image": True}, "image_text_overlap"),
        ({"text": "A", "white": True}, "hidden_or_unproven_text_visibility"),
        ({"text": "A", "bad_unicode": True}, "unreliable_unicode_mapping"),
        ({"text": "Out of bounds", "offpage": True}, "partially_clipped_character"),
        ({"text": "A", "rotate": True}, "rotated_page_geometry"),
    ],
)
def test_unproven_native_text_falls_back_to_full_page_ocr(tmp_path, spec, reason):
    plan = plans(tmp_path, [spec])[0]
    assert plan["route"] == "ocr"
    assert reason in plan["reason_codes"]
    assert plan["native_markdown"] == ""
    assert plan["regions"][0]["bbox"] == [0.0, 0.0, 1.0, 1.0]


def test_cropbox_coordinates_and_probe_failure_do_not_prevent_rendering(tmp_path, monkeypatch):
    plan = plans(tmp_path, [{"text": "A", "image": True, "crop": True}])[0]
    assert plan["route"] == "mixed"
    assert plan["regions"][0]["bbox"] == pytest.approx([0.5, 140 / 190, 0.9, 180 / 190])
    source = pdf_fixture(tmp_path / "second.pdf", [{"text": "A"}])
    import hrs_codex.routing as routing

    def fail(page):
        raise RuntimeError("probe unavailable")

    monkeypatch.setattr(routing, "_inspect", fail)
    result = render_document(source, tmp_path / "fallback")
    assert result["pages"][0]["width"] == 600
    assert result["pages"][0]["extraction_plan"]["reason_codes"] == ["probe_failed"]


def test_image_routing_and_frozen_manifest_are_not_rewritten(tmp_path):
    source = tmp_path / "scan.png"
    Image.new("RGB", (10, 20), "white").save(source)
    result = render_document(source, tmp_path / "render")
    assert result["pages"][0]["extraction_plan"]["route"] == "ocr"
    manifest_path = tmp_path / "render/render.json"
    del result["pages"][0]["extraction_plan"]
    frozen = json.dumps(result, ensure_ascii=False).encode("utf-8")
    manifest_path.write_bytes(frozen)
    assert "extraction_plan" not in render.render_document(source, tmp_path / "render")["pages"][0]
    assert manifest_path.read_bytes() == frozen
