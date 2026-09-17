"""Evidence-based CPU text-layer inspection; corrupt extraction retains OCR.

PDFium is already Docling's PDF backend. This module never loads an OCR model.
Complex layout is reconstructed separately; uncertain structure uses visual
review without repeating recognition of already proven native text.
"""
import ctypes
import math
import re
import unicodedata
from contextlib import ExitStack, closing
from pathlib import Path

from .native_layout import POLICY, extract_layout, structure, validate_layout
from .provenance import text_hash
from .utils import sha256

# Retained only for frozen v1 runs/checkpoints. New runs use structured v2.
LEGACY_POLICY = "native-pdf-simple-text-v1"


def compact(text):
    return re.sub(r"\s+", "", text)


def normalized_text(text):
    # Preserve word boundaries: 'now here' and 'nowhere' are not equivalent.
    return re.sub(r"\s+", " ", text).strip()


def issues(evidence):
    """Re-evaluate retained measurements, rather than trust a stored pass flag."""
    if evidence.get('policy') == POLICY:
        errors = validate_layout(evidence)
        if any(unicodedata.category(c) in {'Co', 'Cs', 'Cn'} or c == '\ufffd'
               or (unicodedata.category(c) == 'Cc' and c not in '\n\r\t') for c in evidence.get('raw_text', '')):
            errors.append('invalid-unicode')
        return errors
    reasons = list(evidence.get("inspection_errors", []))
    text, lines = evidence.get("text", ""), evidence.get("lines", [])
    if evidence.get("policy") != LEGACY_POLICY:
        reasons.append("unknown-native-policy")
    if len(compact(text)) < 30 or len(lines) < 2:
        reasons.append("sparse-text")
    if any(unicodedata.category(c) in {"Co", "Cs", "Cn"} or c == "\ufffd"
           or (unicodedata.category(c) == "Cc" and c not in "\n\r\t") for c in text):
        reasons.append("invalid-unicode")
    # Do not infer tables, formulas, note ownership or Markdown syntax in v1.
    if re.search(r"[<>|#*_`\[\]\\①②③④⑤⑥⑦⑧⑨]|(.)\1{7,}", text):
        reasons.append("structured-or-suspicious-text")
    if not re.search(r'[。！？.!?]', text) or sum(
        len(re.findall(r'\d+(?:[.,]\d+)*', line['text'])) >= 2 for line in lines
    ) >= 2:
        reasons.append('table-or-list-like-text')
    if not lines or compact(text) != compact("".join(line["text"] for line in lines)):
        reasons.append("incomplete-text-coverage")
    sizes = [line["font_size"] for line in lines]
    if sizes and (not all(math.isfinite(s) for s in sizes) or min(sizes) < 6 or max(sizes) > min(sizes) * 1.15):
        reasons.append("mixed-type-or-footnotes")
    if lines:
        width, height = evidence["page_size"]
        boxes = [line["bbox"] for line in lines]
        if not all(all(math.isfinite(v) for v in box) and
                   0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height for box in boxes):
            reasons.append("invalid-text-geometry")
        if max(b[0] for b in boxes) - min(b[0] for b in boxes) > max(sizes) * 0.5:
            reasons.append("multiple-columns-or-indentation")
        if any(a[3] > b[1] + 1 for a, b in zip(boxes, boxes[1:])):
            reasons.append("overlap-or-reading-order")
        gaps = [b[1] - a[1] for a, b in zip(boxes, boxes[1:])]
        if gaps and (max(gaps) > max(sizes) * 2.2 or max(gaps) - min(gaps) > max(sizes) * 0.4):
            reasons.append('paragraph-boundary-or-detached-note')
    return sorted(set(reasons))


def inspect_pdf(source, *, enabled):
    """Return physical-page decisions; a failed probe falls back, never passes."""
    if not enabled or Path(source).suffix.lower() != ".pdf":
        return {}
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    source_hash = sha256(Path(source))
    decisions = {}
    policy = enabled if isinstance(enabled, str) else POLICY
    probe_errors = (OSError, ValueError, TypeError, AttributeError, RuntimeError, ctypes.ArgumentError)
    with ExitStack() as stack:
        document = stack.enter_context(pdfium.PdfDocument(source))
        layout_pages, layout_error = None, None
        if policy == POLICY:
            import pdfplumber
            from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException
            probe_errors += (MalformedPDFException, PdfminerException)
            try:
                layout_document = stack.enter_context(pdfplumber.open(source, laparams={'line_margin': 0.2, 'boxes_flow': 0.5}))
                layout_pages = layout_document.pages
                if len(layout_pages) != len(document):
                    raise ValueError('layout-page-count-mismatch')
            except probe_errors as exc:
                layout_error = f'{type(exc).__name__}: {exc}'
        for index in range(len(document)):
            evidence = {"policy": policy, "page": index + 1, "source_sha256": source_hash,
                        "text": "", "lines": [], "inspection_errors": []}
            try:
                with closing(document[index]) as page, closing(page.get_textpage()) as textpage:
                    evidence.update(_inspect_page(page, textpage, raw, structured=policy == POLICY))
                if layout_error:
                    raise ValueError('layout-parser-unavailable: ' + layout_error)
                if layout_pages is not None:
                    layout_page = layout_pages[index]
                    try:
                        evidence.update(extract_layout(layout_page, evidence))
                    finally:
                        layout_page.close()
            except probe_errors as exc:
                evidence["inspection_errors"].append("probe-failed:" + type(exc).__name__)
                evidence['probe_error'] = str(exc)
            evidence["reasons"] = issues(evidence)
            evidence["route"] = "ocr" if evidence["reasons"] else "native"
            decisions[index + 1] = evidence
    return decisions


def _inspect_page(page, textpage, raw, *, structured=False):
    width, height = page.get_size()
    errors = []
    if page.get_rotation() or (not structured and raw.FPDFPage_GetAnnotCount(page) != 0):
        errors.append("rotation-or-annotations")
    for obj in page.get_objects(textpage=textpage):
        if obj.type != raw.FPDF_PAGEOBJ_TEXT:
            errors.append("non-text-page-object")
            continue
        matrix = obj.get_matrix()
        colors = [ctypes.c_uint() for _ in range(4)]
        clip = raw.FPDFPageObj_GetClipPath(obj)
        if (raw.FPDFTextObj_GetTextRenderMode(obj) != 0
                or not raw.FPDFPageObj_GetFillColor(obj, *colors)
                or colors[3].value != 255
                or (min(c.value for c in colors[:3]) > 245 if structured else max(c.value for c in colors[:3]) > 80)
                or abs(matrix.b) > 0.001 or abs(matrix.c) > 0.001
                or matrix.a <= 0 or matrix.d <= 0
                or (clip and raw.FPDFClipPath_CountPaths(clip) > 0)):
            errors.append("hidden-transformed-or-clipped-text")
    text = textpage.get_text_range(errors="strict").replace("\r\n", "\n").replace("\r", "\n")
    lines, current, sizes = [], [], []

    def finish():
        if current:
            boxes = [item[1] for item in current]
            lines.append({"text": "".join(item[0] for item in current),
                          "bbox": [min(b[0] for b in boxes), min(b[1] for b in boxes),
                                   max(b[2] for b in boxes), max(b[3] for b in boxes)],
                          "font_size": max(sizes)})
            if max(sizes) > min(sizes) * 1.15:
                errors.append("inline-font-change")
            current.clear()
            sizes.clear()

    for index in range(textpage.count_chars()):
        code = raw.FPDFText_GetUnicode(textpage, index)
        character = chr(code)
        if character in "\r\n":
            finish()
            continue
        if character.isspace():
            continue
        if (raw.FPDFText_HasUnicodeMapError(textpage, index) != 0
                or raw.FPDFText_IsGenerated(textpage, index) != 0):
            errors.append("unreliable-unicode-mapping")
        left, bottom, right, top = textpage.get_charbox(index)
        box = [left, height - top, right, height - bottom]
        size = raw.FPDFText_GetFontSize(textpage, index)
        if current:
            prior = current[-1][1]
            if left < prior[2] - 1 or left - prior[2] > size * 0.8 or abs(box[1] - prior[1]) > size * 0.6:
                errors.append("ambiguous-inline-order-or-columns")
        current.append((character, box))
        sizes.append(size)
    finish()
    raw_lines = [line for line in text.splitlines() if line.strip()]
    if len(raw_lines) == len(lines):
        for line, original in zip(lines, raw_lines):
            if compact(line['text']) == compact(original):
                line['text'] = original
    return {"text": text, "lines": lines, "page_size": [width, height],
            "coordinate_basis": "pdf-points-top-left", "inspection_errors": sorted(set(errors))}


def native_eligible(page):
    """Bind CPU acceptance to the measured text, page, source and rendered image."""
    evidence = page.get("native_evidence") or {}
    try:
        if issues(evidence) or evidence.get('visual_reasons') or evidence["page"] != page["page"]:
            return False
        conversion = page["conversion_evidence"]
        if (evidence["source_sha256"] != conversion["source_sha256"]
                or conversion["image_sha256"] != sha256(Path(page["image_path"]))):
            return False
        exported = page.get("layout_cleanup", {}).get("original_text", page["text"])
        if not export_matches(exported, evidence):
            return False
        if exported != page["text"]:
            from .artifacts import clean_page_numbers
            original = {k: v for k, v in page.items() if k != "layout_cleanup"}
            if clean_page_numbers({**original, "text": exported})["text"] != page["text"]:
                return False
        return text_hash(exported) == evidence["exported_text_sha256"]
    except (KeyError, TypeError, ValueError, OSError):
        return False


def export_matches(text, evidence):
    if evidence.get('policy') == POLICY:
        return structure(text) == structure(evidence['text'])
    return normalized_text(text) == normalized_text(evidence['text'])
