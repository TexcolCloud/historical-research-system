"""Read-only, workspace- and source-bound card/entity catalogue queries."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, func, not_, or_, select

from hrs.research.sources import current_card_ids, current_source_conditions
from hrs.storage import models as db


@dataclass(frozen=True, kw_only=True)
class CatalogueScope:
    book_id: str | UUID | None = None
    book_ids: tuple[str, ...] | None = None
    record_ids: tuple[str, ...] | None = None
    query: str | None = None


class CatalogueQuery:
    def __init__(self, engine, workspace_id):
        self.engine = engine
        self.workspace_id = workspace_id

    def list_cards(self, scope, *, offset=0, limit=100):
        return self._list_page(db.research_cards, scope, offset, limit)

    def list_entities(self, scope, *, offset=0, limit=100):
        return self._list_page(db.research_entities, scope, offset, limit)

    def count_cards(self, scope):
        return self._count(db.research_cards, scope)

    def count_entities(self, scope):
        return self._count(db.research_entities, scope)

    def count_stale_cards(self, scope):
        return self._count(db.research_cards, scope, stale=True)

    @staticmethod
    def _relation(table):
        return (
            table.join(db.research_runs)
            .join(db.runs, db.runs.c.id == db.research_runs.c.source_run_id)
            .join(db.books, db.books.c.id == table.c.book_id)
        )

    def _conditions(self, table, scope, *, stale=False):
        workspace_id = self.workspace_id
        book_id, book_ids, record_ids, query = (
            scope.book_id,
            scope.book_ids,
            scope.record_ids,
            scope.query,
        )
        conditions = [
            db.research_runs.c.workspace_id == workspace_id,
            db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
        ]
        if stale:
            conditions.append(
                not_(
                    func.coalesce(
                        and_(*current_source_conditions(db.research_runs, db.runs)), False
                    )
                )
            )
        else:
            conditions.extend(current_source_conditions(db.research_runs, db.runs))
            conditions.append(
                table.c.id.in_(current_card_ids(workspace_id))
                if table is db.research_cards
                else table.c.summary["card_id"].astext.in_(current_card_ids(workspace_id))
            )
        if table is db.research_cards:
            conditions.append(table.c.state != "excluded")
        if book_id:
            conditions.append(table.c.book_id == str(book_id))
        if book_ids is not None:
            conditions.append(table.c.book_id.in_(book_ids))
        if record_ids is not None:
            conditions.append(
                or_(
                    table.c.id.in_(record_ids),
                    table.c.summary["canonical_id"].astext.in_(record_ids),
                )
                if table is db.research_entities
                else table.c.id.in_(record_ids)
            )
        if query:
            # The cards/entities lane is explicitly SQL literal search; source hits use hybrid search.
            conditions.append(
                table.c.search_text.ilike(
                    "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",
                    escape="\\",
                )
            )
        return conditions

    def _count(self, table, scope, *, stale=False):
        with self.engine.connect() as connection:
            return connection.scalar(
                select(func.count())
                .select_from(self._relation(table))
                .where(*self._conditions(table, scope, stale=stale))
            )

    def _list_page(self, table, scope, offset, limit):
        conditions = self._conditions(table, scope)
        relation = self._relation(table)
        with self.engine.connect() as connection:
            total = connection.scalar(select(func.count()).select_from(relation).where(*conditions))
            rows = connection.execute(
                select(table.c.summary, db.runs.c.result["codex_revision_pending"].as_boolean())
                .select_from(relation)
                .where(*conditions)
                .order_by(table.c.id)
                .offset(offset)
                .limit(limit)
            ).all()
        items = [
            {
                **summary,
                "source_current": True,
                "needs_update": bool(revision),
                "source_status": "revision_pending" if revision else "current",
            }
            for summary, revision in rows
        ]
        return {"items": items, "total": total, "offset": offset, "limit": limit}
