---
name: historical-research
description: 处理本地 PDF 或图片史料、继续既有处理任务、打开人工待审页面，或检索已入库史料时使用。
---

# Historical research

Use the `historical_research` MCP and native Codex subagents. Read
`workflow_instructions` for the current processing contract; it is the single
source for PDF routing, original-first review, fallback limits and publication.

1. Call `workspace_status`. Keep its workspace ID for this request. The tools,
   dashboard and executor must use that same workspace; report a mismatch instead
   of silently changing directories or importing a second copy.
2. For a new source, call `process_document(source, title)`. This retains the source,
   chooses page routes and queues the native Codex coordinator. For an existing
   task, read `document_status` before deciding the next action. Use
   `resume_document` for an explicitly requested continuation or technical recovery;
   unresolved source content belongs in the human queue.
3. Use `open_workbench` to obtain this workspace's existing dashboard. Open its
   returned local URL in the Codex browser when the user needs progress, original
   images, human review or library search. The background executor persists progress
   and resumes after human approval; browser and chat history are not the task store.
4. Distinguish queued, running, technical recovery, waiting for a person and
   published. Show document IDs, affected physical pages, exact unresolved issues
   and the dashboard link. A queued run or extracted candidate is not acceptance.
5. After publication, use `search_library` to verify a real query and provide the
   retained Markdown and original-page evidence. A revision keeps the previous
   published version searchable until its replacement passes.

Human decisions are made explicitly in the local form. Preserve human drafts and
receipts; development permission does not authorize approving real source content
on a person's behalf. Model and human decisions retain distinct provenance.

If the connected MCP lacks these tools, report that the project connection needs
reloading; do not build an ad hoc second pipeline or alter unrelated Codex settings.
