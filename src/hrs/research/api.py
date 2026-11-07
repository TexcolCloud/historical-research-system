"""Application service for native research and its SQL read projections."""

from typing import Literal
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, func, not_, select

from hrs.api.schemas import FootnoteLink, ReadingTarget, SearchHit
from hrs.library.books import get_run
from hrs.library.deletion import require_active
from hrs.library.errors import ServiceError
from hrs.library.footnotes import export_footnotes, resolve_footnotes
from hrs.library.native_workspace import require_workspace
from hrs.processing.workspace import identity
from hrs.research.bridge import ResearchBridge
from hrs.research.models import (
    CardDraft,
    EntityMention,
    EventDraft,
    EvidenceSpan,
    IdentityLink,
    ResearchDate,
    VerificationSubmission,
)
from hrs.research.projection import located_card
from hrs.research.sources import (
    ResearchEvidence,
    current_external_sources,
    current_source_conditions,
    indexed_version,
    visible_research_run,
)
from hrs.storage import models as db


class StartResearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_of: str | None = None
    reason: str | None = Field(default=None, max_length=2000)


class ResearchDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["accept", "edit", "reject"]
    expected_version: int = Field(ge=1)
    text: str | None = Field(default=None, max_length=60000)

    @model_validator(mode="after")
    def edit_has_text(self):
        if self.action == "edit" and (not self.text or not self.text.strip()):
            raise ValueError("编辑决定需要修订后的提要。")
        if self.action != "edit" and self.text is not None:
            raise ValueError("只有编辑决定可以提供修订提要。")
        return self


class ResearchFootnoteResponse(FootnoteLink):
    text: str
    reading_target: ReadingTarget | None = None


class ResearchEvidenceResponse(EvidenceSpan):
    model_config = ConfigDict(extra="allow")
    reading_target: ReadingTarget | None = None
    pages: list[int] = Field(default_factory=list)
    footnotes: list[ResearchFootnoteResponse] = Field(default_factory=list)
    text_sha256: str
    book_id: str | None = None
    publication_sha256: str | None = None
    source_run_id: str | None = None
    original_article_id: str | None = None


class ResearchMentionResponse(EntityMention):
    entity_id: str


class ResearchEventResponse(EventDraft):
    entity_id: str


class ResearchHumanDecision(BaseModel):
    action: Literal["accept", "edit", "reject"]
    at: float
    previous_version: int


class ResearchCardResponse(CardDraft):
    model_config = ConfigDict(extra="allow")
    book_id: str
    run_id: str
    state: str
    version: int
    approval_type: str | None = None
    book_title: str = ""
    checks: VerificationSubmission
    human_decision: ResearchHumanDecision | None = None
    source_current: bool = True
    needs_update: bool = False
    source_status: Literal["current", "revision_pending", "stale"] = "current"
    research_current: bool = True
    superseded: bool = False
    evidence: list[ResearchEvidenceResponse]
    mentions: list[ResearchMentionResponse]
    events: list[ResearchEventResponse]


class ResearchCardSummary(BaseModel):
    id: str
    book_id: str
    run_id: str
    article_id: str
    title: str
    summary: str
    state: str
    version: int
    approval_type: str | None
    tags: list[str]
    document_type: str
    source_layer: str
    article_title: str
    evidence_count: int
    item_count: int
    entity_count: int
    reading_target: ReadingTarget | None = None
    source_current: bool = True
    needs_update: bool = False
    source_status: Literal["current", "revision_pending", "stale"] = "current"
    research_current: bool = True
    superseded: bool = False


class ResearchTaskResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    task_id: str
    stage: str
    state: str
    error: str | None = None
    message: str | None = None


class ResearchRunResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    run_id: str
    book_id: str
    source_run_id: str
    publication_sha256: str
    input_hash: str
    state: str
    version: int
    counts: dict[str, int]
    tasks: list[ResearchTaskResponse]
    card_ids: list[str] = Field(default_factory=list)


class ResearchIdentityLinkResponse(IdentityLink):
    model_config = ConfigDict(extra="allow")
    source_entity_id: str
    card_id: str
    card_title: str = ""
    approval_type: str | None = None
    evidence: list[ResearchEvidenceResponse] = Field(default_factory=list)
    reading_target: ReadingTarget | None = None


class ResearchEntityResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    kind: str
    card_id: str
    book_id: str
    run_id: str
    state: str
    identity_basis: str = ""
    description: str = ""
    date: ResearchDate | None = None
    card_title: str = ""
    mentions: list["ResearchEntityResponse"] = Field(default_factory=list)
    identity_links: list[ResearchIdentityLinkResponse] = Field(default_factory=list)
    evidence: list[ResearchEvidenceResponse] = Field(default_factory=list)
    reading_target: ReadingTarget | None = None
    source_current: bool = True
    needs_update: bool = False
    source_status: Literal["current", "revision_pending", "stale"] = "current"
    research_current: bool = True
    superseded: bool = False


class ResearchBookResponse(BaseModel):
    book_id: str
    book_title: str
    indexed: bool
    source_version: str | None
    runs: list[ResearchRunResponse]
    cards: list[ResearchCardSummary]
    cards_count: int
    entities: list[ResearchEntityResponse]
    entities_count: int
    needs_update: bool = False
    stale_cards_count: int = 0
    source_revision_pending: bool = False


class ResearchCardPage(BaseModel):
    items: list[ResearchCardSummary]
    total: int
    offset: int
    limit: int


class ResearchEntityPage(BaseModel):
    items: list[ResearchEntityResponse]
    total: int
    offset: int
    limit: int


class ResearchSearchResponse(BaseModel):
    query: str
    backend: Literal["hybrid", "lexical"]
    available: bool
    hits: list[SearchHit]
    evidence_status: str
    metrics: dict
    cards: list[ResearchCardSummary]
    entities: list[ResearchEntityResponse]
    research_backend: Literal["sql_literal"]


class Research:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.bridge = ResearchBridge(settings, engine)
        self.workflow = self.bridge.workflow
        self.evidence = ResearchEvidence(settings, engine)

    def invoke(self, operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (KeyError, FileNotFoundError) as error:
            raise ServiceError(404, "研究记录不存在。") from error
        except ValueError as error:
            raise ServiceError(409, str(error)) from error

    def require_run(self, run_id):
        status = self.invoke(self.workflow.status, run_id)
        require_workspace(
            self.engine, get_run(self.engine, status["source_run_id"]), self.bridge.store
        )
        with self.engine.begin() as connection:
            require_active(connection, status["book_id"])
        return status

    def overview(self, book_id, *, include_cards=True, include_entities=True, offset=0, limit=100):
        with self.engine.connect() as connection:
            book = (
                connection.execute(
                    select(db.books).where(
                        db.books.c.id == str(book_id),
                        db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
                    )
                )
                .mappings()
                .one_or_none()
            )
            if book is None:
                raise ServiceError(404, "书籍不存在。")
            source_runs = (
                connection.execute(
                    select(db.runs)
                    .where(
                        db.runs.c.book_id == str(book_id),
                        db.runs.c.kind == "book",
                    )
                    .order_by(db.runs.c.created_at.desc())
                )
                .mappings()
                .all()
            )
        version = next((v for run in source_runs if (v := indexed_version(run))), None)
        cards = self.records(
            db.research_cards, book_id, offset=offset, limit=limit, include=include_cards
        )
        entities = self.records(
            db.research_entities, book_id, offset=offset, limit=limit, include=include_entities
        )
        stale = self.records(db.research_cards, book_id, include=False, stale_only=True)["total"]
        revision_pending = any(
            (run["result"] or {}).get("codex_revision_pending") for run in source_runs
        )
        with self.engine.connect() as connection:
            complete = connection.scalar(
                select(func.count())
                .select_from(
                    db.research_runs.join(db.runs, db.runs.c.id == db.research_runs.c.source_run_id)
                )
                .where(
                    db.research_runs.c.book_id == str(book_id),
                    db.research_runs.c.workspace_id == identity(self.bridge.store)["workspace_id"],
                    db.research_runs.c.state == "completed",
                    *current_source_conditions(db.research_runs, db.runs),
                )
            )
        return {
            "book_id": str(book_id),
            "book_title": book["title"],
            "runs": self.workflow.list_runs(book_id=str(book_id)),
            "cards": cards["items"],
            "cards_count": cards["total"],
            "entities": entities["items"],
            "entities_count": entities["total"],
            "indexed": version is not None,
            "source_version": version["publication_sha256"] if version else None,
            "stale_cards_count": stale,
            "source_revision_pending": revision_pending,
            "needs_update": revision_pending or bool(stale and not complete),
        }

    def records(
        self,
        table,
        book_id=None,
        *,
        offset=0,
        limit=100,
        include=True,
        query=None,
        stale_only=False,
    ):
        workspace_id = identity(self.bridge.store)["workspace_id"]
        conditions = [
            db.research_runs.c.workspace_id == workspace_id,
            db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
        ]
        if stale_only:
            conditions.append(
                not_(
                    func.coalesce(
                        and_(*current_source_conditions(db.research_runs, db.runs)), False
                    )
                )
            )
        else:
            conditions.extend(current_source_conditions(db.research_runs, db.runs))
            conditions.append(db.research_runs.c.id == visible_research_run(workspace_id))
        if table is db.research_cards:
            conditions.append(table.c.state != "excluded")
        if book_id:
            conditions.append(table.c.book_id == str(book_id))
        if query:
            # The cards/entities lane is explicitly SQL literal search; source hits use hybrid search.
            conditions.append(
                table.c.search_text.ilike(
                    "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",
                    escape="\\",
                )
            )
        relation = (
            table.join(db.research_runs)
            .join(db.runs, db.runs.c.id == db.research_runs.c.source_run_id)
            .join(db.books, db.books.c.id == table.c.book_id)
        )
        with self.engine.connect() as connection:
            total = connection.scalar(select(func.count()).select_from(relation).where(*conditions))
            rows = (
                connection.execute(
                    select(table.c.summary, db.runs.c.result["codex_revision_pending"].as_boolean())
                    .select_from(relation)
                    .where(*conditions)
                    .order_by(table.c.id)
                    .offset(offset)
                    .limit(limit)
                ).all()
                if include
                else []
            )
        items = [
            {
                **summary,
                "source_current": not stale_only,
                "needs_update": stale_only or bool(revision),
                "source_status": "stale"
                if stale_only
                else "revision_pending"
                if revision
                else "current",
            }
            for summary, revision in rows
        ]
        return {"items": items, "total": total, "offset": offset, "limit": limit}

    def card(self, card_id):
        card = self.invoke(self.workflow.card, card_id)
        snapshot = self.workflow.snapshot(card["run_id"])
        with self.engine.connect() as connection:
            source = (
                connection.execute(
                    select(
                        db.runs,
                        current_external_sources(db.research_runs).label("external_current"),
                        db.research_runs.c.summary["created_at"]
                        .as_float()
                        .label("research_created"),
                    )
                    .select_from(
                        db.runs.join(db.books, db.books.c.id == db.runs.c.book_id).outerjoin(
                            db.research_runs, db.research_runs.c.id == card["run_id"]
                        )
                    )
                    .where(
                        db.runs.c.id == card["source_run_id"],
                        db.runs.c.book_id == card["book_id"],
                        db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
                    )
                )
                .mappings()
                .one_or_none()
            )
        if source is None:
            raise ServiceError(404, "研究来源已删除或不可用。")
        result = source["result"] or {}
        require_workspace(self.engine, source, self.bridge.store)
        current = snapshot["publication_sha256"] == result.get(
            "codex_publication_sha256"
        ) and snapshot["chapters_sha256"] == (result.get("codex_reading_projection") or {}).get(
            "chapters_sha256"
        )
        current = current and bool(source["external_current"])
        revision = bool(result.get("codex_revision_pending"))
        with self.engine.connect() as connection:
            visible = connection.scalar(
                select(visible_research_run(identity(self.bridge.store)["workspace_id"]))
                .select_from(db.books)
                .where(db.books.c.id == card["book_id"])
            )
            newer = None
            if visible not in {None, card["run_id"]}:
                newer = connection.scalar(
                    select(db.research_runs.c.summary["created_at"].as_float()).where(
                        db.research_runs.c.id == visible
                    )
                )
        superseded = bool(
            newer and newer > (source["research_created"] or card.get("created_at", 0))
        )
        research_current = not superseded
        return {
            **located_card(card, snapshot, self.workflow.sources(card["run_id"])),
            "source_current": current,
            "needs_update": not current or revision,
            "source_status": "stale"
            if not current
            else "revision_pending"
            if revision
            else "current",
            "research_current": research_current,
            "superseded": superseded,
        }

    def entity(self, entity_id):
        entity = self.invoke(self.workflow.entity, entity_id)
        cards = {}

        def locate(mention):
            card_id = mention["card_id"]
            if card_id not in cards:
                cards[card_id] = self.card(card_id)
            card = cards[card_id]
            ids = set(mention.get("evidence_ids", []))
            evidence = [span for span in card["evidence"] if span["id"] in ids]
            return {
                **mention,
                "evidence": evidence,
                "card_title": card["title"],
                "reading_target": next(
                    (e["reading_target"] for e in evidence if e["reading_target"]), None
                ),
                "source_current": card["source_current"],
                "needs_update": card["needs_update"],
                "source_status": card["source_status"],
                "research_current": card["research_current"],
                "superseded": card["superseded"],
            }

        result = locate(entity)

        def retained(items):
            rows = []
            for item in items:
                try:
                    rows.append(locate(item))
                except ServiceError as error:
                    if error.status_code != 404:
                        raise
            return rows

        result["mentions"] = retained(entity.get("mentions", []))
        result["identity_links"] = retained(entity.get("identity_links", []))
        return result

    def start(self, book_id, request):
        return self.invoke(self.bridge.start, book_id, **request.model_dump())

    def control(self, run_id, action):
        self.require_run(run_id)
        status = self.invoke(getattr(self.workflow, action), run_id)
        self.bridge.projection.sync(status)
        return status

    def decide(self, card_id, request):
        card = self.card(card_id)
        if not card["source_current"] or card["superseded"]:
            raise ServiceError(409, "来源已换版，请重新整理研究卡片后再作人工决定。")
        self.require_run(card["run_id"])
        self.invoke(
            self.workflow.decision,
            card_id,
            request.action,
            request.expected_version,
            edited_text=request.text,
        )
        self.bridge.projection.sync(self.workflow.status(card["run_id"]))
        return self.card(card_id)

    def search(self, query, book_id=None, *, semantic=True, limit=8):
        result = self.evidence.search(query, book_id, semantic=semantic, limit=limit)
        return {
            **result,
            "cards": self.records(db.research_cards, book_id, query=query, limit=limit)["items"],
            "entities": self.records(db.research_entities, book_id, query=query, limit=limit)[
                "items"
            ],
            "research_backend": "sql_literal",
        }

    def export(self, card_id, *, base_url=""):
        card = self.card(card_id)
        snapshot = self.workflow.snapshot(card["run_id"])
        articles = {
            article["id"]: article
            for article in [*snapshot["articles"], *self.workflow.sources(card["run_id"])]
        }
        lines = [f"# {card['title']}", ""]
        evidence_anchors = {
            span["id"]: f"research-evidence-{index}"
            for index, span in enumerate(card["evidence"], 1)
        }
        names = {
            item["id"]: item.get("name") or item.get("title") or item["id"]
            for item in [*card.get("items", []), *card.get("mentions", []), *card.get("events", [])]
        }

        def field(label, value):
            lines.append(
                f"- **{label}**：{value if value is not None and value != '' else '未记录'}"
            )

        def references(identities):
            return (
                "、".join(
                    f"[{identity}](#{evidence_anchors[identity.rsplit('/', 1)[-1]]})"
                    if identity.rsplit("/", 1)[-1] in evidence_anchors
                    else f"`{identity}`"
                    for identity in identities
                )
                or "未列出"
            )

        def named(identity):
            return f"{names.get(identity, identity)}（`{identity}`）"

        def date(label, value):
            value = value or {}
            lines.extend([f"### {label}", ""])
            field("原文表述", value.get("text"))
            field("标准化日期", value.get("normalized"))
            field("确定性", value.get("certainty", "unknown"))
            field("日期依据", references(value.get("evidence_ids", [])))
            lines.append("")

        lines.extend(["## 卡片与来源版本", ""])
        for label, value in (
            ("卡片 ID（id）", card["id"]),
            ("卡片版本（version）", card.get("version")),
            ("原任务内 ID（local_id）", card.get("local_id")),
            (
                "已保留修订（revision_versions）",
                "、".join(map(str, card.get("revision_versions", []))),
            ),
            ("研究任务（run_id）", card["run_id"]),
            ("原文任务（source_run_id）", card.get("source_run_id", snapshot.get("source_run_id"))),
            ("书籍（book_id）", card.get("book_id", snapshot.get("book_id"))),
            ("书名", card.get("book_title") or snapshot.get("book_title") or snapshot.get("title")),
            ("文章（article_id）", card.get("article_id")),
            ("原文篇名", card.get("article_title")),
            ("原生文档（document_id）", snapshot.get("document_id")),
            ("工作区（workspace_id）", snapshot.get("workspace_id")),
            (
                "发布版本（publication_sha256）",
                card.get("publication_sha256", snapshot.get("publication_sha256")),
            ),
            ("章节版本（chapters_sha256）", snapshot.get("chapters_sha256")),
            ("文章版本（article_sha256）", card.get("article_sha256")),
            ("原件 SHA256", (snapshot.get("source") or {}).get("sha256")),
            ("状态", card.get("state", "draft")),
            ("认证方式", card.get("approval_type", "none")),
            ("来源状态", card.get("source_status", "current")),
            ("是否需要更新", "是" if card.get("needs_update") else "否"),
            ("研究修订是否已替代", "是" if card.get("superseded") else "否"),
            ("创建时间（Unix 秒）", card.get("created_at")),
        ):
            field(label, value)
        lines.append("")
        if not card.get("source_current", True):
            lines.extend(["历史卡片：来源已换版，需要重新整理；以下保留原版本哈希供追溯。", ""])
        elif card.get("needs_update"):
            lines.extend(["来源正在修订；此卡片对应此前已发布版本。", ""])
        if card.get("superseded"):
            lines.extend(["此研究修订已有后续版本；以下保留原卡片内容。", ""])
        lines.extend(["## 史料属性与年代", ""])
        field("文献类型（document_type）", card.get("document_type"))
        field("史料层次（source_layer）", card.get("source_layer"))
        field("标签（tags）", "、".join(card.get("tags", [])))
        lines.append("")
        date("成文时间（formation_date）", card.get("formation_date"))
        date("事件时间（event_date）", card.get("event_date"))
        lines.extend(["## 提要", "", card["summary"], "", "## 卡片内容", ""])
        kinds = {
            "section": "原文结构",
            "evidence": "史料要点",
            "argument": "论点与论证",
            "issue": "研究问题",
            "paper_use": "论文用途",
        }
        for item in card.get("items", []):
            lines.extend(
                [
                    f"### {item['title'] or kinds.get(item['kind'], item['kind'])}",
                    "",
                    item["text"],
                    "",
                ]
            )
            field("条目 ID", item["id"])
            field("类型", f"{kinds.get(item['kind'], item['kind'])}（{item['kind']}）")
            field("认识状态", item.get("epistemic_state"))
            field("归属 / 发言者", item.get("attribution"))
            field("依据", references(item.get("evidence_ids", [])))
            field("条目使用边界", "；".join(item.get("limitations", [])))
            lines.append("")
        lines.extend(["## 人物、机构、地点与实体记载（mentions）", ""])
        for mention in card.get("mentions", []):
            lines.extend([f"### {mention['name']}", ""])
            field("本卡实体", named(mention["id"]))
            field("实体类型", mention["kind"])
            field("实体 ID", mention.get("entity_id"))
            field("身份依据", mention["identity_basis"])
            field("原文依据", references(mention["evidence_ids"]))
            lines.append("")
        lines.extend(["## 事件（events）", ""])
        for event in card.get("events", []):
            lines.extend([f"### {event['name']}", "", event["description"], ""])
            field("事件 ID", event["id"])
            field("实体 ID", event.get("entity_id"))
            field("参与者", "、".join(named(identity) for identity in event["participant_ids"]))
            field("原文依据", references(event["evidence_ids"]))
            lines.append("")
            date(f"{event['name']}的日期", event.get("date"))
        lines.extend(["## 关系网络（relations）", ""])
        for relation in card.get("relations", []):
            lines.extend(
                [
                    f"### {named(relation['subject_id'])} → {relation['predicate']} → {named(relation['object_id'])}",
                    "",
                ]
            )
            field("关系 ID", relation["id"])
            field("关系作用", relation["role"])
            field("关联理由", relation["reason"])
            field("原文依据", references(relation["evidence_ids"]))
            lines.append("")
        lines.extend(["## 跨卡身份关联（identity_links）", ""])
        for link in card.get("identity_links", []):
            field("本卡实体", named(link["local_mention_id"]))
            field("目标实体", link["entity_id"])
            field(
                "身份关系",
                "可能同一身份（possible_same_as）"
                if link["relation"] == "possible_same_as"
                else "同一身份（same_as）",
            )
            field("关联理由", link["reason"])
            field("原文依据", references(link["evidence_ids"]))
            lines.append("")
        for heading, key in (
            ("使用边界（limitations）", "limitations"),
            ("备选解释（alternatives）", "alternatives"),
            ("待核问题（questions）", "questions"),
        ):
            lines.extend([f"## {heading}", "", *(f"- {value}" for value in card.get(key, [])), ""])
        field("未提出论点的理由（no_argument_reason）", card.get("no_argument_reason"))
        lines.extend(["", "## 核验与人工决定", ""])
        checks = card.get("checks") or {}
        field("独立核验结论", checks.get("decision"))
        field("核验输入哈希", checks.get("input_hash"))
        for key, label in (
            ("checked_card_ids", "核验卡片"),
            ("checked_item_ids", "核验条目"),
            ("checked_evidence_ids", "核验引文"),
            ("checked_relation_ids", "核验关系"),
            ("checked_identity_links", "核验身份关联"),
        ):
            field(label, "、".join(checks.get(key, [])))
        field("补充研究判定已核验", checks.get("checked_research_disposition"))
        field("未解决事项", "；".join(checks.get("issues", [])))
        decision = card.get("human_decision") or {}
        field("人工决定", decision.get("action"))
        field("人工决定所据此前版本", decision.get("previous_version"))
        field("人工决定时间（Unix 秒）", decision.get("at"))
        disposition = card.get("research_disposition") or {}
        if disposition:
            lines.extend(["", "### 补充研究判定", ""])
            for key in ("mode", "category", "reason"):
                field(key, disposition.get(key))
            field("判定依据", references(disposition.get("evidence_ids", [])))
        lines.append("")
        lines.extend(["## 原文证据", ""])
        for index, span in enumerate(card["evidence"], 1):
            target = span.get("reading_target") or {}
            pages = ",".join(map(str, span.get("pages", [])))
            query = urlencode(
                {
                    "tab": "reader",
                    "section": "text",
                    "chapter": target.get("chapter_id", span["article_id"]),
                    "physicalPage": target.get("physical_page", ""),
                    "start": target.get("start", span["start"]),
                    "end": target.get("end", span["end"]),
                    "textSha": span["text_sha256"],
                    "run": target.get("run_id", span.get("source_run_id", card["source_run_id"])),
                }
            )
            href = f"{base_url}/#/books/{span.get('book_id', card['book_id'])}?{query}"
            lines.extend(
                [
                    f'<a id="research-evidence-{index}"></a>',
                    "",
                    f"### {span['id']} · 物理页 {pages}",
                    "",
                    evidence_markdown(span, articles[span["article_id"]], f"research-{index}"),
                    "",
                    f"文章：{span['article_id']}；范围：{span['start']}–{span['end']}；"
                    f"原文 SHA256：{span['text_sha256']}",
                    "",
                    f"原图 SHA256：{target.get('image_sha256', '')}",
                    "",
                    f"来源书籍：{span.get('book_id', card['book_id'])}；原文任务（source_run_id）："
                    f"{span.get('source_run_id', target.get('run_id', card.get('source_run_id')))}；"
                    f"原文章 ID：{span.get('original_article_id', target.get('chapter_id', span['article_id']))}",
                    "",
                    f"来源发布版本（publication_sha256）：{span.get('publication_sha256', card.get('publication_sha256', snapshot.get('publication_sha256', '未记录')))}",
                    "",
                    "源脚注 ID：" + "、".join(span.get("footnote_ids", [])),
                    "",
                    f"[阅读原文]({href})",
                    "",
                ]
            )
        return "\n".join(lines)


def evidence_markdown(span, article, prefix):
    """Reuse the source export's note ownership and namespacing for each quoted range."""
    start, end = span["start"], span["end"]
    parts = [
        {
            "text": article["text"][max(start, part["start"]) : min(end, part["end"])],
            "source": part["source"],
        }
        for part in article["parts"]
        if part["start"] < end and part["end"] > start
    ]
    for note in span.get("footnotes", []):
        left, right = note["note"]["start"], note["note"]["end"]
        if start <= left < right <= end:
            continue
        owner = next(part for part in article["parts"] if part["start"] <= left < part["end"])
        parts.append({"text": "\n\n" + article["text"][left:right], "source": owner["source"]})
    text = "".join(part["text"] for part in parts)
    return export_footnotes(text, resolve_footnotes(text, parts), prefix, parts=parts)
