"""Canvas portability retains source-owned quotations instead of accepting imported claims."""

import hashlib
import json
from copy import deepcopy
from uuid import uuid4

from hrs.library.errors import ServiceError
from hrs.library.footnotes import export_footnotes
from hrs.research.evidence_export import evidence_markdown
from hrs.studies.schemas import CanvasCreate, CanvasImport, RecordCreate


def canvas_bundle(service, canvas_id):
    with service.store.ledger.connect() as connection:
        canvas = service.canvas(canvas_id)
        topic = service.store.read("topic", canvas["topic_id"])
        wanted = {
            node["record_id"]
            for node in canvas["document"]["nodes"]
            if node["record_kind"] == "record"
        }
        rows = connection.execute(
            "SELECT id,payload FROM study_items WHERE workspace_id=? AND topic_id=? AND kind='record' "
            "AND id IN (SELECT value FROM json_each(?))",
            (service.store.workspace_id, topic["id"], json.dumps(sorted(wanted))),
        )
        records = {row[0]: json.loads(row[1]) for row in rows}
        if wanted - records.keys():
            raise ServiceError(409, "画布引用的研究记录已不可用，请先恢复记录。")
        refs = {
            node["record_id"]
            for node in canvas["document"]["nodes"]
            if node["record_kind"] == "source"
        }
        refs.update(ref for record in records.values() for ref in record.get("source_refs", []))
        refs.update(
            ref for edge in canvas["document"]["edges"] for ref in edge.get("source_refs", [])
        )
        materials = service.source_materials(topic["id"], refs)
        return {
            "schema_version": 1,
            "workspace_id": service.store.workspace_id,
            "canvas": canvas,
            "topic": {**topic, "materials": materials},
            "records": [records[key] for key in sorted(records)],
        }


def _frozen_articles(service, source, cache):
    workflow = getattr(service.research, "workflow", None)
    if workflow is None:
        return {}
    card = workflow.card(source["card_id"], version=source["card_version"])
    run_id = card["run_id"]
    if run_id not in cache:
        snapshot = workflow.snapshot(run_id)
        cache[run_id] = {
            article["id"]: article for article in [*snapshot["articles"], *workflow.sources(run_id)]
        }
    return cache[run_id]


def _quote_markdown(span, articles, prefix):
    article = articles.get(span.get("article_id"))
    if article is not None:
        text = article["text"]
        if (
            hashlib.sha256(text.encode()).hexdigest() != span.get("text_sha256")
            or text[span["start"] : span["end"]] != span["quote"]
        ):
            raise ServiceError(409, "保留引文与冻结原文或文本哈希不符，未生成整理稿。")
        if article.get("parts") and all(note.get("note") for note in span.get("footnotes", [])):
            return evidence_markdown(span, article, prefix)
    elif articles:
        raise ServiceError(409, "保留引文所属的冻结文章不可用，未生成整理稿。")
    # Old pins without canonical geometry remain literal: a printed number is not ownership.
    lines = [export_footnotes(span["quote"], [], prefix)]
    if span.get("footnotes"):
        lines.extend(["", "关联原注缺少可复原的位置；以下保留原始文字，不推断编号对应关系。", ""])
        for note in span["footnotes"]:
            lines.extend(
                [
                    f"关联原注 {note.get('label', note.get('number', ''))}（{note['id']}）：",
                    export_footnotes(note.get("text", ""), [], prefix),
                    "",
                ]
            )
    return "\n".join(lines)


def _source_markdown(source, articles):
    lines = [
        "### " + source["title"],
        "",
        f"保留卡片 {source['card_id']} · 版本 {source['card_version']}",
        "",
    ]
    if not source["source_current"]:
        lines.extend(["来源已有修订或已不可用；以下为保留版本的引文。", ""])
    lines.extend(
        [
            source.get("source_citation") or "完整书目未提供。",
            "印刷页码：" + (source.get("printed_pages") or "印刷页码未提供"),
            "",
        ]
    )
    for span in source["evidence"]:
        key = hashlib.sha256(f"{source['id']}:{span['id']}".encode()).hexdigest()[:20]
        lines.extend(
            [
                "原文摘录及原注：",
                "",
                _quote_markdown(span, articles, "study-" + key),
                "",
                f"PDF物理页：{','.join(map(str, span.get('pages', [])))}；原文范围：{span['start']}–{span['end']}；文本哈希：{span.get('text_sha256', '未知')}",
                f"证据：{span['id']}；文章：{span.get('article_id', source.get('article_id', '未知'))}",
                "",
            ]
        )
    return lines


def export_canvas(service, canvas_id, *, format="json"):
    bundle = canvas_bundle(service, canvas_id)
    if format == "json":
        return json.dumps(bundle, ensure_ascii=False, indent=2)
    if format != "markdown":
        raise ServiceError(400, "请选择JSON备份或Markdown整理稿。")
    canvas = bundle["canvas"]
    records = {record["id"]: record for record in bundle["records"]}
    sources = {item["id"]: item for item in bundle["topic"]["materials"]}
    lines = ["# " + canvas["title"], "", "个人研究整理；不代替来源核验。", ""]
    for node in canvas["document"]["nodes"]:
        if node["record_kind"] == "group":
            lines.extend(["## " + (node.get("title") or "分组"), ""])
        elif node["record_kind"] == "record":
            record = records[node["record_id"]]
            lines.extend(
                [
                    "## " + record["title"],
                    "",
                    record.get("text", ""),
                    "",
                    "使用限制：" + record.get("limitations", ""),
                    "",
                ]
            )
            for ref in record.get("source_refs", []):
                source = sources[ref]
                lines.extend(
                    [
                        f"来源：{source['title']}（卡片{source['card_id']}，版本{source['card_version']}）",
                        "",
                    ]
                )
    if sources:
        lines.extend(["## 引用来源", ""])
        frozen = {}
        for source in sources.values():
            lines.extend(_source_markdown(source, _frozen_articles(service, source, frozen)))
    names = {
        node["id"]: node.get("title")
        or (
            sources[node["record_id"]]["title"]
            if node["record_kind"] == "source"
            else records[node["record_id"]]["title"]
            if node["record_kind"] == "record"
            else "分组"
        )
        for node in canvas["document"]["nodes"]
    }
    for edge in canvas["document"]["edges"]:
        lines.extend(
            [
                f"研究连线：{names[edge['source']]} → {names[edge['target']]} · {edge.get('label') or edge.get('relation', '关联')}",
                edge.get("notes", ""),
                "",
            ]
        )
    return "\n".join(lines)


def import_canvas(service, topic_id, request):
    body = CanvasImport.model_validate(
        request.model_dump() if hasattr(request, "model_dump") else request
    )
    backup = body.backup
    if len(json.dumps(backup, ensure_ascii=False).encode("utf8")) > 8_000_000:
        raise ServiceError(400, "画布备份超过8MB，请分拆后导入。")
    if (
        backup.get("schema_version") != 1
        or backup.get("workspace_id") != service.store.workspace_id
    ):
        raise ServiceError(409, "该备份不属于当前工作区或版本不兼容，请先恢复对应工作区。")
    canvas = backup.get("canvas") or {}
    parsed = CanvasCreate.model_validate(
        {"title": body.title or canvas.get("title"), "document": canvas.get("document")}
    )
    records = backup.get("records", [])
    material_refs = (backup.get("topic") or {}).get("materials", [])
    if (
        not isinstance(records, list)
        or not isinstance(material_refs, list)
        or len(records) > 2000
        or len(material_refs) > 2000
    ):
        raise ServiceError(400, "备份材料范围无效。")
    record_map = {record.get("id"): record for record in records if isinstance(record, dict)}
    material_map = {ref.get("id"): ref for ref in material_refs if isinstance(ref, dict)}
    if len(record_map) != len(records) or len(material_map) != len(material_refs):
        raise ServiceError(400, "备份含重复或无效的记录标识。")
    wanted_records = {
        node.record_id for node in parsed.document.nodes if node.record_kind == "record"
    }
    if wanted_records - record_map.keys():
        raise ServiceError(400, "备份缺少画布引用的个人记录。")
    prepared_records = {}
    for item_id in wanted_records:
        record = record_map[item_id]
        prepared_records[item_id] = RecordCreate.model_validate(
            {
                key: record[key]
                for key in (
                    "kind",
                    "title",
                    "text",
                    "limitations",
                    "source_refs",
                    "date",
                    "event_status",
                )
                if key in record
            }
        )
    wanted_sources = {
        node.record_id for node in parsed.document.nodes if node.record_kind == "source"
    }
    wanted_sources.update(ref for record in prepared_records.values() for ref in record.source_refs)
    wanted_sources.update(ref for edge in parsed.document.edges for ref in edge.source_refs)
    if wanted_sources - material_map.keys():
        raise ServiceError(400, "备份缺少保留来源引用。")
    # Restore claims only from the already validated local pin, not the file's quote fields.
    pinned = {}
    for item_id in wanted_sources:
        try:
            retained = service.store.read("material", item_id)
        except ServiceError as error:
            raise ServiceError(
                409, "备份引用的来源历史不在当前工作区，请先恢复原工作区数据。"
            ) from error
        for key in (
            "card_id",
            "card_version",
            "book_id",
            "article_id",
            "source_run_id",
            "publication_sha256",
            "article_sha256",
            "evidence_ids",
        ):
            if material_map[item_id].get(key) != retained.get(key):
                raise ServiceError(409, "备份来源身份与本地保留版本不符，未导入任何内容。")
        pinned[item_id] = retained
    current = service._current_cards() if pinned else {}
    warnings = (
        ["部分来源已有修订或已不可用，已保留旧版引文，请逐项核对。"]
        if any(
            not service._source_summary(ref, current)["source_current"] for ref in pinned.values()
        )
        else []
    )
    with service.store.transaction():
        topic = service.store.read("topic", topic_id)
        if topic["version"] != body.expected_version or topic.get("archived"):
            raise ServiceError(409, "专题已有新版本或已归档，请重新读取后导入。")
        sources = list(topic.get("materials", []))
        source_ids = {}
        for old_id, source in pinned.items():
            existing = next(
                (
                    item
                    for item in sources
                    if all(
                        item.get(key) == source.get(key)
                        for key in ("card_id", "card_version", "evidence_ids", "publication_sha256")
                    )
                ),
                None,
            )
            if existing:
                source_ids[old_id] = existing["id"]
                continue
            item_id = uuid4().hex
            payload = {
                key: deepcopy(value)
                for key, value in source.items()
                if key
                not in {"id", "topic_id", "type", "version", "created_at", "updated_at", "archived"}
            }
            payload["id"] = item_id
            service.store.create("material", payload, topic_id=topic_id, item_id=item_id)
            sources.append({key: value for key, value in payload.items() if key != "evidence"})
            source_ids[old_id] = item_id
        if len(sources) > 2000:
            raise ServiceError(400, "导入后专题选材超过2000项，请拆分专题。")
        service.store.save(
            "topic",
            topic_id,
            {
                "materials": sources,
            },
            expected_version=body.expected_version,
        )
        record_ids = {}
        for old_id, record in prepared_records.items():
            value = record.model_dump()
            value["source_refs"] = [source_ids[ref] for ref in record.source_refs]
            created = service.create_record(topic_id, value)
            record_ids[old_id] = created["id"]
        document = parsed.document.model_dump()
        for node in document["nodes"]:
            if node["record_kind"] == "source":
                node["record_id"] = source_ids[node["record_id"]]
            elif node["record_kind"] == "record":
                node["record_id"] = record_ids[node["record_id"]]
        for edge in document["edges"]:
            edge["source_refs"] = [source_ids[ref] for ref in edge["source_refs"]]
        return service.create_canvas(
            topic_id, {"title": parsed.title, "document": document}, import_warnings=warnings
        )
