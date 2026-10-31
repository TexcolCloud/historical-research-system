"""A loopback-only human review form; no model or MCP can grant human approval."""

import html
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from hrs_codex.details import original_detail
from hrs_codex.store import Store


def escape(value) -> str:
    return html.escape(str(value), quote=True)


STYLE = """
:root{font-family:'Segoe UI','Microsoft YaHei',sans-serif;color:#243747;background:#eef2f5}
*{box-sizing:border-box}body{margin:0}a{color:#175f82}header{padding:22px 30px;background:#183e56;color:white}
header h1{font-family:'SimSun',serif;font-size:24px;margin:0 0 6px}header p{margin:0;color:#d1e2ec}
.layout{display:grid;grid-template-columns:250px minmax(0,1fr);min-height:85vh}nav{padding:24px 18px;border-right:1px solid #cbd6df}
nav a{display:block;text-decoration:none;padding:12px;margin:0 0 12px;background:white;border-left:4px solid #8ba9ba;border-radius:4px}
nav a[aria-current]{border-color:#176b8e;background:#e1f0f6}small{display:block;color:#586e7d;margin-top:6px}
main{padding:24px;min-width:0}h2{font-size:20px;margin:0 0 8px}.notice{padding:14px 18px;background:#fff6db;border-left:4px solid #aa7b1d;margin-bottom:20px;white-space:pre-wrap}
.compare{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px}.panel{background:white;border:1px solid #cbd6df;border-radius:6px;padding:18px;min-width:0}
.toolbar{display:flex;gap:18px;flex-wrap:wrap;margin-bottom:14px}.scan{max-height:78vh;overflow:auto;background:#e4e9ed;text-align:center}.scan img{width:100%;height:auto;display:block}
label{display:block;font-weight:600;margin:14px 0 7px}textarea,input{font:inherit;width:100%;padding:10px;border:1px solid #91a6b4;border-radius:3px}
textarea[name=markdown]{min-height:58vh;font-family:'SimSun',serif;font-size:16px;line-height:1.8}textarea[name=note]{min-height:80px}
.check{font-weight:400;display:flex;gap:9px;align-items:flex-start}.check input{width:auto;margin-top:4px}
button{font:inherit;padding:11px 16px;border:1px solid #176b8e;border-radius:4px;background:#176b8e;color:white;cursor:pointer;margin:12px 10px 0 0}
button.secondary{background:white;color:#274859;border-color:#809aaa}button:focus-visible,a:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid #df9b26;outline-offset:3px}
details{margin-top:18px}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 Consolas,monospace;background:#f0f4f7;padding:14px}
.evidence{font:12px/1.6 Consolas,monospace;overflow-wrap:anywhere;color:#586e7d;margin-top:18px}.empty{padding:35px;background:white;border:1px solid #cbd6df}
@media(max-width:1050px){.layout{grid-template-columns:1fr}nav{border-right:0;border-bottom:1px solid #cbd6df}.compare{grid-template-columns:1fr}main{padding:16px}}
"""


def create_review_server(store: Store, port: int = 8766) -> ThreadingHTTPServer:
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not write session tokens or document text into access logs.

        def respond(self, code, data, content_type="text/html; charset=utf-8"):
            if isinstance(data, str):
                data = data.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

        def query(self):
            if self.headers.get("Host") != self.server.authority:
                raise ValueError("Use the printed loopback review URL")
            query = parse_qs(urlparse(self.path).query)
            if not secrets.compare_digest(query.get("token", [""])[0], token):
                raise ValueError("Open the review URL printed by the local CLI")
            return {key: value[0] for key, value in query.items()}

        def link(self, path="/", **query):
            return path + "?" + urlencode({"token": token, **query})

        def do_GET(self):
            try:
                query = self.query()
                path = urlparse(self.path).path
                if path == "/page-image":
                    document_id, number = query["document"], int(query["page"])
                    store.human_packet(document_id, number)
                    if query.get("detail") == "1":
                        _, data = original_detail(store, document_id, number, [0, 0, 1, 1])
                    else:
                        doc = store.status(document_id)
                        data = store._path(doc, doc["pages"][number - 1]["image"]).read_bytes()
                    self.respond(200, data, "image/png")
                elif path == "/":
                    self.respond(200, self.render(query))
                else:
                    self.respond(404, "Not found")
            except (ValueError, KeyError, OSError, StopIteration) as error:
                self.respond(409, escape(error))

        def render(self, query):
            queue = store.human_queue()
            selected = next(
                (
                    row
                    for row in queue
                    if row["document_id"] == query.get("document")
                    and str(row["page"]) == query.get("page")
                ),
                queue[0] if queue else None,
            )
            links = []
            for row in queue:
                url = self.link(document=row["document_id"], page=row["page"])
                current = ' aria-current="page"' if row == selected else ""
                links.append(
                    f'<a href="{escape(url)}"{current}>{escape(row["title"])}<small>物理第 {row["page"]} 页 · 未解决记录 {row["unresolved_attempts"]} 次</small></a>'
                )
            notice = (
                f'<div class="notice" role="status">{escape(query["message"])}</div>'
                if query.get("message")
                else ""
            )
            content = '<div class="empty">当前没有待人工审核的页面。机器后续发现未解决问题时，会出现在这里。</div>'
            if selected:
                packet = store.human_packet(selected["document_id"], selected["page"])
                args = {"document": packet["document_id"], "page": packet["page"]}
                image = self.link("/page-image", **args)
                detail = self.link("/page-image", **args, detail=1)
                fields = {
                    "document_id": packet["document_id"],
                    "page": packet["page"],
                    "input_sha256": packet["input_sha256"],
                }
                hidden = "".join(
                    f'<input type="hidden" name="{key}" value="{escape(value)}">'
                    for key, value in fields.items()
                )
                issues = "\n".join(str(issue) for issue in selected["issues"])
                history = escape(json.dumps(packet["history"], ensure_ascii=False, indent=2))
                content = f'''<h2>{escape(packet["title"])} · 物理第 {packet["page"]} 页</h2>
                <div class="notice">待解决：{escape(issues)}</div><div class="compare">
                <section class="panel"><h2>原始证据</h2><div class="toolbar"><a href="{escape(image)}" target="_blank" rel="noopener">打开原图</a><a href="{escape(detail)}" target="_blank" rel="noopener">放大查看原图</a></div>
                <div class="scan"><img src="{escape(image)}" alt="物理第 {packet["page"]} 页原图"></div>
                <div class="evidence">原件 SHA-256<br>{packet["source_sha256"]}<br>页图 SHA-256<br>{packet["image_sha256"]}</div></section>
                <section class="panel"><h2>人工核对</h2><p>先查看原图。能明确判读再确认整页；仍不清楚可保存草稿，继续待审。</p>
                <form method="post" action="{escape(self.link("/decision"))}">{hidden}
                <label for="markdown">整页正文（可修订）</label><textarea id="markdown" name="markdown">{escape(packet["markdown"])}</textarea>
                <label for="reviewer">审核人</label><input id="reviewer" name="reviewer" required autocomplete="name">
                <label for="note">裁决依据 / 仍待解决的问题</label><textarea id="note" name="note" required></textarea>
                <label class="check"><input type="checkbox" name="confirmed" value="yes">确认通过时：我已对照原图核对整页，所有内容问题均已解决。</label>
                <button name="verdict" value="approved">确认整页通过</button><button class="secondary" name="verdict" value="pending">保存草稿，继续待审</button>
                </form><details><summary>查看原始 OCR</summary><pre>{escape(packet["ocr_markdown"])}</pre></details>
                <details><summary>查看机器尝试和人工历史</summary><pre>{history}</pre></details></section></div>'''
            return f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>史料人工审核</title><style>{STYLE}</style><header><h1>史料人工审核</h1><p>原图对照 · 保留机器尝试 · 人工决定单独留痕</p></header><div class="layout"><nav aria-label="待审页面"><h2>待审队列 · {len(queue)}</h2>{"".join(links)}</nav><main>{notice}{content}</main></div></html>'

        def do_POST(self):
            try:
                self.query()
                if (
                    urlparse(self.path).path != "/decision"
                    or self.headers.get("Origin") != "http://" + self.server.authority
                ):
                    raise ValueError("A same-origin local human form submission is required")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2_000_000:
                    raise ValueError("Invalid review form size")
                form = {
                    k: v[0]
                    for k, v in parse_qs(
                        self.rfile.read(length).decode("utf-8"), keep_blank_values=True
                    ).items()
                }
                if form.get("verdict") == "approved" and form.get("confirmed") != "yes":
                    raise ValueError(
                        "Confirm that the entire page has been checked before approving"
                    )
                result = store.record_human_review(
                    form["document_id"],
                    int(form["page"]),
                    input_sha256=form["input_sha256"],
                    reviewer=form["reviewer"],
                    markdown=form["markdown"],
                    note=form["note"],
                    verdict=form["verdict"],
                )
                message = "草稿已保存，页面继续待审。"
                if result["page_state"] == "approved":
                    message = "人工决定已保存。其他页面仍待审。"
                    if result["state"] == "processing":
                        message = f'全文页面已通过，已恢复文章组织任务。请让 Codex 主代理继续此文档：{form["document_id"]}；或运行 hrs-codex --root "{store.root}" run {form["document_id"]}'
                self.send_response(303)
                self.send_header("Location", self.link(message=message))
                self.end_headers()
            except (ValueError, KeyError, OSError, StopIteration) as error:
                self.respond(409, escape(error))

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.authority = f"127.0.0.1:{server.server_port}"
    server.review_url = f"http://{server.authority}/?token={token}"
    return server


def serve(store: Store, port: int) -> None:
    with create_review_server(store, port) as server:
        print(f"人工审核：{server.review_url}", flush=True)
        server.serve_forever()
