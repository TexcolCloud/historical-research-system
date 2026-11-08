---
name: historical-research
description: 导入 PDF 或图片史料、继续处理任务、打开人工待审页面、检索已入库资料，或处理研究卡片与人物事件证据时使用。
---

# Historical research

Use the `historical_research` MCP and native Codex subagents. Read
`workflow_instructions` for the current processing contract; it is the single
source for PDF routing, original-first review, publication and post-index research.

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
5. After publication, use `search_library` and inspect its reported backend and
   availability before citing results. It shares platform hybrid retrieval; an
   unavailable platform may return explicitly labelled local literal matches,
   never comprehensive evidence or proof of absence. Provide the retained
   Markdown and original-page evidence. A revision keeps the previous published
   version searchable until its replacement passes.

For research cards and person/event evidence, open the workbench's research view
and inspect `research_status(run_id)`. Research starts from published, indexed,
content-verified articles and has an independent queue; original reading does not
wait for cards. Its author, enrich, independent verify and, when required, one
directed repair followed by fresh independent reverify use native Sol workers
and their `research_claim` packets. Follow the research section of
`workflow_instructions` for complete source reads, candidate gates, cross-book
freezing, source-first omission inventories and submission coverage. New complete
cards retain metadata, every source part, named entities, event modalities and
participant roles. Enrichment persists its question plan before retrieval;
cross-card relationships freeze both card versions and complete sources.
Use the research workspace for question scope, adopted/excluded evidence,
unresolved findings and source-bound person/event navigation. Search hits and
graph neighbours are leads; retain identity uncertainty. Canonical identity
changes and structured card revisions are explicit local human actions.
Old frozen policies remain unchanged; upgrade a basic card through an explicit
research revision. Old `kind=cards` execution stays retired.

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

Human page and research-card decisions are made explicitly in the single-user local form;
research accept/edit/reject is a versioned UI/API action, never an MCP approval tool.
Preserve human drafts and receipts; development permission does not authorize approving real source content
on a person's behalf. Explicit user-authorized workflow tests may release real
source pages only in an isolated authorized workspace, with `workflow_test`
receipts and `content_verified=false`. They do not certify accuracy. Model and
human decisions retain distinct provenance.

If the connected MCP lacks these tools, report that the project connection needs
reloading; do not build an ad hoc second pipeline or alter unrelated Codex settings.
