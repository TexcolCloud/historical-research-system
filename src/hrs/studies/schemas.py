"""Personal study HTTP contracts; source facts remain separate frozen records."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from hrs.research.schemas import ResearchEvidenceResponse, ResearchTimelineItem


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TopicCreate(Request):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=60000)
    book_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value):
        if not value.strip():
            raise ValueError("请填写专题名称。")
        return value.strip()


class TopicUpdate(Request):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=60000)
    book_ids: list[str] | None = Field(default=None, max_length=100)
    archived: bool | None = None


class MaterialCreate(Request):
    expected_version: int = Field(ge=1)
    card_id: str = Field(min_length=1, max_length=200)
    card_version: int = Field(ge=1)
    evidence_ids: list[str] = Field(default_factory=list, max_length=200)


class SourceReference(BaseModel):
    id: str
    card_id: str
    card_version: int
    book_id: str
    article_id: str
    title: str
    source_run_id: str
    publication_sha256: str
    article_sha256: str
    source_citation: str = ""
    printed_pages: str = ""
    citation_completeness: str = "unknown"
    preview: str = ""
    preview_truncated: bool = False
    authors: str = ""
    formation_date_text: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    source_current: bool = True
    source_status: str = "current"
    evidence: list[ResearchEvidenceResponse] = Field(default_factory=list)


class StudyDate(Request):
    text: str = Field(default="", max_length=2000)
    normalized: str | None = Field(default=None, max_length=100)
    certainty: Literal["stated", "inferred", "unknown", "disputed"] = "unknown"
    evidence_ids: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def valid_normalized_date(self):
        from hrs.research.network import date_position

        if self.normalized and (
            not self.text.strip()
            or date_position({"normalized": self.normalized, "certainty": "stated"}).undated
        ):
            raise ValueError("规范日期无效；请保留原日期文字，填写有效年月日或区间。")
        return self


RecordKind = Literal["argument", "thought", "question", "note", "clue"]


class RecordCreate(Request):
    kind: RecordKind
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(default="", max_length=60000)
    limitations: str = Field(default="", max_length=10000)
    source_refs: list[str] = Field(default_factory=list, max_length=200)
    date: StudyDate | None = None
    event_status: Literal["reported", "planned", "conditional", "negated", "unknown"] = "unknown"


class RecordUpdate(Request):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    text: str | None = Field(default=None, max_length=60000)
    limitations: str | None = Field(default=None, max_length=10000)
    source_refs: list[str] | None = Field(default=None, max_length=200)
    date: StudyDate | None = None
    event_status: Literal["reported", "planned", "conditional", "negated", "unknown"] | None = None
    archived: bool | None = None


class CanvasNode(Request):
    id: str = Field(min_length=1, max_length=200)
    record_kind: Literal["source", "record", "group"]
    record_id: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=300)
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    width: float = Field(default=280, gt=0, le=20000, allow_inf_nan=False)
    height: float = Field(default=200, gt=0, le=20000, allow_inf_nan=False)
    parent_id: str | None = Field(default=None, max_length=200)
    collapsed: bool = False


class CanvasEdge(Request):
    id: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=200)
    label: str = Field(default="", max_length=1000)
    relation: str = Field(default="association", max_length=100)
    notes: str = Field(default="", max_length=10000)
    source_refs: list[str] = Field(default_factory=list, max_length=200)


class CanvasViewport(Request):
    x: float = Field(default=0, allow_inf_nan=False)
    y: float = Field(default=0, allow_inf_nan=False)
    z: float = Field(default=1, gt=0, le=20, allow_inf_nan=False)


class CanvasDocument(Request):
    nodes: list[CanvasNode] = Field(default_factory=list, max_length=2000)
    edges: list[CanvasEdge] = Field(default_factory=list, max_length=4000)
    viewport: CanvasViewport = Field(default_factory=CanvasViewport)

    @model_validator(mode="after")
    def valid_graph(self):
        nodes = {node.id: node for node in self.nodes}
        if len(nodes) != len(self.nodes) or len({edge.id for edge in self.edges}) != len(
            self.edges
        ):
            raise ValueError("画布节点和连线标识不能重复。")
        for node in self.nodes:
            if node.record_kind != "group" and not node.record_id:
                raise ValueError("材料卡片必须引用研究记录。")
            ancestors = {node.id}
            parent = node.parent_id
            while parent:
                if (
                    parent in ancestors
                    or parent not in nodes
                    or nodes[parent].record_kind != "group"
                ):
                    raise ValueError("分组必须引用现有分组，且不能形成循环。")
                ancestors.add(parent)
                parent = nodes[parent].parent_id
        if any(edge.source not in nodes or edge.target not in nodes for edge in self.edges):
            raise ValueError("连线两端必须存在于当前画布。")
        return self


class CanvasCreate(Request):
    title: str = Field(min_length=1, max_length=300)
    document: CanvasDocument = Field(default_factory=CanvasDocument)


class CanvasImport(Request):
    expected_version: int = Field(ge=1)
    backup: dict[str, JsonValue]
    title: str | None = Field(default=None, min_length=1, max_length=300)


class CanvasUpdate(Request):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    document: CanvasDocument | None = None
    archived: bool | None = None


class ItemResponse(BaseModel):
    id: str
    topic_id: str | None = None
    title: str
    version: int
    archived: bool = False
    created_at: float
    updated_at: float


class TopicResponse(ItemResponse):
    description: str = ""
    book_ids: list[str] = Field(default_factory=list)
    materials: list[SourceReference] = Field(default_factory=list)
    material_count: int = 0


class TopicPage(BaseModel):
    items: list[TopicResponse]
    total: int
    offset: int
    limit: int


class RecordResponse(ItemResponse):
    kind: RecordKind
    text: str = ""
    limitations: str = ""
    source_refs: list[str] = Field(default_factory=list)
    date: StudyDate | None = None
    event_status: str = "unknown"
    origin: str = "personal"
    analysis_id: str | None = None


class RecordPage(BaseModel):
    items: list[RecordResponse]
    total: int
    offset: int
    limit: int


class CanvasResponse(ItemResponse):
    document: CanvasDocument
    import_warnings: list[str] = Field(default_factory=list)


class CanvasSummary(ItemResponse):
    node_count: int = 0
    edge_count: int = 0


class CanvasPage(BaseModel):
    items: list[CanvasSummary]
    total: int
    offset: int
    limit: int


class RecordPreview(RecordResponse):
    preview_only: Literal[True] = True
    text_truncated: bool = False


class CanvasContextResponse(BaseModel):
    canvas_id: str
    canvas_version: int
    topic_id: str
    records: list[RecordPreview]
    materials: list[SourceReference]


class PersonalTimelineItem(BaseModel):
    id: str
    item_kind: Literal["personal"] = "personal"
    origin: Literal["personal", "ai_adopted"] = "personal"
    personal_record_id: str
    name: str
    description: str = ""
    date: StudyDate
    date_basis: Literal["event", "formation"] = "event"
    date_precision: Literal["year", "month", "day", "range", "unknown"]
    date_start: str | None = None
    date_end: str | None = None
    undated: bool
    event_status: str = "unknown"
    status_note: str = "个人补记，不改写史料日期。"
    source_refs: list[str] = Field(default_factory=list)
    preview_only: Literal[True] = True
    description_truncated: bool = False


class StudyTimelineResponse(BaseModel):
    items: list[ResearchTimelineItem | PersonalTimelineItem]
    total: int
    dated_total: int
    undated_total: int
    offset: int
    limit: int
    date_basis: Literal["event", "formation"]
    truncated: bool


class CanvasRevision(BaseModel):
    version: int
    title: str
    updated_at: float
    archived: bool
