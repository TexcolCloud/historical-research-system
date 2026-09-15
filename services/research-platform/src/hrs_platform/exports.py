"""Reproducible, source-bound Markdown exports stored in S3."""

import json

from fastapi import HTTPException
from sqlalchemy import select

from . import schema as db
from .cards import Cards, quote_pages
from .library import Library
from .outputs import Outputs


class Exports:
    def __init__(self, settings, engine):
        self.engine = engine
        self.library = Library(settings, engine)
        self.cards = Cards(settings, engine)
        self.outputs = Outputs(settings, engine)

    def book(self, book_id):
        chapters = self.library.chapters(book_id)
        if not chapters:
            raise HTTPException(409, "本书尚未入库，暂不能导出完整正文。")
        dependencies = [chapter["content"]["sha256"] for chapter in chapters]
        run_id = chapters[0]["run_id"]
        saved = self.outputs.get(run_id, "export:book-reading-v1", dependencies)
        if saved is None:
            content = "".join(self.library.chapter(row["id"])["reading_text"] for row in chapters)
            saved = self.outputs.objects.put_bytes(content.encode("utf-8"), "text/markdown; charset=utf-8", run_id=run_id)
            self.outputs.put(run_id, "export:book-reading-v1", saved, dependencies)
        with self.engine.connect() as connection:
            title = connection.scalar(select(db.books.c.title).where(db.books.c.id == str(book_id)))
        return title, saved

    def card(self, card_id):
        card = self.cards.get(card_id)
        dependencies = {"content": card["content"]["sha256"], "checks": card["checks"]["sha256"]}
        key = f"export:card-v2:{card_id}"
        saved = self.outputs.get(card["run_id"], key, dependencies)
        if saved is None:
            draft = card["candidate"]
            parts = [
                "# " + draft["title"],
                "",
                "机器核验通过" if card["state"] == "adopted" else "候选卡：机器核验尚未通过",
                "",
                "文献类型：" + draft["document_type"],
                "来源层次：" + draft["source_layer"],
                "",
            ]
            units = {unit["unit_id"]: unit for unit in card["units"]}
            for item in draft["items"]:
                parts.extend(["## " + item["title"], "", item["text"], ""])
                for selection in item["selections"]:
                    unit = units[selection["unit_id"]]
                    parts.extend(
                        [
                            "\n".join("> " + line for line in selection["quote"].splitlines()),
                            "",
                            f"出处：{unit['title']}，PDF 物理页 {'、'.join(map(str, quote_pages(unit, selection)))}；来源单元 {unit['unit_id']}。",
                            "",
                        ]
                    )
                for field, label in [
                    ("attribution", "陈述归属"),
                    ("interpretation", "释读"),
                    ("context", "上下文"),
                ]:
                    if item.get(field):
                        parts.extend([label + "：" + item[field], ""])
                for field, label in [
                    ("limitations", "限制"),
                    ("alternatives", "其他解释"),
                    ("questions", "待查"),
                ]:
                    parts.extend(label + "：" + value for value in item.get(field, []))
            # Preserve the complete portable contract alongside its reading view:
            # dates, entities, evidence relations and source offsets must not vanish.
            parts.extend(
                [
                    "",
                    "## 完整结构化记录",
                    "",
                    "以下记录保留日期依据、实体、论证关系、原文范围与机器核验结果。",
                    "",
                    "```json",
                    json.dumps(
                        {
                            "schema_version": 1,
                            "book_id": card["book_id"],
                            "card_id": card["id"],
                            "state": card["state"],
                            "candidate": draft,
                            "units": card["units"],
                            "machine_checks": card["verdict"],
                        },
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    ),
                    "```",
                    "",
                ]
            )
            saved = self.outputs.objects.put_bytes(
                "\n".join(parts).encode("utf-8"), "text/markdown; charset=utf-8",
                run_id=card["run_id"],
            )
            self.outputs.put(card["run_id"], key, saved, dependencies)
        return card["title"], saved
