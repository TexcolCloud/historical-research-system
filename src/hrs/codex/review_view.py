"""Standalone projection of the same source-bound issue contract used by React."""

import html
import json

from hrs.processing.review_locations import locate_issues


def review_locations(packet):
    pending = next(
        row
        for row in reversed(packet["history"])
        if (row.get("result") or {}).get("verdict") == "pending"
    )
    return locate_issues(
        packet["markdown"],
        pending["result"]["issues"],
        page=packet["page"],
        image_sha256=packet["image_sha256"],
    )


def resolutions_from_form(form, locations, *, required):
    expected = {location["id"] for location in locations}
    supplied = {
        key.removeprefix("resolution."): value
        for key, value in form.items()
        if key.startswith("resolution.")
    }
    if set(supplied) - expected or any(
        value not in {"keep", "edit"} for value in supplied.values()
    ):
        raise ValueError("疑点已经变化，请刷新并逐项核对；草稿文字仍保留。")
    if required and set(supplied) != expected:
        raise ValueError("请对每处疑点作出保留或修改的最终判断，再提交本页。")
    return [
        {"id": location["id"], "action": supplied[location["id"]]}
        for location in locations
        if location["id"] in supplied
    ]


def marked_text(text, locations):
    ranges = sorted(
        (span["start"], span["end"], location["id"])
        for location in locations
        for span in location.get("text_spans", [location])
        if span.get("text_status") == "located"
        and type(span.get("start")) is int
        and type(span.get("end")) is int
        and 0 <= span["start"] < span["end"] <= len(text)
    )
    output, position = [], 0
    for start, end, issue in ranges:
        if start < position:
            continue
        output.extend(
            (
                html.escape(text[position:start]),
                f'<mark data-review-id="{issue}">{html.escape(text[start:end])}</mark>',
            )
        )
        position = end
    output.append(html.escape(text[position:]))
    return "".join(output)


def render_doubts(packet, locations, image, size):
    saved = {
        row["id"]: row["action"]
        for row in (packet["history"][-1].get("result") or {}).get("resolutions", [])
    }
    parts = [f'<p data-review-completion role="status">逐项核对，共 {len(locations)} 处疑点。</p>']
    for index, location in enumerate(locations):
        issue = location["id"]
        crop = ""
        boxes = location.get("boxes", [])
        if boxes:
            width, height = size
            left = max(0, min(box[0] for box in boxes) - 0.03) * width
            top = max(0, min(box[1] for box in boxes) - 0.02) * height
            right = min(1, max(box[2] for box in boxes) + 0.03) * width
            bottom = min(1, max(box[3] for box in boxes) + 0.02) * height
            rectangles = "".join(
                f'<rect x="{x * width}" y="{y * height}" width="{(r - x) * width}" height="{(b - y) * height}"/>'
                for x, y, r, b in boxes
            )
            crop = f'<svg class="doubt-crop" role="img" aria-label="疑点原图区域" viewBox="{left} {top} {right - left} {bottom - top}"><image href="{html.escape(image, quote=True)}" width="{width}" height="{height}"/>{rectangles}</svg><small>原图定位区域，请结合整页上下文核对。</small>'
        anchor = html.escape(json.dumps(location["anchor"], ensure_ascii=False), quote=True)
        focus = html.escape(location["focus"], quote=True)
        options = "".join(
            f'<label class="check"><input type="radio" name="resolution.{issue}" value="{action}" required'
            f"{' checked' if saved.get(issue) == action else ''}>{label}</label>"
            for action, label in (("keep", "识别正确，保留"), ("edit", "已在正文中修改"))
        )
        locate = (
            f'<button type="button" class="secondary" data-locate-anchor="{anchor}" data-locate-focus="{focus}">在正文中定位并修改</button>'
            if location["text_status"] == "located"
            else "<p>当前草稿中没有可靠的文字定位，请结合整页原图核对。</p>"
        )
        parts.append(f'''<details class="review-doubt" data-review-doubt="{issue}"{" open" if index == 0 else ""}>
        <summary>疑点 {index + 1} / {len(locations)} · {html.escape(location["message"] or "请对照原件核对")}</summary>
        <p>当前待核文字：<mark>{html.escape(location["focus"] or location["candidate"] or "需结合整页")}</mark></p>
        <p>机器参考读法：{html.escape(location["source_reading"] or "未提供可靠读法，由你作出最终判断")}</p>
        {crop}{locate}<fieldset><legend>本处最终判断</legend>{options}</fieldset></details>''')
    parts.append(
        f'<details open><summary>疑点在当前底稿中的位置</summary><pre class="review-highlighted">{marked_text(packet["markdown"], locations)}</pre></details>'
    )
    return "".join(parts)
