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
2. For an upload already in the React workbench, inspect its linked native document ID;
   do not import a duplicate. For a new local source, call `process_document(source, title)`. This retains the source,
   chooses page routes and queues the native Codex coordinator. For an existing
   task, read `document_status` before deciding the next action. Use
   `resume_document` for an explicitly requested continuation or technical recovery;
   unresolved source content belongs in the human queue.
3. Use `open_workbench` to obtain this workspace's existing dashboard. Open its
   returned local URL in the Codex browser when the user needs progress, original
   images, human review or library search. The background executor persists progress
   and resumes after human approval; browser and chat history are not the task store.
4. Distinguish queued, running, technical recovery, waiting for a person,
   explicitly paused and published. Use scheduling_plan for bounded stage allocation; workers
   honor returned batch_size/session_max_tasks (legacy two; configured warm sessions up to eight).
   Source-only preflight packets allow independent original reading during OCR;
   freeze observations before candidate exposure and follow the returned packet contract.
   Continue other pages while human pages wait.
   Navigation tasks use historical_organization; follow the source-bound TOC
   contract and preserve conflicts for the local UI. Never resume a paused source
   without an explicit request.
   Show document IDs, affected physical pages, exact unresolved issues
   and the dashboard link. A queued run or extracted candidate is not acceptance.
5. After publication, use `search_library` to verify a real query and provide the
   retained Markdown and original-page evidence. A revision keeps the previous
   published version searchable until its replacement passes.

If independent original-image audits still disagree after detail inspection,
use `request_human_review` with current evidence hashes and the exact unresolved
issue. It records machine uncertainty and protects the human handoff from another
automatic retry. Preserve opposing observations; a prior publication is not gold.

Use `historical_ocr` (GPT-6.1 Terra), `historical_review` and
`historical_organization` (GPT-6.1 Sol). New imports use bounded review: clear
located repairs and explicitly disputed readings get one independent original-first
full-page verification. Unreadable originals and failed verification go to humans.
Retained human-only policies stay frozen until explicit migration/resume; protect
human drafts and decisions. Preserve Markdown footnotes, note ownership, tables, retained
figure images and exact article boundaries.

Human decisions are made explicitly in the single-user local form. Preserve human drafts and
receipts; development permission does not authorize approving real source content
on a person's behalf. Explicit user-authorized workflow tests may release real
source pages only in an isolated authorized workspace, with `workflow_test`
receipts and `content_verified=false`. They do not certify accuracy. Model and
human decisions retain distinct provenance.

If the connected MCP lacks these tools, report that the project connection needs
reloading; do not build an ad hoc second pipeline or alter unrelated Codex settings.
