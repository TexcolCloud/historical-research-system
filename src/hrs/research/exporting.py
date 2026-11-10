"""Frozen card Markdown layouts behind the application export interface."""

from urllib.parse import urlencode

from hrs.research.dossier_export import export_dossier
from hrs.research.evidence_export import evidence_markdown, evidence_url


def export_card(card, snapshot, articles, *, base_url=""):
    if card.get("dossier"):
        return export_dossier(card, articles, base_url=base_url)
    return _LegacyCardMarkdown(card, snapshot, articles, base_url).render()


class _LegacyCardMarkdown:
    """Retain v1-v6 layout, empty-field and reference conventions byte-for-byte."""

    def __init__(self, card, snapshot, articles, base_url):
        self.card, self.snapshot, self.articles = card, snapshot, articles
        self.base_url = base_url
        self.lines = [f"# {card['title']}", ""]
        self.anchors = {
            span["id"]: f"research-evidence-{index}"
            for index, span in enumerate(card["evidence"], 1)
        }
        self.names = {
            item["id"]: item.get("name") or item.get("title") or item["id"]
            for item in [*card.get("items", []), *card.get("mentions", []), *card.get("events", [])]
        }

    def field(self, label, value):
        self.lines.append(
            f"- **{label}**：{(value if value is not None and value != '' else '未记录')}"
        )

    def references(self, identities):
        return (
            "、".join(
                f"[{identity}](#{self.anchors[identity.rsplit('/', 1)[-1]]})"
                if identity.rsplit("/", 1)[-1] in self.anchors
                else f"`{identity}`"
                for identity in identities
            )
            or "未列出"
        )

    def named(self, identity):
        return f"{self.names.get(identity, identity)}（`{identity}`）"

    def date(self, label, value):
        value = value or {}
        self.lines.extend([f"### {label}", ""])
        self.field("原文表述", value.get("text"))
        self.field("标准化日期", value.get("normalized"))
        self.field("确定性", value.get("certainty", "unknown"))
        self.field("日期依据", self.references(value.get("evidence_ids", [])))
        self.lines.append("")

    def _source_version(self):
        self.lines.extend(["## 卡片与来源版本", ""])
        for label, value in (
            ("卡片 ID（id）", self.card["id"]),
            ("卡片版本（version）", self.card.get("version")),
            ("原任务内 ID（local_id）", self.card.get("local_id")),
            ("卡片沿革（lineage_id）", self.card.get("lineage_id")),
            ("替代此前卡片（replaces_card_id）", self.card.get("replaces_card_id")),
            (
                "已保留修订（revision_versions）",
                "、".join(map(str, self.card.get("revision_versions", []))),
            ),
            ("研究任务（run_id）", self.card["run_id"]),
            (
                "原文任务（source_run_id）",
                self.card.get("source_run_id", self.snapshot.get("source_run_id")),
            ),
            ("书籍（book_id）", self.card.get("book_id", self.snapshot.get("book_id"))),
            (
                "书名",
                self.card.get("book_title")
                or self.snapshot.get("book_title")
                or self.snapshot.get("title"),
            ),
            ("文章（article_id）", self.card.get("article_id")),
            ("原文篇名", self.card.get("article_title")),
            ("原生文档（document_id）", self.snapshot.get("document_id")),
            ("工作区（workspace_id）", self.snapshot.get("workspace_id")),
            (
                "发布版本（publication_sha256）",
                self.card.get("publication_sha256", self.snapshot.get("publication_sha256")),
            ),
            ("章节版本（chapters_sha256）", self.snapshot.get("chapters_sha256")),
            ("文章版本（article_sha256）", self.card.get("article_sha256")),
            ("原件 SHA256", (self.snapshot.get("source") or {}).get("sha256")),
            ("状态", self.card.get("state", "draft")),
            ("认证方式", self.card.get("approval_type", "none")),
            ("来源状态", self.card.get("source_status", "current")),
            ("是否需要更新", "是" if self.card.get("needs_update") else "否"),
            ("研究修订是否已替代", "是" if self.card.get("superseded") else "否"),
            ("创建时间（Unix 秒）", self.card.get("created_at")),
        ):
            self.field(label, value)
        self.lines.append("")
        if not self.card.get("source_current", True):
            self.lines.extend(
                ["历史卡片：来源已换版，需要重新整理；以下保留原版本哈希供追溯。", ""]
            )
        elif self.card.get("needs_update"):
            self.lines.extend(["来源正在修订；此卡片对应此前已发布版本。", ""])
        if self.card.get("superseded"):
            self.lines.extend(["此研究修订已有后续版本；以下保留原卡片内容。", ""])

    def _metadata(self):
        self.lines.extend(["## 史料属性与年代", ""])
        self.field("文献类型（document_type）", self.card.get("document_type"))
        self.field("史料层次（source_layer）", self.card.get("source_layer"))
        self.field("标签（tags）", "、".join(self.card.get("tags", [])))
        self.lines.append("")
        self.date("成文时间（formation_date）", self.card.get("formation_date"))
        self.date("事件时间（event_date）", self.card.get("event_date"))
        metadata = self.card.get("metadata") or {}
        if metadata:
            self.lines.extend(["## 文献身份与形成信息（metadata）", ""])
            labels = {
                "original_title": "原题",
                "authors": "作者",
                "recipients": "收受者",
                "editors": "编者",
                "publication": "出版与版本",
                "volume": "卷次",
                "archive_id": "档号",
                "formation_place": "成文地点",
                "purpose": "写作目的",
                "testimony": "记载性质",
            }
            self.field("标题性质（title_origin）", metadata.get("title_origin"))
            for key, value in metadata.items():
                if key == "title_origin":
                    continue
                self.lines.extend([f"### {labels.get(key, key)}（{key}）", ""])
                self.field("原文记载", value.get("value") or "未详")
                self.field("认识状态", value.get("certainty"))
                self.field("原文依据", self.references(value.get("evidence_ids", [])))
                self.field("说明与局限", value.get("note"))
                self.lines.append("")

    def _items(self):
        self.lines.extend(["## 提要", "", self.card["summary"], "", "## 卡片内容", ""])
        kinds = {
            "section": "原文结构",
            "evidence": "史料要点",
            "argument": "论点与论证",
            "issue": "研究问题",
            "paper_use": "论文用途",
        }
        for item in self.card.get("items", []):
            self.lines.extend(
                [
                    f"### {item['title'] or kinds.get(item['kind'], item['kind'])}",
                    "",
                    item["text"],
                    "",
                ]
            )
            self.field("条目 ID", item["id"])
            self.field("类型", f"{kinds.get(item['kind'], item['kind'])}（{item['kind']}）")
            self.field("认识状态", item.get("epistemic_state"))
            self.field("归属 / 发言者", item.get("attribution"))
            self.field("依据", self.references(item.get("evidence_ids", [])))
            self.field("条目使用边界", "；".join(item.get("limitations", [])))
            self.lines.append("")

    def _mentions(self):
        self.lines.extend(["## 人物、机构、地点与实体记载（mentions）", ""])
        for mention in self.card.get("mentions", []):
            self.lines.extend([f"### {mention['name']}", ""])
            self.field("本卡实体", self.named(mention["id"]))
            self.field("实体类型", mention["kind"])
            self.field("实体 ID", mention.get("entity_id"))
            self.field("身份依据", mention["identity_basis"])
            self.field("原文依据", self.references(mention["evidence_ids"]))
            for alias in mention.get("aliases", []):
                self.field("别名", alias["name"])
                self.field("别名依据", self.references(alias.get("evidence_ids", [])))
            for location in mention.get("locations", []):
                self.field(
                    "原文提及位置",
                    f"{location['article_id']} {location['start']}–{location['end']}",
                )
                self.field("原文写法", location.get("quote"))
            self.lines.append("")

    def _events(self):
        self.lines.extend(["## 事件（events）", ""])
        for event in self.card.get("events", []):
            self.lines.extend([f"### {event['name']}", "", event["description"], ""])
            self.field("事件 ID", event["id"])
            self.field("实体 ID", event.get("entity_id"))
            self.field(
                "参与者", "、".join(self.named(identity) for identity in event["participant_ids"])
            )
            self.field("原文依据", self.references(event["evidence_ids"]))
            self.field("具体行为（action）", event.get("action"))
            self.field("材料中的事件性质（event_status）", event.get("event_status", "unknown"))
            self.field("事件性质说明（status_note）", event.get("status_note"))
            place = event.get("place") or {}
            self.field("事件地点", place.get("value") or "未详")
            self.field("地点认识状态", place.get("certainty"))
            self.field("地点依据", self.references(place.get("evidence_ids", [])))
            self.field("地点说明", place.get("note"))
            for role in event.get("participant_roles", []):
                self.field("参与者角色", f"{self.named(role['mention_id'])}：{role['role']}")
                self.field("角色记载", role["description"])
                self.field("角色依据", self.references(role["evidence_ids"]))
            self.lines.append("")
            self.date(f"{event['name']}的日期", event.get("date"))

    def _relations(self):
        self.lines.extend(["## 关系网络（relations）", ""])
        for relation in self.card.get("relations", []):
            self.lines.extend(
                [
                    f"### {self.named(relation['subject_id'])} → {relation['predicate']} → {self.named(relation['object_id'])}",
                    "",
                ]
            )
            self.field("关系 ID", relation["id"])
            self.field("关系作用", relation["role"])
            self.field("关联理由", relation["reason"])
            self.field("原文依据", self.references(relation["evidence_ids"]))
            self.lines.append("")
        self.lines.extend(["## 跨卡身份关联（identity_links）", ""])
        for link in self.card.get("identity_links", []):
            self.field("本卡实体", self.named(link["local_mention_id"]))
            self.field("目标实体", link["entity_id"])
            self.field(
                "身份关系",
                "可能同一身份（possible_same_as）"
                if link["relation"] == "possible_same_as"
                else "同一身份（same_as）",
            )
            self.field("关联理由", link["reason"])
            self.field("原文依据", self.references(link["evidence_ids"]))
            self.lines.append("")
        if self.card.get("cross_relations"):
            self.lines.extend(["## 跨卡文献与证据关系（cross_relations）", ""])
            for relation in self.card["cross_relations"]:
                self.lines.extend([f"### {relation['kind']} · {relation['id']}", ""])
                for key in (
                    "target_card_id",
                    "target_card_version",
                    "target_publication_sha256",
                    "certainty",
                    "reason",
                ):
                    self.field(key, relation.get(key))
                self.field("本卡来源版本", self.card.get("publication_sha256"))
                self.field("本篇原文依据", self.references(relation["source_evidence_ids"]))
                self.field("对方原文依据", self.references(relation["target_evidence_ids"]))
                self.lines.extend(
                    [
                        f"[查看对方卡片]({self.base_url}/#/research?{urlencode({'card': relation['target_card_id']})})",
                        "",
                    ]
                )
        derived = self.card.get("derived_relations") or {}
        if derived.get("items"):
            self.lines.extend(["## 后续补充的双向关系（derived_relations）", ""])
            for relation in derived["items"]:
                self.lines.extend(
                    [f"### {relation.get('kind', 'association')} · {relation['id']}", ""]
                )
                for key in (
                    "card_id",
                    "card_version",
                    "source_id",
                    "target_id",
                    "target_card_version",
                    "target_publication_sha256",
                    "certainty",
                    "reason",
                    "needs_update",
                ):
                    self.field(key, relation.get(key))
                for span in relation.get("evidence", []):
                    self.lines.extend(
                        [
                            "",
                            f"**冻结证据 {span['id']}** · {span['article_id']} {span['start']}–{span['end']}",
                            "",
                            span["quote"],
                            "",
                        ]
                    )
                    self.field("来源发布版本", span.get("publication_sha256"))
                    self.field("原文 SHA256", span.get("text_sha256"))
                    for note in span.get("footnotes", []):
                        self.field("源脚注 " + note["id"], note.get("text"))
                self.lines.extend(
                    [
                        f"[查看独立证据包]({self.base_url}/#/research?{urlencode({'card': relation['card_id']})})",
                        "",
                    ]
                )
            if derived.get("truncated"):
                self.lines.extend(["补充关系有省略，请在工作台继续查阅。", ""])

    def _coverage_and_questions(self):
        if self.card.get("coverage"):
            self.lines.extend(["## 全文提取覆盖（coverage）", ""])
            for unit in self.card["coverage"]:
                self.lines.extend(
                    [
                        f"### {unit['id']} · {unit['article_id']} {unit['start']}–{unit['end']}",
                        "",
                        unit["summary"],
                        "",
                    ]
                )
                for key in ("mention_ids", "event_ids", "relation_ids"):
                    self.field(key, "、".join(unit.get(key, [])))
                for key in ("no_mentions_reason", "no_events_reason", "no_relations_reason"):
                    self.field(key, unit.get(key))
                self.lines.append("")
        if self.card.get("research_plan"):
            self.lines.extend(["## 补证计划与证据评价（research_plan）", ""])
            for plan in self.card["research_plan"]:
                self.lines.extend([f"### {plan['question']}", ""])
                for key in ("id", "status", "evaluation", "stop_reason"):
                    self.field(key, plan.get(key))
                for key in ("proposed_queries", "search_receipt_ids", "missing_evidence"):
                    self.field(key, "；".join(plan.get(key, [])))
                self.field("实际采用依据", self.references(plan.get("evidence_ids", [])))
                self.lines.append("")
        if self.card.get("question_findings"):
            self.lines.extend(["## 研究问题结论（question_findings）", ""])
            for finding in self.card["question_findings"]:
                for key in ("id", "question_id", "definition_version", "status"):
                    self.field(key, finding.get(key))
                self.lines.extend(["", finding["text"], ""])
                self.field("原文依据", self.references(finding.get("evidence_ids", [])))
                self.field("未解决事项", "；".join(finding.get("unresolved", [])))
                self.lines.append("")
        for heading, key in (
            ("使用边界（limitations）", "limitations"),
            ("备选解释（alternatives）", "alternatives"),
            ("待核问题（questions）", "questions"),
        ):
            self.lines.extend(
                [f"## {heading}", "", *(f"- {value}" for value in self.card.get(key, [])), ""]
            )
        self.field("未提出论点的理由（no_argument_reason）", self.card.get("no_argument_reason"))

    def _verification(self):
        self.lines.extend(["", "## 核验与人工决定", ""])
        checks = self.card.get("checks") or {}
        self.field("独立核验结论", checks.get("decision"))
        self.field("核验输入哈希", checks.get("input_hash"))
        for key, label in (
            ("checked_card_ids", "核验卡片"),
            ("checked_item_ids", "核验条目"),
            ("checked_evidence_ids", "核验引文"),
            ("checked_relation_ids", "核验关系"),
            ("checked_identity_links", "核验身份关联"),
            ("checked_search_receipt_ids", "核验检索回执"),
            ("checked_mechanical_evidence_ids", "核验字符回执"),
            ("checked_metadata_fields", "核验文献信息"),
            ("checked_entity_ids", "核验人物实体"),
            ("checked_event_ids", "核验事件"),
            ("checked_coverage_ids", "核验全文覆盖"),
            ("checked_research_question_ids", "核验补证计划"),
            ("checked_cross_relation_ids", "核验跨卡关系"),
            ("checked_question_finding_ids", "核验研究问题结论"),
        ):
            if self.card.get("card_only") and key in {
                "checked_search_receipt_ids",
                "checked_research_question_ids",
                "checked_cross_relation_ids",
                "checked_question_finding_ids",
            }:
                continue
            self.field(label, "、".join(checks.get(key, [])))
        if not self.card.get("card_only"):
            self.field("补充研究判定已核验", checks.get("checked_research_disposition"))
        self.field("未解决事项", "；".join(checks.get("issues", [])))
        decision = self.card.get("human_decision") or {}
        self.field("人工决定", decision.get("action"))
        self.field("人工决定所据此前版本", decision.get("previous_version"))
        self.field("人工决定时间（Unix 秒）", decision.get("at"))
        disposition = self.card.get("research_disposition") or {}
        if disposition:
            self.lines.extend(["", "### 补充研究判定", ""])
            for key in ("mode", "category", "reason"):
                self.field(key, disposition.get(key))
            self.field("判定依据", self.references(disposition.get("evidence_ids", [])))
        if self.card.get("human_notes"):
            self.lines.extend(
                ["", "## 个人备注（用户记录，非原文）", "", self.card["human_notes"], ""]
            )
            self.field("备注版本", self.card["notes_version"])
        self.lines.append("")

    def _evidence(self):
        self.lines.extend(["## 原文证据", ""])
        for index, span in enumerate(self.card["evidence"], 1):
            target = span.get("reading_target") or {}
            pages = ",".join(map(str, span.get("pages", [])))
            href = evidence_url(
                span,
                self.card,
                source_run_id=target.get(
                    "run_id", span.get("source_run_id", self.card["source_run_id"])
                ),
                base_url=self.base_url,
            )
            self.lines.extend(
                [
                    f'<a id="research-evidence-{index}"></a>',
                    "",
                    f"### {span['id']} · 物理页 {pages}",
                    "",
                    evidence_markdown(span, self.articles[span["article_id"]], f"research-{index}"),
                    "",
                    f"文章：{span['article_id']}；范围：{span['start']}–{span['end']}；原文 SHA256：{span['text_sha256']}",
                    "",
                    f"原图 SHA256：{target.get('image_sha256', '')}",
                    "",
                    f"来源书籍：{span.get('book_id', self.card['book_id'])}；原文任务（source_run_id）：{span.get('source_run_id', target.get('run_id', self.card.get('source_run_id')))}；原文章 ID：{span.get('original_article_id', target.get('chapter_id', span['article_id']))}",
                    "",
                    f"来源发布版本（publication_sha256）：{span.get('publication_sha256', self.card.get('publication_sha256', self.snapshot.get('publication_sha256', '未记录')))}",
                    "",
                    "源脚注 ID：" + "、".join(span.get("footnote_ids", [])),
                    "",
                    f"[阅读原文]({href})",
                    "",
                ]
            )

    def render(self):
        self._source_version()
        self._metadata()
        self._items()
        self._mentions()
        self._events()
        self._relations()
        self._coverage_and_questions()
        self._verification()
        self._evidence()
        return "\n".join(self.lines)
