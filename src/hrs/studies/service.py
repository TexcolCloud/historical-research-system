"""Personal topics, argument collections and canvases, bound to retained evidence."""

import json
from copy import deepcopy
from uuid import UUID, uuid4

from pydantic import ValidationError

from hrs.library.errors import ServiceError
from hrs.research.network import DateWindow, NetworkQuery, date_position
from hrs.studies.schemas import (
    CanvasCreate,
    CanvasDocument,
    CanvasUpdate,
    MaterialCreate,
    RecordCreate,
    RecordUpdate,
    TopicCreate,
    TopicUpdate,
)
from hrs.studies.store import StudyStore


def _request(model, value):
    try:
        return model.model_validate(
            value.model_dump(exclude_unset=True) if hasattr(value, "model_dump") else value
        )
    except ValidationError as error:
        raise ServiceError(400, "研究记录格式无效，请检查字段及内容范围。") from error


class StudyService:
    """Personal organization never grants source approval or overwrites source records."""

    def __init__(self, research):
        self.research = research
        self.store = StudyStore(research.bridge.store)

    def _books(self, values):
        try:
            selected = list(dict.fromkeys(str(UUID(value)) for value in values))
        except (ValueError, TypeError, AttributeError) as error:
            raise ServiceError(400, "专题书籍范围无效。") from error
        if set(selected) - set(self.research.workspace.available_books()):
            raise ServiceError(404, "所选书籍未在当前工作区发布或已不可用。")
        return selected

    def _current_cards(self):
        return {
            (card["id"], card["version"], card["publication_sha256"]): card
            for card in self.research.workspace._network_query().current_cards
        }

    @staticmethod
    def _source_summary(material, current):
        active = current.get(
            (material["card_id"], material["card_version"], material["publication_sha256"])
        )
        return {
            **{key: value for key, value in material.items() if key != "evidence"},
            "source_current": bool(active),
            "source_status": "revision_pending"
            if active and active.get("needs_update")
            else "current"
            if active
            else "stale",
            "evidence": [],
        }

    def create_topic(self, request):
        body = _request(TopicCreate, request)
        payload = body.model_dump()
        payload.update(book_ids=self._books(body.book_ids), materials=[])
        return self.store.create("topic", payload)

    def topics(self, *, archived=False, offset=0, limit=50):
        result = self.store.list(
            "topic", archived=archived, offset=offset, limit=limit, summary=True
        )
        # List metadata intentionally omits pinned quotations and source hydration.
        result["items"] = [{**item, "materials": []} for item in result["items"]]
        return result

    def topic(self, topic_id):
        value = self.store.read("topic", topic_id)
        current = self._current_cards() if value.get("materials") else {}
        return {
            **value,
            "material_count": len(value.get("materials", [])),
            "materials": [
                self._source_summary(item, current) for item in value.get("materials", [])
            ],
        }

    def update_topic(self, topic_id, request):
        body = _request(TopicUpdate, request)
        changes = body.model_dump(exclude_unset=True, exclude={"expected_version"})
        if any(changes.get(key) is None for key in ("description", "archived") if key in changes):
            raise ServiceError(400, "专题说明和归档状态不能为null。")
        if "title" in changes and (changes["title"] is None or not changes["title"].strip()):
            raise ServiceError(400, "请填写专题名称。")
        if "book_ids" in changes:
            changes["book_ids"] = self._books(changes["book_ids"] or [])
        self.store.save("topic", topic_id, changes, expected_version=body.expected_version)
        return self.topic(topic_id)

    def add_material(self, topic_id, request):
        body = _request(MaterialCreate, request)
        card = self.research.workspace.current_card(body.card_id, body.card_version)
        selected = set(body.evidence_ids)
        available = {span["id"]: span for span in card["evidence"]}
        if selected - available.keys():
            raise ServiceError(409, "选材引文不属于指定卡片版本。")
        if not selected:
            selected = set(available)
        preview = next((span["quote"] for key, span in available.items() if key in selected), "")
        pinned = {
            "id": uuid4().hex,
            "card_id": card["id"],
            "card_version": card["version"],
            "book_id": card["book_id"],
            "article_id": card["article_id"],
            "title": card["title"],
            "source_run_id": card["source_run_id"],
            "publication_sha256": card["publication_sha256"],
            "article_sha256": card["article_sha256"],
            "source_citation": card.get("source_citation", ""),
            "printed_pages": card.get("printed_pages", ""),
            "citation_completeness": card.get("citation_completeness", "unknown"),
            "preview": preview[:300],
            "preview_truncated": len(preview) > 300,
            "authors": card.get("authors", ""),
            "formation_date_text": (card.get("formation_date") or {}).get("text", ""),
            "evidence_ids": [key for key in available if key in selected],
            "evidence": [deepcopy(span) for key, span in available.items() if key in selected],
        }
        with self.store.transaction():
            topic = self.store.read("topic", topic_id)
            if body.expected_version > topic["version"]:
                raise ServiceError(409, "专题版本无效，请重新读取后添加选材。")
            if topic.get("archived"):
                raise ServiceError(409, "专题已归档，请恢复后添加选材。")
            existing = next(
                (
                    item
                    for item in topic.get("materials", [])
                    if all(
                        item.get(key) == pinned[key]
                        for key in ("card_id", "card_version", "publication_sha256", "evidence_ids")
                    )
                ),
                None,
            )
            if existing:
                return self.topic(topic_id)
            if len(topic.get("materials", [])) >= 2000:
                raise ServiceError(400, "专题选材已达2000项，请拆分专题。")
            self.store.create("material", pinned, topic_id=topic_id, item_id=pinned["id"])
            self.store.save(
                "topic",
                topic_id,
                {
                    "materials": [
                        *topic.get("materials", []),
                        {key: value for key, value in pinned.items() if key != "evidence"},
                    ],
                },
                expected_version=body.expected_version,
            )
        return self.topic(topic_id)

    def source_materials(self, topic_id, ids=None, *, require_current=False):
        topic = self.store.read("topic", topic_id)
        selected = set(ids) if ids is not None else None
        items = [
            item
            for item in topic.get("materials", [])
            if selected is None or item["id"] in selected
        ]
        if selected is not None and selected - {item["id"] for item in items}:
            raise ServiceError(404, "选材不属于当前专题。")
        current = self._current_cards() if items else {}
        with self.store.ledger.connect() as connection:
            retained = (
                {
                    row[0]: json.loads(row[1])
                    for row in connection.execute(
                        "SELECT id,payload FROM study_items WHERE workspace_id=? AND kind='material' "
                        "AND topic_id=? AND id IN (SELECT value FROM json_each(?))",
                        (
                            self.store.workspace_id,
                            topic_id,
                            json.dumps([item["id"] for item in items]),
                        ),
                    )
                }
                if items
                else {}
            )
        result = [
            {
                **self._source_summary(item, current),
                "evidence": deepcopy(retained[item["id"]]["evidence"]),
            }
            for item in items
        ]
        if require_current and any(not item["source_current"] for item in result):
            raise ServiceError(409, "部分来源已有修订或已不可用，请先核对专题选材。")
        return result

    def material(self, topic_id, material_id):
        return self.source_materials(topic_id, [material_id])[0]

    def remove_material(self, topic_id, material_id, *, expected_version):
        with self.store.transaction():
            topic = self.store.read("topic", topic_id)
            if material_id not in {item["id"] for item in topic.get("materials", [])}:
                raise ServiceError(404, "专题选材不存在。")
            with self.store.ledger.connect() as connection:
                rows = connection.execute(
                    "SELECT kind,payload FROM study_items WHERE workspace_id=? AND topic_id=?",
                    (self.store.workspace_id, topic_id),
                )
                for kind, content in rows:
                    value = json.loads(content)
                    if (
                        (kind == "record" and material_id in value.get("source_refs", []))
                        or (
                            kind == "canvas"
                            and any(
                                node.get("record_kind") == "source"
                                and node.get("record_id") == material_id
                                for node in value.get("document", {}).get("nodes", [])
                            )
                        )
                        or (
                            kind == "canvas"
                            and any(
                                material_id in edge.get("source_refs", [])
                                for edge in value.get("document", {}).get("edges", [])
                            )
                        )
                    ):
                        raise ServiceError(
                            409, "材料仍被论据、记录或画布引用（含归档记录），请先移除相关引用。"
                        )
            self.store.save(
                "topic",
                topic_id,
                {
                    "materials": [item for item in topic["materials"] if item["id"] != material_id],
                },
                expected_version=expected_version,
            )
        return self.topic(topic_id)

    @staticmethod
    def _validate_refs(topic, refs):
        if set(refs) - {item["id"] for item in topic.get("materials", [])}:
            raise ServiceError(400, "引用必须来自当前专题的保留选材。")
        if len(set(refs)) != len(refs):
            raise ServiceError(400, "同一材料引用不能重复。")

    def create_record(
        self, topic_id, request, *, origin="personal", analysis_id=None, item_id=None
    ):
        body = _request(RecordCreate, request)
        if not body.title.strip():
            raise ServiceError(400, "请填写记录标题。")
        with self.store.transaction():
            topic = self.store.read("topic", topic_id)
            if topic.get("archived"):
                raise ServiceError(409, "专题已归档，请恢复后编辑。")
            self._validate_refs(topic, body.source_refs)
            return self.store.create(
                "record",
                {**body.model_dump(), "origin": origin, "analysis_id": analysis_id},
                topic_id=topic_id,
                item_id=item_id,
            )

    def records(self, topic_id, *, kind=None, archived=False, offset=0, limit=50):
        self.store.read("topic", topic_id)
        if kind is None:
            return self.store.list(
                "record", topic_id=topic_id, archived=archived, offset=offset, limit=limit
            )
        if kind not in {"argument", "thought", "question", "note", "clue"}:
            raise ServiceError(400, "记录类型无效。")
        # Filter in SQLite before paging, including counts.
        with self.store.ledger.connect() as connection:
            if not self.store.exists(connection):
                return {"items": [], "total": 0, "offset": offset, "limit": limit}
            where = "workspace_id=? AND kind='record' AND topic_id=? AND json_extract(payload,'$.kind')=? AND archived=?"
            args = [self.store.workspace_id, topic_id, kind, int(archived)]
            total = connection.execute(
                "SELECT COUNT(*) FROM study_items WHERE " + where, args
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT payload FROM study_items WHERE "
                + where
                + " ORDER BY updated_at DESC,id LIMIT ? OFFSET ?",
                [*args, limit, offset],
            )
            return {
                "items": [json.loads(row[0]) for row in rows],
                "total": total,
                "offset": offset,
                "limit": limit,
            }

    def record(self, record_id):
        return self.store.read("record", record_id)

    def update_record(self, record_id, request):
        body = _request(RecordUpdate, request)
        changes = body.model_dump(exclude_unset=True, exclude={"expected_version"})
        if any(
            changes.get(key) is None
            for key in ("text", "limitations", "event_status", "archived", "source_refs")
            if key in changes
        ):
            raise ServiceError(400, "正文、引用和记录状态不能为null；日期可以清除。")
        if "title" in changes and (changes["title"] is None or not changes["title"].strip()):
            raise ServiceError(400, "请填写记录标题。")
        with self.store.transaction():
            previous = self.store.read("record", record_id)
            topic = self.store.read("topic", previous["topic_id"])
            if "source_refs" in changes:
                self._validate_refs(topic, changes["source_refs"] or [])
            return self.store.save(
                "record", record_id, changes, expected_version=body.expected_version
            )

    def _validate_document(self, topic, document):
        for edge in document.edges:
            self._validate_refs(topic, edge.source_refs)
        source_ids = {item["id"] for item in topic.get("materials", [])}
        record_ids = set()
        with self.store.ledger.connect() as connection:
            if self.store.exists(connection):
                record_ids = {
                    row[0]
                    for row in connection.execute(
                        "SELECT id FROM study_items WHERE workspace_id=? AND kind='record' AND topic_id=?",
                        (self.store.workspace_id, topic["id"]),
                    )
                }
        for node in document.nodes:
            if (node.record_kind == "source" and node.record_id not in source_ids) or (
                node.record_kind == "record" and node.record_id not in record_ids
            ):
                raise ServiceError(400, "画布卡片必须引用当前专题的材料或研究记录。")

    def create_canvas(self, topic_id, request, *, import_warnings=()):
        body = _request(CanvasCreate, request)
        if not body.title.strip():
            raise ServiceError(400, "请填写画布名称。")
        with self.store.transaction():
            topic = self.store.read("topic", topic_id)
            if topic.get("archived"):
                raise ServiceError(409, "专题已归档，请恢复后编辑。")
            self._validate_document(topic, body.document)
            return self.store.create(
                "canvas",
                {**body.model_dump(), "import_warnings": list(import_warnings)},
                topic_id=topic_id,
            )

    def canvases(self, topic_id, *, archived=False, offset=0, limit=50):
        self.store.read("topic", topic_id)
        return self.store.list(
            "canvas", topic_id=topic_id, archived=archived, offset=offset, limit=limit, summary=True
        )

    def canvas(self, canvas_id):
        return self.store.read("canvas", canvas_id)

    def canvas_context(self, canvas_id, *, expected_version=None):
        """One bounded lookup for placed records; the material drawer is a separate page."""
        with self.store.ledger.connect() as connection:
            canvas = self.canvas(canvas_id)
            if expected_version is not None and canvas["version"] != expected_version:
                raise ServiceError(409, "画布已有新版本，请重新读取后加载卡片。")
            topic = self.store.read("topic", canvas["topic_id"])
            ids = {
                node["record_id"]
                for node in canvas["document"]["nodes"]
                if node["record_kind"] == "record"
            }
            rows = connection.execute(
                "SELECT id,json_set(payload,'$.text',substr(json_extract(payload,'$.text'),1,600),"
                "'$.limitations',substr(json_extract(payload,'$.limitations'),1,2000),"
                "'$.text_truncated',length(json_extract(payload,'$.text'))>600,'$.preview_only',json('true')) "
                "FROM study_items WHERE workspace_id=? AND topic_id=? AND kind='record' "
                "AND id IN (SELECT value FROM json_each(?))",
                (self.store.workspace_id, topic["id"], json.dumps(sorted(ids))),
            )
            records = {row[0]: json.loads(row[1]) for row in rows}
            if ids - records.keys():
                raise ServiceError(409, "画布引用的研究记录已不可用，请先恢复相关记录。")
            refs = {
                node["record_id"]
                for node in canvas["document"]["nodes"]
                if node["record_kind"] == "source"
            }
            refs.update(ref for record in records.values() for ref in record.get("source_refs", []))
            refs.update(
                ref for edge in canvas["document"]["edges"] for ref in edge.get("source_refs", [])
            )
            current = self._current_cards() if refs else {}
            sources = {item["id"]: item for item in topic.get("materials", [])}
            if refs - sources.keys():
                raise ServiceError(409, "画布引用的来源选材已不可用，请重新读取。")
            return {
                "canvas_id": canvas_id,
                "canvas_version": canvas["version"],
                "topic_id": topic["id"],
                "records": [records[key] for key in sorted(records)],
                "materials": [self._source_summary(sources[key], current) for key in sorted(refs)],
            }

    def update_canvas(self, canvas_id, request):
        body = _request(CanvasUpdate, request)
        changes = body.model_dump(exclude_unset=True, exclude={"expected_version"})
        if "archived" in changes and changes["archived"] is None:
            raise ServiceError(400, "归档状态不能为null。")
        if "title" in changes and (changes["title"] is None or not changes["title"].strip()):
            raise ServiceError(400, "请填写画布名称。")
        with self.store.transaction():
            previous = self.store.read("canvas", canvas_id)
            topic = self.store.read("topic", previous["topic_id"])
            if "document" in changes:
                document = _request(CanvasDocument, changes["document"])
                self._validate_document(topic, document)
                changes["document"] = document.model_dump()
            return self.store.save(
                "canvas", canvas_id, changes, expected_version=body.expected_version
            )

    def canvas_revisions(self, canvas_id, *, offset=0, limit=50):
        return [
            {key: item[key] for key in ("version", "title", "updated_at", "archived")}
            for item in self.store.history(
                "canvas", canvas_id, offset=offset, limit=limit, summary=True
            )
        ]

    def export_canvas(self, canvas_id, *, format="json"):
        from hrs.studies.exporting import export_canvas

        return export_canvas(self, canvas_id, format=format)

    def import_canvas(self, topic_id, request):
        from hrs.studies.exporting import import_canvas

        try:
            return import_canvas(self, topic_id, request)
        except (ValidationError, TypeError, KeyError, AttributeError) as error:
            raise ServiceError(400, "画布备份格式无效，未导入任何内容。") from error

    def _personal_timeline(self, topic_id, window, material_id):
        where = "workspace_id=? AND topic_id=? AND kind='record' AND archived=0 AND json_extract(payload,'$.kind')='clue'"
        args = [self.store.workspace_id, topic_id]
        if material_id:
            where += " AND EXISTS(SELECT 1 FROM json_each(payload,'$.source_refs') ref WHERE ref.value=?)"
            args.append(material_id)
        with self.store.ledger.connect() as connection:
            rows = connection.execute(
                "SELECT id,json_extract(payload,'$.title'),substr(json_extract(payload,'$.text'),1,600),"
                "json_extract(payload,'$.date'),json_extract(payload,'$.event_status'),"
                "json_extract(payload,'$.origin'),json_extract(payload,'$.source_refs'),"
                "length(json_extract(payload,'$.text'))>600 FROM study_items WHERE " + where,
                args,
            )
            items = []
            for row in rows:
                value = (
                    json.loads(row[3])
                    if row[3]
                    else {"text": "", "normalized": None, "certainty": "unknown"}
                )
                position = date_position(value)
                if window.includes(position):
                    items.append(
                        {
                            "id": row[0],
                            "personal_record_id": row[0],
                            "item_kind": "personal",
                            "origin": row[5] or "personal",
                            "name": row[1],
                            "description": row[2] or "",
                            "date": value,
                            "date_basis": "event",
                            "event_status": row[4] or "unknown",
                            "source_refs": json.loads(row[6]) if row[6] else [],
                            "date_start": position.start,
                            "date_end": position.end,
                            "date_precision": position.precision,
                            "undated": position.undated,
                            "preview_only": True,
                            "description_truncated": bool(row[7]),
                        }
                    )
        return items

    def timeline(
        self,
        topic_id,
        *,
        date_basis="event",
        material_id=None,
        date_from=None,
        date_to=None,
        include_undated=True,
        offset=0,
        limit=100,
    ):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ServiceError(400, "时间线分页范围无效。")
        topic = self.store.read("topic", topic_id)
        try:
            window = DateWindow(date_from, date_to, include_undated=include_undated)
        except ValueError as error:
            raise ServiceError(400, str(error)) from error
        query = self.research.workspace._network_query()
        materials = topic.get("materials", [])
        if material_id:
            materials = [item for item in materials if item["id"] == material_id]
            if not materials:
                raise ServiceError(404, "所选来源不属于当前专题。")
        whole_books = set(topic.get("book_ids", [])) if not material_id else set()
        pinned = {
            (item["card_id"], item["card_version"], item["publication_sha256"])
            for item in materials
        }
        query = NetworkQuery(
            query.ledger,
            current_cards=[
                card
                for card in query.current_cards
                if card["book_id"] in whole_books
                or (card["id"], card["version"], card["publication_sha256"]) in pinned
            ],
        )
        book_ids = sorted(whole_books | {item["book_id"] for item in materials})
        evidence_scope = {}
        for item in materials:
            if item["book_id"] not in whole_books:
                evidence_scope.setdefault((item["card_id"], item["card_version"]), set()).update(
                    item["evidence_ids"]
                )
        personal = (
            self._personal_timeline(topic_id, window, material_id) if date_basis == "event" else []
        )
        # Only either stream's first K items can appear in their combined first K.
        # Keep native whole-stream counts without loading every event's participant preview.
        needed = offset + limit
        source_items = []
        cursor = 0
        while True:
            result = query.timeline(
                book_ids=book_ids,
                evidence_scope=evidence_scope,
                date_basis=date_basis,
                date_from=date_from,
                date_to=date_to,
                include_undated=include_undated,
                offset=cursor,
                limit=min(200, needed - cursor),
            )
            source_items.extend(result["items"])
            cursor += min(200, needed - cursor)
            if cursor >= min(needed, result["total"]):
                break

        def order(item):
            return date_position(item.get("date")).lower or (10000, 1, 1), item["id"]

        personal.sort(key=order)
        items = sorted([*source_items, *personal[:needed]], key=order)
        total = result["total"] + len(personal)
        undated = result["undated_total"] + sum(item["undated"] for item in personal)
        return {
            "items": items[offset : offset + limit],
            "total": total,
            "dated_total": total - undated,
            "undated_total": undated,
            "offset": offset,
            "limit": limit,
            "date_basis": date_basis,
            "truncated": offset + limit < total,
        }
