from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field

from hrs_platform.domain.generation_contracts import CardDraft


class RuntimeStatus(BaseModel):
    backend: str
    workspace_id: str
    root: str
    ocr_model: str
    review_model: str
    review_failure: str
    validation_scope: str


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    byte_length: int = Field(gt=0)


class UploadSession(BaseModel):
    session_id: UUID
    book_id: UUID
    token: str
    endpoint: str
    expires_at: datetime


class BookSummary(BaseModel):
    id: UUID
    title: str
    state: str
    created_at: datetime
    run_id: UUID | None = None
    stage: str | None = None
    revision: int = 0
    pending_count: int = 0
    navigation_pending_count: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "navigation_pending_count", AliasPath("result", "codex_navigation_pending_count")
        ),
    )
    revision_pending: bool = Field(
        default=False,
        validation_alias=AliasChoices("revision_pending", AliasPath("result", "codex_revision_pending")),
    )


class RunProgress(BaseModel):
    stage: Literal["vision"]
    completed: int = Field(ge=0)
    total: int = Field(gt=0)


class PageProgress(BaseModel):
    total: int = Field(gt=0)
    extracted: int = Field(ge=0)
    reviewed: int = Field(ge=0)
    approved: int = Field(ge=0)
    pending: int = Field(ge=0)


class DeletionReceipt(BaseModel):
    book_id: UUID
    state: str
    error: str | None = None


class ActiveTask(BaseModel):
    stage: str
    page: int | None = None
    started_at: float | None = None


class RunExecution(BaseModel):
    state: str | None = None
    paused: bool = False
    automatic_work_remaining: bool = False
    last_progress_at: float | None = None
    active_tasks: list[ActiveTask] = Field(default_factory=list)


class StageMetrics(BaseModel):
    completed: int
    queued: int
    active: int
    median_seconds: float | None = None
    pages_per_minute: float
    samples: int


class SchedulingReport(BaseModel):
    capacity: int
    workers: dict[str, int]
    active: dict[str, int]
    metrics: dict[str, StageMetrics]
    batch_size: int
    cooldown_until: float = 0


class RunSummary(BaseModel):
    id: UUID
    book_id: UUID
    state: str
    stage: str
    revision: int
    updated_at: datetime
    error: dict | None = None
    pending_count: int = 0
    kind: str = "book"
    codex_document_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("codex_document_id", AliasPath("result", "codex_document_id")),
    )
    content_acceptance: str | None = Field(
        default=None,
        validation_alias=AliasChoices("content_acceptance", AliasPath("result", "content_acceptance")),
    )
    progress: RunProgress | None = Field(
        default=None, validation_alias=AliasChoices("progress", AliasPath("result", "progress"))
    )
    page_progress: PageProgress | None = Field(
        default=None,
        validation_alias=AliasChoices("page_progress", AliasPath("result", "codex_page_progress")),
    )
    execution: RunExecution | None = Field(
        default=None, validation_alias=AliasChoices("execution", AliasPath("result", "codex_execution"))
    )
    scheduling: SchedulingReport | None = Field(
        default=None, validation_alias=AliasChoices("scheduling", AliasPath("result", "codex_scheduling"))
    )


class NavigationTarget(BaseModel):
    physical_page: int
    printed_label: str | None = None
    anchor: str
    start: int
    markdown_sha256: str
    image_sha256: str
    sequence: str = "body"


class NavigationEntry(BaseModel):
    id: str
    title: str
    level: int
    path: list[str]
    source_page: int
    source_anchor: str
    source_image: str
    target_label: str
    target_end_label: str | None = None
    status: str
    reason: str
    target: NavigationTarget | None = None
    target_url: str | None = None
    target_image: str | None = None
    target_chapter_id: str | None = None
    candidates: list[NavigationTarget] = Field(default_factory=list)


class NavigationPage(BaseModel):
    page: int
    headings: list[str]
    printed_label: str | None = None
    image: str | None = None


class NavigationReport(BaseModel):
    state: str
    entries: list[NavigationEntry]
    version: int
    sha256: str
    coverage_complete: bool = False
    approved_pages: list[NavigationPage] = Field(default_factory=list)


class NavigationDecision(BaseModel):
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_page: int | None = Field(default=None, ge=1)
    target_anchor: str = ""


class RunControlReceipt(BaseModel):
    document_id: str
    state: str


class TusUpload(BaseModel):
    ID: str = ""
    Size: int = 0
    Offset: int = 0
    SizeIsDeferred: bool = False
    IsPartial: bool = False
    IsFinal: bool = False
    MetaData: dict[str, str] = Field(default_factory=dict)
    Storage: dict[str, str] | None = None


class TusEvent(BaseModel):
    Upload: TusUpload


class TusHook(BaseModel):
    Type: Literal["pre-create", "post-create", "post-receive", "post-finish"]
    Event: TusEvent


class ReviewResolution(BaseModel):
    id: str
    action: Literal["keep", "adopt", "edit", "defer"]


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_id: UUID
    expected_revision: int = Field(ge=1)
    expected_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: Literal["confirm", "correct"]
    text: str | None = Field(default=None, min_length=1, max_length=200000)
    resolutions: list[ReviewResolution] | None = None


class ReviewDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=200000)
    resolutions: list[ReviewResolution] = Field(default_factory=list)


class DraftReceipt(BaseModel):
    revision: int
    draft_text: str
    resolutions: list[ReviewResolution] = Field(default_factory=list)


class ReviewReceipt(BaseModel):
    decision_id: UUID
    issue_id: UUID
    run_id: UUID
    revision: int
    pending_count: int
    next_issue_id: UUID | None
    state: str


class ReviewIssueSummary(BaseModel):
    id: UUID
    run_id: UUID
    page: int
    revision: int
    state: str
    text_sha256: str


class ReviewIssue(ReviewIssueSummary):
    text: str
    proposed_text: str | None = None
    draft_text: str | None = None
    reasons: list[str | dict]
    kind: str
    pages: list[int]
    images: list[str]
    footnotes: list["FootnoteLink"] = Field(default_factory=list)
    locations: list[dict] = Field(default_factory=list)
    resolutions: list[ReviewResolution] = Field(default_factory=list)


class FootnoteSpan(BaseModel):
    start: int
    end: int


class FootnoteDefinition(FootnoteSpan):
    marker_end: int


class FootnoteLink(BaseModel):
    id: str
    label: str
    note: FootnoteDefinition
    references: list[FootnoteSpan]


class ConversionPage(BaseModel):
    page: int
    page_count: int
    text: str
    image: str
    machine_status: str
    extraction_method: str = "ocr"
    acceptance_basis: str | None = None
    footnotes: list[FootnoteLink] = Field(default_factory=list)


class ChapterSummary(BaseModel):
    id: UUID
    book_id: UUID
    run_id: UUID
    position: int
    title: str
    kind: str
    pages: list[int]
    codepoints: int


class StructureItem(BaseModel):
    id: str
    kind: str
    title: str
    pages: list[int]
    status: str
    reason: str
    level: int | None = None
    before: str = ""
    after: str = ""


class StructureReport(BaseModel):
    policy: str
    available: bool
    items: list[StructureItem] = Field(default_factory=list)


class Chapter(ChapterSummary):
    text: str
    parts: list[dict]
    footnotes: list[FootnoteLink] = Field(default_factory=list)
    reading_text: str | None = None
    reading_footnotes: list[FootnoteLink] = Field(default_factory=list)
    structure: list[StructureItem] = Field(default_factory=list)


class ExecutionNode(BaseModel):
    id: UUID
    run_id: UUID
    parent_id: UUID | None
    kind: str
    label: str
    objective: str
    state: str
    started_at: datetime
    finished_at: datetime | None


class CardSummary(BaseModel):
    id: UUID
    book_id: UUID
    run_id: UUID
    title: str
    state: str
    created_at: datetime


class CardSource(BaseModel):
    unit_id: str
    chapter_id: UUID
    run_id: UUID
    title: str
    text: str
    pages: list[int]
    start: int
    end: int


class CardQuoteLocation(BaseModel):
    item_id: str
    selection_index: int
    unit_id: str
    start: int | None = Field(description="Start in source-unit text, measured in Unicode code points.")
    end: int | None = Field(description="Exclusive end in source-unit text, measured in Unicode code points.")
    pages: list[int]
    issue: str | None


class CardDetail(CardSummary):
    candidate: CardDraft
    units: list[CardSource]
    verdict: dict
    quote_locations: list[CardQuoteLocation] = Field(default_factory=list)


class SearchContext(BaseModel):
    start: int
    end: int
    text: str
    pages: list[int]
    role: str
    sources: list[dict] = Field(default_factory=list)


class SearchHit(BaseModel):
    toc_path: list[str] = Field(default_factory=list)
    toc_entry_id: str | None = None
    navigation_version: int = 0
    printed_pages: list[dict] = Field(default_factory=list)
    doc_kind: str = "content"
    id: UUID
    book_id: UUID
    chapter_id: UUID
    run_id: UUID
    title: str
    text: str
    pages: list[int]
    start: int
    end: int
    score: float
    book_title: str = ""
    section_path: list[str] = Field(default_factory=list)
    table_scopes: list[dict] = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)
    context: list[SearchContext] = Field(default_factory=list)
    context_truncated: bool = False
    oversized: bool = False


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
