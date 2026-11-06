"""Source comparison and final directory decisions for the SQL-free workbench."""

from html import escape
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from hrs.processing.navigation import navigation_requires_confirmation


def navigation_queue(store):
    return [
        (doc, entry)
        for row in store.list_documents()
        for doc in [store.status(row["id"])]
        for entry in (doc.get("navigation") or {}).get("entries", [])
        if doc.get("state") not in {"cancelled", "deleting", "deleted"}
        and navigation_requires_confirmation(entry)
    ]


def hidden(fields):
    return "".join(
        f'<input type="hidden" name="{name}" value="{escape(str(value), quote=True)}">'
        for name, value in fields.items()
    )


def render_navigation(store, query, link, scope):
    queue = navigation_queue(store)
    selected = next(
        (
            (doc, entry)
            for doc, entry in queue
            if doc["id"] == query.get("document")
            and (not query.get("entry") or entry["id"] == query["entry"])
        ),
        None,
    )
    if selected is None and not query.get("document") and queue:
        selected = queue[0]
    sidebar = (
        '<nav aria-label="目录待办">'
        + "".join(
            f'<a href="{escape(link(view="navigation", document=doc["id"], entry=entry["id"]))}">{escape(doc["title"])}<small>{escape(entry["title"])} · 目录原件第 {entry["source_page"]} 页</small></a>'
            for doc, entry in queue
        )
        + "</nav>"
    )
    if selected is None:
        return (
            '<section class="panel"><h2>目录核对完成</h2><p>当前没有需要人工决定的目录对应。后续处理会继续使用已保存结果。</p></section>',
            sidebar,
        )
    doc, entry = selected
    candidates = entry.get("candidates", [])
    default = candidates[0] if candidates else {}
    number = int(query.get("target_page") or default.get("physical_page") or 0)
    anchor = query.get("target_anchor", default.get("anchor", ""))
    pages = [
        page
        for page in doc["pages"]
        if page.get("approved")
        and page.get("observation", {}).get("kind", page.get("page_kind")) != "toc"
    ]
    page = next((page for page in pages if page["page"] == number), None)
    text = page["approved"]["markdown"] if page else ""
    located = bool(anchor and text.count(anchor) == 1)
    preview = escape(text)
    if located:
        start = text.index(anchor)
        preview = (
            escape(text[:start])
            + "<mark>"
            + escape(anchor)
            + "</mark>"
            + escape(text[start + len(anchor) :])
        )
    source_image = link("/page-image", document=doc["id"], page=entry["source_page"])
    target_image = link("/page-image", document=doc["id"], page=number) if page else ""
    choices = "".join(
        f'<option value="{page["page"]}"{" selected" if page["page"] == number else ""}>PDF 第 {page["page"]} 页</option>'
        for page in pages
    )
    candidate_links = "".join(
        f'<a href="{escape(link(view="navigation", document=doc["id"], entry=entry["id"], target_page=candidate["physical_page"], target_anchor=candidate["anchor"]))}">候选：{escape(candidate["anchor"])} · PDF 第 {candidate["physical_page"]} 页</a> '
        for candidate in candidates
    )
    fields = hidden(
        {
            "document_id": doc["id"],
            "entry_id": entry["id"],
            "expected_sha256": doc["navigation"]["sha256"],
            "decision_id": uuid4().hex,
            "decision_scope": "workflow_test" if scope == "workflow_only" else scope,
            "target_page": number,
            "target_anchor": anchor,
        }
    )
    preview_fields = hidden({"view": "navigation", "document": doc["id"], "entry": entry["id"]})
    token = parse_qs(urlparse(link()).query)["token"][0]
    preview_fields += hidden({"token": token})
    return (
        f'''<section data-live-section="navigation-content"><h2>确认目录对应的正文</h2><p>先核对目录原件与正文原件。对应正确即可确认；也可以更改位置，或确定该条没有可用对应。</p>
    <section class="panel"><h3>{escape(entry["title"])}</h3><p>目录记载书内页码：{escape(entry["target_label"])}。{escape(entry["reason"])}</p>{candidate_links}</section>
    <div class="compare"><section class="panel"><h3>目录原件 · PDF 第 {entry["source_page"]} 页</h3><blockquote><mark>{escape(entry["source_anchor"])}</mark></blockquote><div class="scan"><img src="{escape(source_image)}" alt="目录原件"></div></section>
    <section class="panel"><h3>正文原件 · PDF 第 {number} 页</h3>{f'<div class="scan"><img src="{escape(target_image)}" alt="正文原件"></div><pre>{preview}</pre>' if page else "<p>暂无可靠候选，请在下方选择已核验正文。</p>"}</section></div>
    <details><summary>更改正文位置</summary><form method="get" action="/">{preview_fields}<label for="target-page">正文的 PDF 页序</label><select id="target-page" name="target_page">{choices}</select><label for="target-anchor">从下方正文复制一段唯一出现的标题或开头</label><input id="target-anchor" name="target_anchor" value="{escape(anchor, quote=True)}"><button class="secondary">查看所选正文与原图</button></form><p>可选正文均已通过原图核对。选择页序后先查看原图，再复制该页文字作为跳转位置。</p></details>
    <form method="post" action="{escape(link("/navigation-decision"))}" data-approval-ready="{str(scope != "unknown").lower()}">{fields}<button name="verdict" value="link" data-final-decision data-target-ready="{str(located).lower()}"{" disabled" if scope == "unknown" or not located else ""}>就是这篇，确认跳转</button><button name="verdict" value="unlink" class="secondary" data-final-decision{" disabled" if scope == "unknown" else ""}>该条无可用对应，保留目录文字</button><small>{"授权放行仅验证导航流程，不代表内容准确率通过。" if scope == "workflow_only" else "只决定这条目录的跳转位置，正文与原件保留。"}</small></form></section>''',
        sidebar,
    )
