# Historical Research System

## Delivery phase

- Build each pipeline module as a usable happy-path implementation with basic validation and recoverable outputs.
- Defer cross-module boundary hardening, exhaustive safety policy, and final integration constraints until the user explicitly starts the project-wide boundary pass after all modules are complete.
- Keep module interfaces and artifacts explicit so that final boundary work can be applied without rewriting extraction logic.

## Development review

- Automatically use the active Codex GPT vision model for every development or evaluation artifact that reaches sample, image, structure, or candidate review; persist an original-first structured verdict and evidence hashes without pausing for routine user approval.
- Record GPT verdicts as machine approval, never as user-human review or sealed gold approval. Ask the user only for final acceptance or when required source evidence is missing. Unreadable scans after retry remain pending without forced transcription.
- Production execution and runtime acceptance are independent of GPT APIs, Codex, and GPT development approval. Original-image checks in conversion, ingestion and card generation use local Qwen3-VL-8B-Instruct Q4_K_M; never fall back to DeepSeek for vision. DeepSeek remains the text generation/reasoning provider. GPT assists development evaluation only; its verdicts are not production release approval. For model setup or GPU scheduling changes, read [local vision runtime](docs/local-vision-20260912.md).

## Content acceptance

- Accept typo corrections, simplified/traditional character conversion, and punctuation or display normalization when the article's meaning is preserved; exact transcription of the printed spelling is not required.
- Preserve people, places, dates, quantities, factual claims, negation and causal relations, quotation/note ownership, and article organization. Keep original evidence and change records so accepted normalization remains traceable.
- Judge a repair by the final usable content: record harmless normalization separately, and close content or structural regressions rather than treating a pending-review status as a successful repair.
- When the original scan itself remains unclear after retry, preserve the source and mark the affected content pending; do not force a reading or invent missing words. This exception does not close errors introduced from clear originals.
- After OCR produces Markdown, run full local visual review. Preserve model corrections as proposals for human review. Library ingestion and downstream partitioning wait until the whole document has no unresolved or unreviewed content; clean blocks remain readable in the review workspace. Preserve table originals and recognized content for human checking when automatic review fails. Existing published records are not retroactively deleted.

## Document conversion baseline

- `services/document-extraction` uses Docling for PDF/image conversion with one configured recognition backend. The 2026-09-10 retained-sample comparison selected PaddleOCR-VL-1.6 as the default; `config/rapidocr.json` is an explicit alternative, never an automatic second recognizer.
- See [OCR selection evidence](output/pdf/ocr-selection-20260910/OCR选型报告.md). Keep the frozen comparison separate from development sample corrections; it is not an unseen-data accuracy guarantee. Do not resume OCR/table tuning without the user reopening it.
- Follow the current [module README](services/document-extraction/README.md). The dual-OCR implementation, table arbitration and correction-memory code have been retired; the recoverable source snapshot and cleanup evidence are linked there.
- Extraction preserves carrier content and provenance; article partition and bibliographic identity belong to document ingestion. Keep unclaimed identity explicit in the output contract.
- Preserve its CLI, output artifacts, local visual-review routing, and OCR model layout during later integration work.
