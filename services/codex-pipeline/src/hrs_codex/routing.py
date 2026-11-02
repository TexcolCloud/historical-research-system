"""CPU-only PDF candidate routing. No route is a content approval."""

import ctypes
import hashlib
import math
import unicodedata

import pypdfium2 as pdfium
import pypdfium2.raw as raw

POLICY = "codex-pdf-routing-v1"


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plan(route, reasons, text="", regions=None, evidence=None):
    return {
        "policy": POLICY,
        "route": route,
        "reason_codes": sorted(set(reasons)),
        "native_markdown": text,
        "native_sha256": _hash(text),
        "regions": regions or [],
        "probe_evidence": evidence or {},
        "machine_approval": False,
    }


def image_plan():
    return _plan(
        "ocr",
        ["image_source"],
        regions=[{"region_id": "page", "bbox": [0.0, 0.0, 1.0, 1.0], "kind": "image"}],
    )


def _box(bounds, canvas):
    left, bottom, right, top = bounds
    x0, y0, x1, y1 = canvas
    if not all(math.isfinite(v) for v in (*bounds, *canvas)):
        raise ValueError("nonfinite_geometry")
    if not (x0 <= left < right <= x1 and y0 <= bottom < top <= y1):
        raise ValueError("out_of_page_geometry")
    return [
        (left - x0) / (x1 - x0),
        (y1 - top) / (y1 - y0),
        (right - x0) / (x1 - x0),
        (y1 - bottom) / (y1 - y0),
    ]


def _overlaps(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def _intersection(bounds, canvas):
    if not all(math.isfinite(v) for v in (*bounds, *canvas)):
        raise ValueError("nonfinite_geometry")
    clipped = [
        max(bounds[0], canvas[0]),
        max(bounds[1], canvas[1]),
        min(bounds[2], canvas[2]),
        min(bounds[3], canvas[3]),
    ]
    return clipped if clipped[0] < clipped[2] and clipped[1] < clipped[3] else None


def _objects(page, form=None, parent=None, depth=0):
    """PDFium object bounds include the object's matrix, but not ancestor Forms."""
    if depth > 30:
        raise ValueError("form_nesting_limit")
    parent = parent or pdfium.PdfMatrix()
    for obj in page.get_objects(max_depth=1, form=form):
        bounds = parent.on_rect(*obj.get_bounds())
        yield obj, bounds, parent, depth
        if obj.type == raw.FPDF_PAGEOBJ_FORM:
            yield from _objects(page, obj, obj.get_matrix().multiply(parent), depth + 1)


def _pointer(pointer):
    return ctypes.cast(pointer, ctypes.c_void_p).value


def _clip_canvas(obj, parent, canvas):
    """Accept proven rectangular PDF clips; curves or compound shapes stay uncertain."""
    clip = raw.FPDFPageObj_GetClipPath(obj)
    count = raw.FPDFClipPath_CountPaths(clip) if clip else 0
    visible = list(canvas)
    for index in range(max(0, count)):
        points = []
        segments = raw.FPDFClipPath_CountPathSegments(clip, index)
        if segments not in {4, 5}:
            return visible, False
        for number in range(segments):
            segment = raw.FPDFClipPath_GetPathSegment(clip, index, number)
            expected = raw.FPDF_SEGMENT_MOVETO if number == 0 else raw.FPDF_SEGMENT_LINETO
            if raw.FPDFPathSegment_GetType(segment) != expected:
                return visible, False
            x, y = ctypes.c_float(), ctypes.c_float()
            if not raw.FPDFPathSegment_GetPoint(segment, x, y):
                return visible, False
            points.append(parent.on_point(x.value, y.value))
        if (
            len(set(points)) != 4
            or len({p[0] for p in points}) != 2
            or len({p[1] for p in points}) != 2
        ):
            return visible, False
        if any(a[0] != b[0] and a[1] != b[1] for a, b in zip(points, points[1:] + points[:1])):
            return visible, False
        rectangle = [
            min(p[0] for p in points),
            min(p[1] for p in points),
            max(p[0] for p in points),
            max(p[1] for p in points),
        ]
        visible = _intersection(rectangle, visible)
        if visible is None:
            return None, True
    return visible, True


def _annotations(page):
    rows = []
    for index in range(raw.FPDFPage_GetAnnotCount(page)):
        annotation = raw.FPDFPage_GetAnnot(page, index)
        if not annotation:
            raise ValueError("annotation_unavailable")
        try:
            subtype = raw.FPDFAnnot_GetSubtype(annotation)
            x, y, width = ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
            border_known = bool(raw.FPDFAnnot_GetBorder(annotation, x, y, width))
            appearance = bool(raw.FPDFAnnot_HasKey(annotation, b"AP"))
            styled_border = bool(raw.FPDFAnnot_HasKey(annotation, b"BS"))
            invisible_link = (
                subtype == raw.FPDF_ANNOT_LINK
                and border_known
                and width.value == 0
                and not appearance
                and not styled_border
            )
            rows.append(
                {
                    "subtype": subtype,
                    "border_width": width.value if border_known else None,
                    "has_appearance": appearance,
                    "has_border_style": styled_border,
                    "invisible_link": invisible_link,
                }
            )
        finally:
            raw.FPDFPage_CloseAnnot(annotation)
    return rows


def _union(boxes):
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _path_groups(boxes, canvas):
    """Group adjacent outlines in page points; never guess their textual reading."""
    dx, dy = 8 / (canvas[2] - canvas[0]), 8 / (canvas[3] - canvas[1])
    groups = []
    for index, box in enumerate(boxes):
        group = {"bbox": box, "path_indices": [index]}
        # Revisit merges because joining two runs can bridge a third run.
        pending = True
        while pending:
            pending = False
            for prior in list(groups):
                b = group["bbox"]
                expanded = [b[0] - dx, b[1] - dy, b[2] + dx, b[3] + dy]
                if _overlaps(expanded, prior["bbox"]):
                    group["bbox"] = _union([b, prior["bbox"]])
                    group["path_indices"].extend(prior["path_indices"])
                    groups.remove(prior)
                    pending = True
        groups.append(group)
    return sorted(groups, key=lambda g: (g["bbox"][1], g["bbox"][0]))


def _straight_rule(obj):
    if raw.FPDFPath_CountSegments(obj) != 2:
        return False
    points = []
    for index in range(2):
        segment = raw.FPDFPath_GetPathSegment(obj, index)
        expected = raw.FPDF_SEGMENT_MOVETO if index == 0 else raw.FPDF_SEGMENT_LINETO
        if raw.FPDFPathSegment_GetType(segment) != expected:
            return False
        x, y = ctypes.c_float(), ctypes.c_float()
        if not raw.FPDFPathSegment_GetPoint(segment, x, y):
            return False
        points.append((x.value, y.value))
    bounds = obj.get_bounds()
    return min(bounds[2] - bounds[0], bounds[3] - bounds[1]) <= 2 and (
        abs(points[0][0] - points[1][0]) < 0.001 or abs(points[0][1] - points[1][1]) < 0.001
    )


def _white_rectangle(obj):
    fill, stroke = ctypes.c_int(), ctypes.c_int()
    colors = [ctypes.c_uint() for _ in range(4)]
    if (
        not raw.FPDFPath_GetDrawMode(obj, fill, stroke)
        or not fill.value
        or stroke.value
        or not raw.FPDFPageObj_GetFillColor(obj, *colors)
        or [c.value for c in colors] != [255, 255, 255, 255]
        or raw.FPDFPath_CountSegments(obj) not in {4, 5}
    ):
        return False
    points = []
    for index in range(raw.FPDFPath_CountSegments(obj)):
        segment = raw.FPDFPath_GetPathSegment(obj, index)
        if raw.FPDFPathSegment_GetType(segment) != (
            raw.FPDF_SEGMENT_MOVETO if index == 0 else raw.FPDF_SEGMENT_LINETO
        ):
            return False
        x, y = ctypes.c_float(), ctypes.c_float()
        if not raw.FPDFPathSegment_GetPoint(segment, x, y):
            return False
        points.append((x.value, y.value))
    return (
        len(set(points)) == 4
        and len({p[0] for p in points}) == 2
        and len({p[1] for p in points}) == 2
        and all(a[0] == b[0] or a[1] == b[1] for a, b in zip(points, points[1:] + points[:1]))
    )


def _stroke_subpaths(obj, parent):
    """Locate disconnected crop marks/diagram strokes instead of their union bbox."""
    fill, stroke = ctypes.c_int(), ctypes.c_int()
    if not raw.FPDFPath_GetDrawMode(obj, fill, stroke) or fill.value or not stroke.value:
        return None
    matrix = obj.get_matrix().multiply(parent)
    width = ctypes.c_float()
    if not raw.FPDFPageObj_GetStrokeWidth(obj, width):
        raise ValueError("stroke_width_unavailable")
    padding = max(
        0.25, width.value * max(math.hypot(matrix.a, matrix.b), math.hypot(matrix.c, matrix.d)) / 2
    )
    groups, current, curved = [], [], False

    def finish():
        if current:
            bounds = [
                min(p[0] for p in current) - padding,
                min(p[1] for p in current) - padding,
                max(p[0] for p in current) + padding,
                max(p[1] for p in current) + padding,
            ]
            straight = (
                not curved
                and len(current) >= 2
                and (len({p[0] for p in current}) == 1 or len({p[1] for p in current}) == 1)
            )
            groups.append((bounds, straight))

    for index in range(raw.FPDFPath_CountSegments(obj)):
        segment = raw.FPDFPath_GetPathSegment(obj, index)
        kind = raw.FPDFPathSegment_GetType(segment)
        if kind == raw.FPDF_SEGMENT_MOVETO:
            finish()
            current, curved = [], False
        elif kind == raw.FPDF_SEGMENT_BEZIERTO:
            curved = True
        x, y = ctypes.c_float(), ctypes.c_float()
        if not raw.FPDFPathSegment_GetPoint(segment, x, y):
            raise ValueError("stroke_point_unavailable")
        current.append(matrix.on_point(x.value, y.value))
    finish()
    return groups


def _inspect(page):
    canvas = page.get_bbox()
    evidence = {
        "page_size": list(page.get_size()),
        "page_bbox": list(canvas),
        "coordinate_basis": "normalized-top-left-ltrb",
        "characters": [],
        "text_objects": [],
        "image_boxes": [],
        "path_boxes": [],
        "structural_lines": [],
        "full_page_structure_review_required": True,
        "cropped_objects": [],
        "cropped_characters": [],
        "form_objects": [],
        "white_rectangles": [],
        "text_normalizations": [],
    }
    reasons, images, charboxes, paths = [], [], [], []
    if page.get_rotation():
        reasons.append("rotated_page_geometry")
    evidence["annotations"] = _annotations(page)
    if any(not row["invisible_link"] for row in evidence["annotations"]):
        reasons.append("annotations_require_full_page_ocr")
    objects = list(_objects(page))
    evidence["object_types"] = [obj.type for obj, _, _, _ in objects]
    object_clips = {}
    object_order = {_pointer(obj.raw): index for index, (obj, _, _, _) in enumerate(objects)}
    for object_index, (obj, bounds, parent, depth) in enumerate(objects):
        visible_canvas, clip_known = _clip_canvas(obj, parent, canvas)
        object_clips[_pointer(obj.raw)] = visible_canvas
        clipped_bounds = (
            _intersection(bounds, visible_canvas) if visible_canvas is not None else None
        )
        if clipped_bounds is None:
            evidence["cropped_objects"].append({"type": obj.type, "bounds": list(bounds)})
            continue
        if list(bounds) != clipped_bounds:
            evidence["cropped_objects"].append(
                {
                    "type": obj.type,
                    "bounds": list(bounds),
                    "visible_bounds": clipped_bounds,
                    "partial": True,
                }
            )
        if not clip_known:
            reasons.append("nonrectangular_clipping_unproven")
        if obj.type == raw.FPDF_PAGEOBJ_FORM:
            matrix = obj.get_matrix().multiply(parent)
            evidence["form_objects"].append(
                {"depth": depth, "matrix": list(matrix.get()), "bounds": list(bounds)}
            )
            continue
        if obj.type == raw.FPDF_PAGEOBJ_TEXT:
            colors = [ctypes.c_uint() for _ in range(4)]
            fill_known = bool(raw.FPDFPageObj_GetFillColor(obj, *colors))
            mode = raw.FPDFTextObj_GetTextRenderMode(obj)
            clipped = not clip_known
            matrix = obj.get_matrix().multiply(parent)
            visible = (
                mode == 0
                and fill_known
                and colors[3].value == 255
                and min(c.value for c in colors[:3]) < 245
                and not clipped
            )
            evidence["text_objects"].append(
                {
                    "render_mode": mode,
                    "fill_rgba": [c.value for c in colors] if fill_known else None,
                    "clipped": clipped,
                    "visible_candidate": visible,
                }
            )
            if not visible:
                reasons.append("hidden_or_unproven_text_visibility")
            if abs(matrix.b) > 0.001 or abs(matrix.c) > 0.001 or matrix.a <= 0 or matrix.d <= 0:
                reasons.append("transformed_text_geometry")
        elif obj.type == raw.FPDF_PAGEOBJ_IMAGE:
            box = _box(clipped_bounds, canvas)
            images.append(box)
            evidence["image_boxes"].append(box)
        elif obj.type == raw.FPDF_PAGEOBJ_PATH:
            box = _box(clipped_bounds, canvas)
            if _white_rectangle(obj):
                evidence["white_rectangles"].append({"bbox": box, "object_index": object_index})
            elif (subpaths := _stroke_subpaths(obj, parent)) is not None:
                for bounds, straight in subpaths:
                    clipped = _intersection(bounds, visible_canvas)
                    if clipped is None:
                        continue
                    subbox = _box(clipped, canvas)
                    if straight:
                        evidence["structural_lines"].append(
                            {"bbox": subbox, "role": "unclassified"}
                        )
                    else:
                        paths.append(subbox)
                        evidence["path_boxes"].append(subbox)
            elif _straight_rule(obj):
                # Preserve the geometry: a line may belong to a table or chart.
                # It requires full-page structure review, not text recognition.
                evidence["structural_lines"].append({"bbox": box, "role": "unclassified"})
            else:
                paths.append(box)
                evidence["path_boxes"].append(box)
        elif obj.type != raw.FPDF_PAGEOBJ_FORM:
            reasons.append("nontext_nonimage_objects")
    textpage = page.get_textpage()
    try:
        raw_text = textpage.get_text_range(errors="strict")
        retained, whitespace, excluded_run = [], "", False
        for index in range(textpage.count_chars()):
            code = raw.FPDFText_GetUnicode(textpage, index)
            char = chr(code)
            if char.isspace():
                whitespace += char
                continue
            bounds = textpage.get_charbox(index)
            text_object = raw.FPDFText_GetTextObject(textpage, index)
            character_canvas = object_clips.get(_pointer(text_object), canvas)
            clipped = (
                _intersection(bounds, character_canvas) if character_canvas is not None else None
            )
            if clipped is None:
                evidence["cropped_characters"].append(
                    {"index": index, "unicode": code, "bounds": list(bounds)}
                )
                excluded_run = True
                continue
            if list(bounds) != clipped:
                reasons.append("partially_clipped_character")
                evidence["cropped_characters"].append(
                    {
                        "index": index,
                        "unicode": code,
                        "bounds": list(bounds),
                        "visible_bounds": clipped,
                        "partial": True,
                    }
                )
            retained.append(("\n" if excluded_run else whitespace) if retained else "")
            retained.append(char)
            whitespace, excluded_run = "", False
            mapping_error = raw.FPDFText_HasUnicodeMapError(textpage, index)
            generated = raw.FPDFText_IsGenerated(textpage, index)
            hyphen = raw.FPDFText_IsHyphen(textpage, index)
            if code == 2 and hyphen == 1:
                char = "-"
                retained[-1] = char
                evidence["text_normalizations"].append(
                    {
                        "index": index,
                        "unicode_before": code,
                        "after": "-",
                        "reason": "pdfium_hyphen_marker",
                    }
                )
            if (
                mapping_error != 0
                or code == 0
                or char == "\ufffd"
                or unicodedata.category(char) in {"Co", "Cs", "Cn", "Cc"}
            ):
                reasons.append("unreliable_unicode_mapping")
            if generated != 0:
                reasons.append("unproven_generated_character")
            box = _box(clipped, canvas)
            charboxes.append(box)
            evidence["characters"].append(
                {
                    "index": index,
                    "unicode": code,
                    "bbox": box,
                    "unicode_map_error": mapping_error,
                    "generated": generated,
                    "pdfium_hyphen": hyphen,
                }
            )
            text_order = object_order.get(_pointer(text_object), -1)
            if any(
                rectangle["object_index"] > text_order and _overlaps(rectangle["bbox"], box)
                for rectangle in evidence["white_rectangles"]
            ):
                reasons.append("white_rectangle_may_occlude_native_text")
        text = "".join(retained).replace("\r\n", "\n").replace("\r", "\n")
        evidence["raw_native_text_sha256"] = _hash(raw_text)
    finally:
        textpage.close()
    evidence["native_text_sha256"] = _hash(text)
    evidence["native_text"] = text
    if text.strip() and not charboxes:
        reasons.append("no_reliable_character_boxes")
    if text.strip() and not evidence["text_objects"]:
        reasons.append("no_visible_text_objects")
    if any(_overlaps(image, char) for image in images for char in charboxes):
        reasons.append("image_text_overlap")
    regions = [
        {"region_id": f"image-{number}", "bbox": box, "kind": "image"}
        for number, box in enumerate(images, 1)
    ]
    groups = _path_groups(paths, canvas)
    evidence["path_groups"] = groups
    for number, group in enumerate(groups, 1):
        box = group["bbox"]
        if any(_overlaps(box, char) for char in charboxes):
            reasons.append("path_native_text_overlap")
        if (box[2] - box[0]) * (box[3] - box[1]) > 0.6:
            reasons.append("path_region_not_localized")
        regions.append({"region_id": f"path-{number}", "bbox": box, "kind": "unknown"})
    if reasons:
        return _plan(
            "ocr",
            reasons,
            evidence=evidence,
            regions=[{"region_id": "page", "bbox": [0.0, 0.0, 1.0, 1.0], "kind": "unknown"}],
        )
    if text.strip():
        return _plan(
            "mixed" if regions else "native",
            [
                "visible_native_text_with_regions"
                if paths
                else "visible_native_text_with_image_regions"
                if images
                else "visible_native_text"
            ],
            text,
            regions,
            evidence,
        )
    if not objects:
        return _plan("blank_candidate", ["no_page_objects_or_text"], evidence=evidence)
    return _plan("ocr", ["no_usable_native_text"], regions=regions, evidence=evidence)


def probe_page(page):
    """Conservatively fall back if CPU inspection fails while rendering still works."""
    try:
        return _inspect(page)
    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        RuntimeError,
        ctypes.ArgumentError,
        pdfium.PdfiumError,
    ) as error:
        # PDFium/parser failures must not block the original-image OCR path.
        return _plan(
            "ocr",
            ["probe_failed"],
            regions=[{"region_id": "page", "bbox": [0.0, 0.0, 1.0, 1.0], "kind": "unknown"}],
            evidence={"error_type": type(error).__name__, "error": str(error)},
        )
