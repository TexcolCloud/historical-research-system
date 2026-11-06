"""Local task workbench and genuine human review; model decisions stay separate."""

import html
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from hrs.codex.navigation_view import navigation_queue, render_navigation
from hrs.codex.review_view import render_doubts, resolutions_from_form, review_locations
from hrs.processing.acceptance import workspace_scope
from hrs.processing.details import original_detail
from hrs.processing.store import Store


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
.primary{display:flex;gap:8px;padding:12px 30px;background:#fff;border:0;border-bottom:1px solid #cbd6df}
.primary a{margin:0;padding:10px 22px;background:transparent;border:0;border-bottom:3px solid transparent;border-radius:0}
.primary a[aria-current]{border-bottom-color:#176b8e;background:#e1f0f6}
.workspace{padding:12px 30px;background:#e4ebf0;font-size:12px;color:#425a6a;overflow-wrap:anywhere}
.workspace code{margin-left:10px}.workspace-body{max-width:1500px;margin:auto}.workspace-body main{padding:30px}
.section-heading{display:flex;align-items:center;justify-content:space-between;gap:20px;margin:0 0 24px}
.section-heading h2{margin-top:8px}.section-heading p{color:#586e7d;margin-bottom:0}.panel{margin-bottom:24px}
.import{border-top:4px solid #176b8e}.import form{max-width:900px}.table-scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;text-align:left;font-size:14px}th{font-weight:600;color:#425a6a;background:#f0f4f7}
td,th{padding:16px 14px;border-bottom:1px solid #dce4eb;vertical-align:top}td:first-child{min-width:180px}
td small{font-size:12px}td progress{width:130px;accent-color:#176b8e}.inline{display:inline-block}
td>a{display:inline-block;margin:4px 12px 4px 0}
.inline button{padding:7px 10px;font-size:13px}.search-hit{padding:18px 0;border-bottom:1px solid #dce4eb}
li{margin:12px 0}code{overflow-wrap:anywhere}p{line-height:1.65}button:hover{filter:brightness(.94)}
.sync{padding:8px 30px;color:#425a6a;background:#f5f8fa;font-size:13px}button:disabled{opacity:.55;cursor:wait}
progress,td small{font-variant-numeric:tabular-nums}h1,h2{text-wrap:balance}.form-error{font-weight:400}
mark{background:#fff09a;color:inherit}.review-doubt{border:1px solid #cbd6df;padding:12px;border-radius:5px;margin:12px 0}.review-doubt fieldset{border:0;padding:0}.review-doubt .check input{flex:none}.doubt-crop{width:100%;max-height:240px;background:#fff}.doubt-crop rect{fill:#f5bc1733;stroke:#965e00;stroke-width:2;vector-effect:non-scaling-stroke}.source-evidence{align-self:start;position:sticky;top:12px}.review-highlighted{max-height:38vh;overflow:auto}
@media(max-width:1050px){.layout{grid-template-columns:1fr}nav{border-right:0;border-bottom:1px solid #cbd6df}.compare{grid-template-columns:1fr}main{padding:16px}}
@media(max-width:600px){header{padding:20px}.primary{padding:10px}.primary a{padding:10px 12px}.workspace{padding:10px 16px}.workspace-body main{padding:18px}.section-heading{align-items:flex-start}td,th{padding:12px 10px}}
"""


def create_review_server(store: Store, port: int = 8766) -> ThreadingHTTPServer:
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def scope(self):
            try:
                return workspace_scope(store.root)
            except ValueError:
                return "unknown"

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
                "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'",
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

        def redirect(self, **query):
            self.send_response(303)
            self.send_header("Location", self.link(**query))
            self.end_headers()

        def shell(self, view, content, sidebar=""):
            pending_count = len(store.human_queue()) + len(navigation_queue(store))
            links = "".join(
                f'<a href="{escape(self.link(view=key))}"'
                f"{' aria-current=page' if view == key else ''}>{label}</a>"
                for key, label in (
                    ("tasks", "处理任务"),
                    ("review", f"人工待审 · {pending_count}"),
                    ("library", "已入库"),
                )
            )
            from hrs.processing.workspace import identity

            workspace = identity(store)
            location = f"{workspace['root']} · {workspace['workspace_id']}"
            scope = self.scope()
            scope_notice = (
                '<div class="notice" role="note">流程验收工作区：授权放行只验证顺序与格式，不代表内容准确率通过。</div>'
                if scope == "workflow_only"
                else '<div class="notice" role="alert">工作区范围未确认，可阅读和保存草稿；修复授权配置并刷新后才能提交终审。</div>'
                if scope == "unknown"
                else ""
            )
            return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1"><title>史料研究工作台</title>
            <style>{STYLE}</style><script defer src="{escape(self.link("/assets/workbench.js"))}"></script></head><body data-workspace-id="{workspace["workspace_id"]}"><header><h1>史料研究工作台</h1>
            <p>从原件到可检索正文，每一步都有记录。</p></header>
            <nav class="primary" aria-label="工作台导航" data-live-section="navigation">{links}</nav>
            <div class="workspace">当前工作区 <code>{escape(location)}</code></div>
            {scope_notice}
            <div class="sync" data-sync-status role="status" aria-live="polite">正在连接进度更新…</div>
            <div class="{"layout" if sidebar else "workspace-body"}">{sidebar}<main>{content}</main></div></body></html>'''

        def run_form(self, doc):
            if doc["state"] == "published":
                return ""
            if (doc.get("run_request") or {}).get("state") == "waiting_human":
                return f'<a href="{escape(self.link(view="review", document=doc["id"]))}">打开人工待审</a>'
            return f'''<form method="post" action="{escape(self.link("/run"))}" class="inline">
            <input type="hidden" name="document_id" value="{escape(doc["id"])}">
            <button class="secondary">继续 / 重试运行</button></form>'''

        @staticmethod
        def state_label(state):
            return {
                "queued": "已排队",
                "running": "执行中",
                "succeeded": "本轮执行完成",
                "waiting_human": "等待人工核对",
                "retryable_error": "运行遇到故障，可重试",
                "processing": "处理中",
                "pending": "待核对",
                "published": "已入库",
                "ready": "待处理",
                "leased": "执行中",
                "completed": "已完成",
                "approved": "已通过",
                "superseded": "已保留历史",
                "failed": "运行失败",
                "awaiting_ocr": "等待识别",
                "awaiting_review": "等待原图审核",
                "awaiting_verification": "等待独立复核",
            }.get(state, state or "尚未排队")

        def document_link(self, doc):
            return escape(self.link(view="document", document=doc["id"]))

        def run_label(self, run):
            return "已排队" if run.get("state") == "pending" else self.state_label(run.get("state"))

        def render(self, query):
            view = query.get(
                "view", "review" if query.get("document") and query.get("page") else "tasks"
            )
            if view == "review":
                return self.render_review(query)
            if view == "navigation":
                content, sidebar = render_navigation(store, query, self.link, self.scope())
                return self.shell("review", content, sidebar)
            notice = (
                f'<div class="notice" role="status">{escape(query["message"])}</div>'
                if query.get("message")
                else ""
            )
            if view == "document":
                return self.shell(
                    view, notice + self.render_document(store.status(query["document"]))
                )
            if view == "library":
                return self.shell(view, notice + self.render_library(query))
            if view != "tasks":
                raise ValueError("Unknown workbench view")
            documents = [store.status(row["id"]) for row in store.list_documents()]
            rows = "".join(self.document_row(doc) for doc in reversed(documents))
            content = f'''<div class="section-heading"><div><h2>处理任务</h2>
            <p>导入本地原件，查看处理进展；内容不确定时转入人工待审。</p></div>
            <a href="{escape(self.link(view="tasks"))}">刷新状态</a></div>
            <section class="panel import"><h2>导入原件</h2>
            <form method="post" action="{escape(self.link("/import"))}">
            <label for="source">本地 PDF / 图片完整路径</label><input id="source" name="source" required placeholder="D:\\史料\\原件.pdf">
            <label for="title">文档名称（可选）</label><input id="title" name="title">
            <button>导入并开始处理</button><small>支持 PDF、PNG、JPEG、TIFF。原件保留在当前工作区。</small></form></section>
            <section class="panel" data-live-section="task-list"><h2>任务列表 · {len(documents)}</h2>
            {f'<div class="table-scroll"><table><thead><tr><th>文档</th><th>页面进度</th><th>内容状态</th><th>运行状态</th><th>操作</th></tr></thead><tbody>{rows}</tbody></table></div>' if rows else '<p class="empty">还没有文档。在上方输入原件路径即可开始。</p>'}</section>'''
            return self.shell("tasks", notice + content)

        def document_row(self, doc):
            approved = sum(page["state"] == "approved" for page in doc["pages"])
            total = len(doc["pages"])
            run = doc.get("run_request") or {}
            return f'''<tr><td><a href="{self.document_link(doc)}">{escape(doc["title"])}</a><small>{escape(doc["id"])}</small></td>
            <td><progress value="{approved}" max="{total}"></progress><small>已通过 {approved} / {total} 页</small></td>
            <td>{escape(self.state_label(doc["state"]))}<small>{escape(doc["state"])}</small></td>
            <td>{escape(self.run_label(run))}<small>{escape(run.get("state", ""))}</small></td>
            <td><a href="{self.document_link(doc)}">查看详情</a>{self.run_form(doc)}</td></tr>'''

        def render_document(self, doc):
            run = doc.get("run_request") or {}
            routes = {
                "native": "原生文本",
                "mixed": "混合内容",
                "ocr": "扫描识别",
                "blank_candidate": "待确认空白",
            }
            queue = {(row["document_id"], row["page"]) for row in store.human_queue()}
            rows = []
            for page in doc["pages"]:
                action = f'<a target="_blank" rel="noopener" href="{escape(self.link("/page-image", document=doc["id"], page=page["page"]))}">查看原图</a> '
                if (doc["id"], page["page"]) in queue:
                    action += f'<a href="{escape(self.link(view="review", document=doc["id"], page=page["page"]))}">对照原图核对</a>'
                text = (page.get("approved") or page.get("candidate") or page.get("ocr") or {}).get(
                    "markdown", ""
                )
                preview = (
                    f"<details><summary>查看已保留正文</summary><pre>{escape(text)}</pre></details>"
                    if text
                    else ""
                )
                rows.append(f"""<tr><td>第 {page["page"]} 页</td><td>{escape(routes.get(page.get("route"), "扫描识别"))}</td>
                <td>{escape(self.state_label(page["state"]))}<small>{escape(page["state"])}</small></td><td>{action}{preview}</td></tr>""")
            error = run.get("error") or run.get("last_error")
            failure = (
                f'<div class="notice" role="alert">运行故障：{escape(error)}<p>已保存的结果保留。排除故障后点击继续 / 重试运行。</p></div>'
                if error
                else ""
            )
            task_rows = "".join(
                f"<tr><td>{escape(task['stage'])}</td><td>{escape(task.get('page') or '全文')}</td><td>{escape(self.state_label(task['state']))}<small>{escape(task['state'])}</small></td></tr>"
                for task in doc["tasks"]
            )
            articles = (doc.get("organization") or {}).get("articles", [])
            article_list = "".join(
                f"<li>{escape(article['title'])} · 原件页 {escape(article['pages'])}</li>"
                for article in articles
            )
            from hrs.processing.navigation import navigation_review_summary

            navigation_pending = navigation_review_summary(doc.get("navigation") or {})[
                "pending_count"
            ]
            navigation_link = (
                f'<section class="panel"><h2>原书目录</h2><a href="{escape(self.link(view="navigation", document=doc["id"]))}">确认目录对应 · {navigation_pending} 项待办</a></section>'
                if navigation_pending
                else ""
            )
            revision_note = ""
            if doc.get("active_publication") and doc["state"] != "published":
                reason = doc["revision_history"][-1]["reason"]
                revision_note = f'<div class="notice">当前为第 {doc.get("revision", 1)} 版草稿；检索暂用历史发布的第 {doc["active_publication"]["revision"]} 版。复核原因：{escape(reason.split("。", 1)[0])}<details><summary>查看复核依据</summary><pre>{escape(reason)}</pre></details></div>'
            revision_form = ""
            if doc["state"] == "published" and any(
                p["approved"].get("approval_type") != "human" for p in doc["pages"]
            ):
                revision_form = f'<details><summary>重新核对已发布内容</summary><form method="post" action="{escape(self.link("/revision"))}"><input type="hidden" name="document_id" value="{doc["id"]}"><label for="reason">重新核对原因</label><input id="reason" name="reason" required><button class="secondary">创建复核版本并开始</button><small>保留当前发布版本和人工决定；新版本全部通过后才替换检索版本。</small></form></details>'
            return f'''<div data-live-section="document"><a href="{escape(self.link(view="tasks"))}">← 全部任务</a><div class="section-heading"><div>
            <h2>{escape(doc["title"])}</h2><p>{escape(self.state_label(doc["state"]))} · {len(doc["pages"])} 页</p></div>
            <a href="{self.document_link(doc)}">刷新状态</a></div>
            {revision_note}{failure}<section class="panel"><h2>运行状态</h2><p>{escape(self.run_label(run))} <code>{escape(run.get("state", ""))}</code></p>
            <p>排队请求由本地运行器接续；未启动运行器时会保持排队。</p>{self.run_form(doc)}</section>
            <section class="panel"><h2>逐页进度</h2><div class="table-scroll"><table><thead><tr><th>物理页</th><th>处理方式</th><th>页面状态</th><th>正文与核对</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>
            {navigation_link}<section class="panel"><h2>文章目录</h2>{f"<ul>{article_list}</ul>" if articles else "<p>全文通过后自动整理文章目录。</p>"}</section>
            <details><summary>处理任务与历史</summary><table><thead><tr><th>步骤</th><th>范围</th><th>状态</th></tr></thead><tbody>{task_rows}</tbody></table>
            <pre>{escape(json.dumps(run, ensure_ascii=False, indent=2))}</pre></details></div>
            <div data-live-section="revision-controls">{revision_form}</div>'''

        def render_library(self, query):
            term = query.get("q", "").strip()
            results = store.search(term) if term else []
            hits = "".join(
                f'<article class="search-hit"><a href="{escape(self.link(view="document", document=row.get("document_id", row.get("id"))))}">{escape(row.get("title", row.get("document_id", "文档")))}</a>'
                f"<small>物理第 {escape(row.get('page', ''))} 页</small>"
                + (
                    f'<p class="notice">新版本复核中，以下为历史发布文本。{escape((row.get("revision_reason") or "").split("。", 1)[0])}</p>'
                    if row.get("revision_pending")
                    else ""
                )
                + f"<pre>{escape(row.get('excerpt', row.get('snippet', row.get('text', ''))))}</pre></article>"
                for row in results
            )
            documents = [store.status(doc["id"]) for doc in store.list_documents()]
            documents = [
                doc
                for doc in documents
                if doc["state"] == "published" or doc.get("active_publication")
            ]
            entries = "".join(
                f'<li><a href="{self.document_link(doc)}">{escape(doc["title"])}</a>{" · 新版本复核中" if doc["state"] != "published" else ""}</li>'
                for doc in documents
            )
            return f'''<h2>已入库</h2><p>仅检索全部通过并已发布的正文。</p><section class="panel">
            <form method="get" action="/"><input type="hidden" name="token" value="{escape(token)}"><input type="hidden" name="view" value="library">
            <label for="q">检索已入库正文</label><input id="q" name="q" value="{escape(term)}" required><button>检索</button></form></section>
            <div data-live-section="library-results">{f'<section class="panel"><h2>检索结果 · {len(results)}</h2>{hits or "<p>没有匹配的已入库内容。</p>"}</section>' if term else ""}
            <section class="panel"><h2>已入库文档 · {len(documents)}</h2>{f"<ul>{entries}</ul>" if entries else "<p>还没有已入库文档。处理任务全部通过后会出现在这里。</p>"}</section></div>'''

        def do_GET(self):
            try:
                query = self.query()
                path = urlparse(self.path).path
                if path in {"/health", "/changes"}:
                    from hrs.processing.workspace import change_token, identity

                    state = {"workspace_id": identity(store)["workspace_id"]}
                    if path == "/changes":
                        state["revision"] = f"{change_token(store)}:{self.scope()}"
                    else:
                        executor = getattr(self.server, "executor", None)
                        state["executor_running"] = bool(
                            executor
                            and executor.thread
                            and executor.thread.is_alive()
                            and not executor.stop_event.is_set()
                        )
                    self.respond(200, json.dumps(state), "application/json; charset=utf-8")
                    return
                if path == "/assets/workbench.js":
                    from hrs.codex.workbench_assets import SCRIPT

                    self.respond(200, SCRIPT, "text/javascript; charset=utf-8")
                    return
                if path == "/page-image":
                    document_id, number = query["document"], int(query["page"])
                    if query.get("detail") == "1":
                        _, data = original_detail(store, document_id, number, [0, 0, 1, 1])
                    else:
                        _, data = store.original_page(document_id, number)
                    self.respond(200, data, "image/png")
                elif path == "/":
                    self.respond(200, self.render(query))
                else:
                    self.respond(404, "Not found")
            except (ValueError, KeyError, OSError, StopIteration) as error:
                self.respond(409, escape(error))

        def render_review(self, query):
            queue = store.human_queue()
            navigation = navigation_queue(store)
            if not queue and navigation:
                content, sidebar = render_navigation(
                    store,
                    {"document": query.get("document", "")} if query.get("document") else {},
                    self.link,
                    self.scope(),
                )
                return self.shell("review", content, sidebar)
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
            if navigation:
                links.append(
                    f'<a href="{escape(self.link(view="navigation"))}">目录对应待办 · {len(navigation)} 项</a>'
                )
            for row in queue:
                url = self.link(view="review", document=row["document_id"], page=row["page"])
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
                    "decision_scope": "workflow_test"
                    if self.scope() == "workflow_only"
                    else self.scope(),
                }
                hidden = "".join(
                    f'<input type="hidden" name="{key}" value="{escape(value)}">'
                    for key, value in fields.items()
                )
                locations = review_locations(packet)
                from PIL import Image

                with Image.open(
                    store.artifact_path(
                        store.status(packet["document_id"]),
                        store.status(packet["document_id"])["pages"][packet["page"] - 1]["image"],
                    )
                ) as original:
                    size = original.size
                doubts = render_doubts(packet, locations, image, size)
                scope = self.scope()
                final_label = (
                    "按授权提交流程测试结果"
                    if scope == "workflow_only"
                    else "确认工作区范围后再提交"
                    if scope == "unknown"
                    else "提交本页并确认通过"
                )
                history = escape(json.dumps(packet["history"], ensure_ascii=False, indent=2))
                content = f'''<h2>{escape(packet["title"])} · 物理第 {packet["page"]} 页</h2>
                <div class="compare">
                <section class="panel source-evidence"><h2>原始证据</h2><div class="toolbar"><a href="{escape(image)}" target="_blank" rel="noopener">打开原图</a><a href="{escape(detail)}" target="_blank" rel="noopener">放大查看原图</a></div>
                <div class="scan"><img src="{escape(image)}" alt="物理第 {packet["page"]} 页原图"></div>
                <div class="evidence">原件 SHA-256<br>{packet["source_sha256"]}<br>页图 SHA-256<br>{packet["image_sha256"]}</div></section>
                <section class="panel"><h2>人工核对</h2><p>对照原图，保留正确文字或修改为最终文字，再提交本页。保存进度仅暂存草稿，不提交审核结果。</p>
                <form method="post" action="{escape(self.link("/decision"))}" data-approval-ready="{str(scope != "unknown").lower()}">{hidden}{doubts}
                <label for="markdown">整页正文（可修订）</label><textarea id="markdown" name="markdown">{escape(packet["markdown"])}</textarea>
                <button name="verdict" value="approved"{" disabled" if scope == "unknown" else ""}>{final_label}</button><button class="secondary" name="verdict" value="pending" formnovalidate>保存进度，稍后继续</button>
                </form><details><summary>查看原始 OCR</summary><pre>{escape(packet["ocr_markdown"])}</pre></details>
                <details><summary>查看机器尝试和人工历史</summary><pre>{history}</pre></details></section></div>'''
            sidebar = f'<nav aria-label="待审页面" data-live-section="review-queue"><h2>待审队列 · {len(queue)}</h2>{"".join(links)}</nav>'
            return self.shell(
                "review",
                f'<div data-live-section="review-content">{notice}{content}</div>',
                sidebar,
            )

        def do_POST(self):
            try:
                self.query()
                same_origin = self.headers.get("Origin") == "http://" + self.server.authority
                # Embedded browsers may use an opaque Origin while preserving
                # browser-controlled Fetch Metadata for same-origin navigation.
                embedded_navigation = (
                    self.headers.get("Origin") == "null"
                    and self.headers.get("Sec-Fetch-Site") == "same-origin"
                    and (
                        self.headers.get("Sec-Fetch-Mode") == "navigate"
                        or (
                            self.headers.get("Sec-Fetch-Mode") == "same-origin"
                            and self.headers.get("X-HRS-Form") == "1"
                        )
                    )
                )
                if urlparse(self.path).path not in {
                    "/decision",
                    "/navigation-decision",
                    "/import",
                    "/run",
                    "/revision",
                } or not (same_origin or embedded_navigation):
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
                action = urlparse(self.path).path
                if action == "/navigation-decision":
                    if form.get("verdict") not in {"link", "unlink"}:
                        raise ValueError("请选择确认跳转，或确定该条无可用对应。")
                    store.navigation_decision(
                        form["document_id"],
                        form["entry_id"],
                        expected_sha256=form["expected_sha256"],
                        target_page=int(form["target_page"]) if form["verdict"] == "link" else None,
                        target_anchor=form.get("target_anchor", ""),
                        decision_id=form["decision_id"],
                        decision_scope=form.get("decision_scope"),
                    )
                    store.request_run(form["document_id"])
                    self.redirect(
                        view="navigation", document=form["document_id"], message="目录对应已保存。"
                    )
                    return
                if action == "/revision":
                    store.begin_revision(form["document_id"], form["reason"])
                    store.request_run(form["document_id"])
                    self.redirect(
                        view="document",
                        document=form["document_id"],
                        message="复核版本已创建并排队，原发布版本保留。",
                    )
                    return
                if action == "/import":
                    from hrs.processing.application import prepare

                    doc = prepare(store, form["source"].strip(), form.get("title", "").strip())
                    store.request_run(doc["id"], resume=False)
                    self.redirect(view="document", document=doc["id"], message="原件已导入并排队。")
                    return
                if action == "/run":
                    store.request_run(form["document_id"], resume=True)
                    self.redirect(
                        view="document",
                        document=form["document_id"],
                        message="已提交继续处理请求，请查看运行状态。",
                    )
                    return
                packet = store.human_packet(form["document_id"], int(form["page"]))
                resolutions = resolutions_from_form(
                    form, review_locations(packet), required=form["verdict"] == "approved"
                )
                result = store.record_human_review(
                    form["document_id"],
                    int(form["page"]),
                    input_sha256=form["input_sha256"],
                    reviewer=form.get("reviewer") or "本地使用者",
                    markdown=form["markdown"],
                    note=form.get("note")
                    or ("确认通过" if form["verdict"] == "approved" else "保存待定"),
                    verdict=form["verdict"],
                    decision_scope=form.get("decision_scope"),
                    resolutions=resolutions,
                )
                message = "草稿已保存，尚未提交本页审核结果。"
                if result["page_state"] == "approved":
                    message = "人工决定已保存。其他页面仍待审。"
                    if result["state"] == "processing":
                        message = "全文页面已通过，后续文章整理已自动排队。"
                context = (
                    {"document": form["document_id"], "page": form["page"]}
                    if result["page_state"] == "pending"
                    else {}
                )
                self.redirect(view="review", message=message, **context)
            except (ValueError, KeyError, OSError, StopIteration) as error:
                message = (
                    "找不到原件，请核对完整文件路径后重试。"
                    if isinstance(error, FileNotFoundError)
                    else str(error)
                )
                self.respond(409, escape(message))

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.authority = f"127.0.0.1:{server.server_port}"
    server.review_url = f"http://{server.authority}/?token={token}&view=tasks"
    return server


def serve(store: Store, port: int) -> None:
    import os
    from contextlib import ExitStack

    from hrs.codex.execution import create_runner
    from hrs.config import project_root
    from hrs.processing.snapshots import atomic_json
    from hrs.processing.workspace import identity
    from hrs.runtime import lifecycle_lock

    with ExitStack() as resources:
        with lifecycle_lock(project_root()):
            server = resources.enter_context(create_review_server(store, port))
            executor = resources.enter_context(create_runner(store))
            server.executor = executor
            record = {
                **identity(store),
                "url": server.review_url,
                "pid": os.getpid(),
                "runner": "native-codex",
                "workbench_protocol": 2,
            }
            atomic_json(store.root / ".workbench.json", record)
        print(f"史料工作台：{server.review_url}", flush=True)
        try:
            server.serve_forever()
        finally:
            with lifecycle_lock(project_root()):
                resources.close()
                path = store.root / ".workbench.json"
                if (
                    path.exists()
                    and json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid()
                ):
                    path.unlink()
