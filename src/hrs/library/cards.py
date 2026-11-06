"""Read-only access to historical cards; generation has been retired."""

from sqlalchemy import select

from hrs.library.card_quotes import quote_pages, quote_range
from hrs.library.errors import ServiceError
from hrs.library.review import Review
from hrs.storage import models as db


class Cards:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.review = Review(settings, engine)

    def list(self, book_id=None, offset=0, limit=100):
        query = (
            select(db.cards)
            .where(db.cards.c.state != "superseded")
            .order_by(db.cards.c.created_at.desc(), db.cards.c.id)
            .offset(offset)
            .limit(limit)
        )
        if book_id:
            query = query.where(db.cards.c.book_id == str(book_id))
        with self.engine.connect() as connection:
            return list(connection.execute(query).mappings())

    def get(self, card_id):
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(db.cards).where(db.cards.c.id == str(card_id)))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ServiceError(404, "卡片不存在。")
        detail = {
            **row,
            **self.review.read_json(row["content"]),
            "verdict": self.review.read_json(row["checks"]),
        }
        units = {unit["unit_id"]: unit for unit in detail["units"]}
        locations = []
        for item in detail["candidate"]["items"]:
            # Older candidates remain exportable without inventing item identities.
            if not item.get("item_id"):
                continue
            for index, selection in enumerate(item.get("selections", [])):
                start, end, pages, issue = None, None, [], None
                unit = units.get(selection["unit_id"])
                if unit is None:
                    issue = "未找到此引文的来源正文。"
                else:
                    try:
                        start, end = quote_range(unit, selection)
                    except ValueError:
                        issue = "引文未能在保存的来源正文中精确定位。"
                    else:
                        try:
                            pages = quote_pages(unit, selection)
                        except (ValueError, KeyError):
                            issue = "引文已定位，但缺少可核实的原件页码。"
                locations.append(
                    {
                        "item_id": item["item_id"],
                        "selection_index": index,
                        "unit_id": selection["unit_id"],
                        "start": start,
                        "end": end,
                        "pages": pages,
                        "issue": issue,
                    }
                )
        return {**detail, "quote_locations": locations}
