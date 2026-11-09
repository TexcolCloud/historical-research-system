"""Readable document dossiers with exact, version-bound source evidence."""

import json
from urllib.parse import urlencode


def export_dossier(card, articles, *, base_url, render_evidence):
    dossier = card["dossier"]
    citation = dossier["citation"]
    evidence = card["evidence"]
    anchors = {span["id"]: f"research-evidence-{index}" for index, span in enumerate(evidence, 1)}
    names = {
        item["id"]: item.get("name") or item.get("title") or item["id"]
        for item in [*card.get("items", []), *card.get("mentions", []), *card.get("events", [])]
    }
    lines = [f"# {card['title']}", ""]

    def refs(ids):
        return "、".join(
            f"[{identity}](#{anchors[identity]})" for identity in ids if identity in anchors
        )

    def field(label, value):
        if value:
            lines.extend([f"- **{label}**：{value}", ""])

    def section(title):
        lines.extend([f"## {title}", ""])

    def value(label, item):
        item = item or {}
        lines.extend([f"### {label}", "", item.get("value") or "未详", ""])
        field("说明", item.get("note"))
        field("依据", refs(item.get("evidence_ids", [])))
        if item.get("certainty") in {"inferred", "disputed"}:
            field("认识状态", "据来源整理" if item["certainty"] == "inferred" else "有争议")

    states = {
        "verified": "模型核验通过",
        "pending": "待人工核对",
        "human_accepted": "用户已确认",
        "excluded": "已排除",
    }
    field("核验状态", states.get(card.get("state"), card.get("state")))
    field("卡片标识", card.get("display_id") or card["id"])
    if not card.get("source_current", True):
        lines.extend(["历史卡片：来源已换版，需要重新整理；以下保留原版本哈希供追溯。", ""])
    elif card.get("needs_update"):
        lines.extend(["来源正在修订；此卡片对应此前已发布版本。", ""])
    if card.get("superseded") or card.get("research_current") is False:
        lines.extend(["此研究修订已有后续版本；以下保留原卡片内容。", ""])
    section("一、基本信息与完整出处")
    metadata = card.get("metadata") or {}
    for key, label in (
        ("authors", "形成者"),
        ("recipients", "收受者"),
        ("editors", "编者"),
        ("volume", "卷次"),
        ("archive_id", "档号"),
    ):
        field(label, (metadata.get(key) or {}).get("value"))
    field("文献类型", card.get("document_type"))
    field("形成时间", (card.get("formation_date") or {}).get("text") or "未详")
    field("事件时间", (card.get("event_date") or {}).get("text") or "未详")
    field("出版时间", citation["publication_date"].get("text") or "未详")
    field(
        "已知出处" if citation["completeness"] != "complete" else "完整出处",
        citation["full"].get("value") or "未详",
    )
    field("出处说明", citation["full"].get("note"))
    field("尚缺书目信息", "、".join(citation["missing_fields"]))
    field("原书页", citation["printed_pages"].get("value") or "未详")
    field("PDF物理页", "、".join(map(str, card.get("pages", []))))

    section("二、研究对象与时空边界")
    value("研究对象与直接语境", dossier["research_scope"])
    section("三、形成背景与文本结构")
    value("形成背景", dossier["formation_background"])
    value("文本结构", dossier["text_structure"])
    section("四、核心内容")
    lines.extend([card["summary"], ""])
    for item in card.get("items", []):
        lines.extend([f"### {item['title'] or '内容要点'}", "", item["text"], ""])
        field(
            "内容性质",
            {
                "section": "内容要点",
                "evidence": "引文与事实",
                "argument": "原作者观点",
                "issue": "疑义",
            }.get(item["kind"], item["kind"]),
        )
        if item.get("epistemic_state", "stated") != "stated":
            field(
                "认识状态",
                {"inferred": "推断", "unknown": "未明", "disputed": "有争议"}.get(
                    item["epistemic_state"], item["epistemic_state"]
                ),
            )
        field("表述归属", item.get("attribution"))
        field("依据", refs(item.get("evidence_ids", [])))
        for limit in item.get("limitations", []):
            field("适用限制", limit)
    section("五、史料性质、来源角色与局限")
    value("史料性质", dossier["source_nature"])
    value("来源角色", dossier["source_role"])
    field("来源层次", card.get("source_layer"))
    for limit in card.get("limitations", []):
        field("使用限制", limit)
    section("六、史料价值")
    value("基于本件的阅读说明", dossier["research_value"])
    section("七、证据边界")
    for key, label in (
        ("can_support", "可以支持"),
        ("cannot_support", "不能直接证明"),
        ("needs_corroboration", "仍需互证"),
    ):
        value(label, dossier[key])
    section("八、可用论证单元")
    for unit in dossier["argument_units"]:
        lines.extend([f"### {unit['title']}", "", unit["claim"], ""])
        field("直接依据", refs(unit["evidence_ids"]))
        field("适用边界", unit["applicability_limit"])
        field("仍需互证", unit["corroboration_needed"])
    if not dossier["argument_units"]:
        lines.extend([dossier["argument_units_note"], ""])
    section("九、规范引注")
    value(
        "完整形式" if citation["completeness"] == "complete" else "已知出处（尚未齐备）",
        citation["full"],
    )
    value("简称形式", citation["short"])
    section("十、关联人物、事件与史料")
    for mention in card.get("mentions", []):
        lines.extend([f"### {mention['name']}", ""])
        field(
            "类型",
            {
                "person": "人物",
                "organization": "机构",
                "place": "地点",
                "document": "文献",
                "event": "事件",
            }.get(mention["kind"], mention["kind"]),
        )
        field("身份依据", mention.get("identity_basis"))
        field("依据", refs(mention.get("evidence_ids", [])))
        for alias in mention.get("aliases", []):
            field("原文别名", alias["name"] + " " + refs(alias.get("evidence_ids", [])))
    for event in card.get("events", []):
        lines.extend([f"### {event['name']}", "", event["description"], ""])
        field("行为", event.get("action"))
        field("材料中的事件性质", event.get("status_note") or event.get("event_status"))
        field("时间", (event.get("date") or {}).get("text") or "未详")
        field("地点", (event.get("place") or {}).get("value") or "未详")
        field("依据", refs(event.get("evidence_ids", [])))
        for role in event.get("participant_roles", []):
            field(
                "参与角色",
                f"{names.get(role['mention_id'], role['mention_id'])}：{role['role']}；{role['description']} "
                + refs(role.get("evidence_ids", [])),
            )
    for relation in card.get("relations", []):
        field(
            "本卡关联",
            f"{names.get(relation['subject_id'], relation['subject_id'])} → {relation['predicate']} → {names.get(relation['object_id'], relation['object_id'])}；{relation['reason']} "
            + refs(relation.get("evidence_ids", [])),
        )
    for relation in card.get("cross_relations", []):
        field(
            "关联史料",
            f"{relation['kind']}：{relation['reason']}（对方卡片 {relation['target_card_id']}，版本 {relation['target_card_version']}）",
        )
        field("本件依据", refs(relation["source_evidence_ids"]))
        field("对方依据", refs(relation["target_evidence_ids"]))
    section("十一、证据与页码映射")
    for index, span in enumerate(evidence, 1):
        target = span.get("reading_target") or {}
        query = urlencode(
            {
                "tab": "reader",
                "section": "text",
                "chapter": target.get("chapter_id", span["article_id"]),
                "physicalPage": target.get("physical_page", ""),
                "start": target.get("start", span["start"]),
                "end": target.get("end", span["end"]),
                "textSha": span["text_sha256"],
                "run": span.get("source_run_id", card["source_run_id"]),
            }
        )
        lines.extend(
            [
                f'<a id="{anchors[span["id"]]}"></a>',
                "",
                f"### 引文 {index} · PDF物理页 {','.join(map(str, span.get('pages', [])))}",
                "",
                render_evidence(span, articles[span["article_id"]], f"research-{index}"),
                "",
                f"[对照原文]({base_url}/#/books/{span.get('book_id', card['book_id'])}?{query})",
                "",
            ]
        )
    if card.get("human_notes"):
        section("我的阅读笔记（用户记录，非原文）")
        lines.extend([card["human_notes"], ""])
        field("备注版本", card.get("notes_version"))
    lines.extend(["<details>", "<summary>核验与版本记录</summary>", ""])
    for label, key in (
        ("卡片完整标识", "id"),
        ("卡片版本", "version"),
        ("原文发布版本", "publication_sha256"),
        ("原文章哈希", "article_sha256"),
        ("原文任务", "source_run_id"),
    ):
        field(label, card.get(key))
    for issue in (card.get("checks") or {}).get("issues", []):
        field("保留待办", issue)
    field("结构化记录", "下列数据保留来源、脚注、完整覆盖、关系和独立核验结果；不构成人工审批。")
    audit = {
        key: value
        for key, value in card.items()
        if key not in {"human_notes", "notes_version", "notes_updated_at"}
    }
    lines.extend(
        ["```json", json.dumps(audit, ensure_ascii=False, indent=2), "```", "", "</details>", ""]
    )
    return "\n".join(lines)
