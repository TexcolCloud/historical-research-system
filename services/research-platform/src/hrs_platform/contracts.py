from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field

from .domain.generation_contracts import CardDraft


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


class RunProgress(BaseModel):
    stage: Literal["vision"]
    completed: int = Field(ge=0)
    total: int = Field(gt=0)


class DeletionReceipt(BaseModel):
    book_id: UUID
    state: str
    error: str | None = None


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
    progress: RunProgress | None = Field(
        default=None, validation_alias=AliasChoices("progress", AliasPath("result", "progress"))
    )


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


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_id: UUID
    expected_revision: int = Field(ge=1)
    expected_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: Literal["confirm", "correct"]
    text: str | None = Field(default=None, min_length=1, max_length=200000)


class ReviewDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=200000)


class DraftReceipt(BaseModel):
    revision: int
    draft_text: str


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
    draft_text: str | None = None
    reasons: list[str]
    kind: str
    pages: list[int]
    images: list[str]


class ConversionPage(BaseModel):
    page: int
    page_count: int
    text: str
    image: str
    machine_status: str


class ChapterSummary(BaseModel):
    id: UUID
    book_id: UUID
    run_id: UUID
    position: int
    title: str
    kind: str
    pages: list[int]
    codepoints: int


class Chapter(ChapterSummary):
    text: str
    parts: list[dict]


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


class CardDetail(CardSummary):
    candidate: CardDraft
    units: list[CardSource]
    verdict: dict


class SearchContext(BaseModel):
    start: int
    end: int
    text: str
    pages: list[int]
    role: str
    sources: list[dict] = Field(default_factory=list)


class SearchHit(BaseModel):
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
    sources: list[dict] = Field(default_factory=list)
    context: list[SearchContext] = Field(default_factory=list)
    context_truncated: bool = False
    oversized: bool = False


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
