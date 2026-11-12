---
name: historical-research
description: 导入 PDF 或图片史料、继续处理任务、打开人工待审页面、检索已入库资料，或处理史料卡、实体事件证据与专题选材分析时使用。
---

# Historical research

Use the `historical_research` MCP and native Codex subagents. Read
`workflow_instructions` for the current processing contract; it is the single
source for PDF routing, original-first review, publication and post-index source cards.

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

For source cards and entity/event evidence, open the workbench's card view and
inspect `research_status(run_id)`. Matching published, indexed, content-verified
articles automatically enter a separate queue; original reading remains available.
New v7 document dossiers use native Sol author → independent verify and, when required, one
directed repair → fresh independent reverify. Read each `research_claim` packet's
frozen policy and schema. Follow the source-card section of `workflow_instructions`
for complete source reads, candidate gates, omission inventories and coverage.
Retain metadata, every source part, exact quotations/footnotes, source limitations,
people, organizations, places, documents, event modalities and participant roles.
OpenSearch is an optional author/repair tool; new cards require no question plan
or mandatory search receipt. Cross-card claims still require frozen complete
sources and both card versions. Search hits and graph neighbours remain leads.
New dossiers add source-bound background, scope, structure, value and three-part
evidence boundaries; limited argument units retain direct citations, applicability
limits and what still needs corroboration. Supported full/short citations distinguish
actual printed pages from PDF physical pages and carrier publication from formation
and event dates. Missing bibliography stays explicitly partial/unknown. Independent
verification checks every advertised dossier field; interpretation is not source fact
or a historical credibility grade. Older cards display their own retained fields.
Canonical identity changes and structured card revisions are explicit local human
actions. Personal notes are separately versioned and remain distinct from source
claims. Existing v1–v6 runs keep their frozen contracts; index rebuilding never
upgrades them. New research-question and automatic link-refresh execution is
retired; prior history remains readable/exportable. Old `kind=cards` stays retired.

For personal studies, use the workbench's topics, arguments, timeline and canvas.
Materials pin source card versions and evidence IDs; personal edits, manual links
and timeline clues remain separate from verified source assertions. Analysis is
explicitly requested for selected material: native Sol author and fresh verifier
use the `study_analysis_*` tools against the same workspace. Follow the personal
studies contract in `workflow_instructions`; only supported suggestions can be
explicitly adopted by the user. A personal question or adopted note does not
reactivate the retired research-question executor or approve source content.

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
