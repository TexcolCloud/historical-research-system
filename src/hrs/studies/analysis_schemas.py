"""Bounded source-linked suggestions and their independent machine checks."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["summary", "compare", "leads"]
    material_ids: list[str] = Field(min_length=1, max_length=12)
    prompt: str = Field(default="", max_length=6000)


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material_id: str = Field(min_length=1, max_length=200)
    evidence_id: str = Field(min_length=1, max_length=200)


class Suggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=2000)
    limitations: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=30)


class AnalysisCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suggestions: list[Suggestion] = Field(min_length=1, max_length=8)


class SuggestionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suggestion_id: str = Field(min_length=1, max_length=100)
    supported: bool
    issues: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    ] = Field(max_length=30)


class AnalysisVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checks: list[SuggestionCheck] = Field(min_length=1, max_length=8)


class AnalysisAdoption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    suggestion_id: str
    kind: Literal["note", "argument", "thought"] = "note"
    title: str | None = Field(default=None, min_length=1, max_length=300)


class AnalysisVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class VerifiedSuggestion(Suggestion):
    verified: bool
    issues: list[str] = Field(default_factory=list)
    adopted_record_id: str | None = None


class AnalysisResponse(BaseModel):
    id: str
    topic_id: str
    mode: Literal["summary", "compare", "leads"]
    material_ids: list[str]
    prompt: str
    state: Literal["queued", "running", "verification", "ready", "failed", "cancelled"]
    suggestions: list[VerifiedSuggestion] = Field(default_factory=list)
    error: str | None = None
    version: int
    created_at: float
    updated_at: float


class AnalysisPage(BaseModel):
    items: list[AnalysisResponse]
    total: int
    offset: int
    limit: int
