"""Source-bound, read-only network and timeline projections from the SQLite ledger."""

import calendar
import json
import re
from dataclasses import dataclass

from hrs.research.catalogue import ResearchCatalogue


@dataclass(frozen=True)
class DatePosition:
    start: str | None = None
    end: str | None = None
    precision: str = "unknown"
    lower: tuple | None = None
    upper: tuple | None = None

    @property
    def undated(self):
        return self.lower is None


def _date_bound(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        raise ValueError("日期请填写 YYYY、YYYY-MM 或 YYYY-MM-DD。")
    parts = [int(part) for part in value.split("-")]
    year = parts[0]
    month = parts[1] if len(parts) > 1 else 1
    if not 1 <= year <= 9999 or not 1 <= month <= 12:
        raise ValueError("日期无效，请检查年份、月份和天数。")
    last = calendar.monthrange(year, month)[1]
    day = parts[2] if len(parts) > 2 else 1
    if not 1 <= day <= last:
        raise ValueError("日期无效，请检查年份、月份和天数。")
    lower = (year, month, day)
    upper = (year, 12, 31) if len(parts) == 1 else (year, month, last if len(parts) == 2 else day)
    return lower, upper, ("year", "month", "day")[len(parts) - 1]


def date_position(value):
    """Only interpret explicit ISO precision; free text and unknown readings stay undated."""
    if not value or value.get("certainty", "unknown") == "unknown":
        return DatePosition()
    normalized = value.get("normalized")
    if not isinstance(normalized, str):
        return DatePosition()
    parts = re.split(r"/|\.\.", normalized)
    if len(parts) not in {1, 2}:
        return DatePosition()
    try:
        lower, _, precision = _date_bound(parts[0])
        _, upper, _ = _date_bound(parts[-1])
    except ValueError:
        return DatePosition()
    if lower > upper:
        return DatePosition()
    return DatePosition(
        parts[0], parts[-1], precision if len(parts) == 1 else "range", lower, upper
    )


class DateWindow:
    def __init__(self, start=None, end=None, *, include_undated=True):
        self.lower = _date_bound(start)[0] if start else None
        self.upper = _date_bound(end)[1] if end else None
        self.include_undated = include_undated
        if self.lower and self.upper and self.lower > self.upper:
            raise ValueError("开始日期不能晚于结束日期。")

    def includes(self, position):
        if position.undated:
            return self.include_undated
        return not (
            self.lower and position.upper < self.lower or self.upper and position.lower > self.upper
        )


class NetworkQuery:
    """The library supplies only a current-source gate; evidence stays in the native ledger."""

    def __init__(self, ledger, *, current_cards=None):
        self.ledger = ledger
        self.current_cards = current_cards

    def _scope(self, book_ids):
        selected = set(map(str, book_ids)) if book_ids is not None else None
        cards = self.current_cards
        if cards is not None and selected is not None:
            cards = [card for card in cards if card["book_id"] in selected]
        gate = (
            "JOIN json_each(?) allowed ON c.id=json_extract(allowed.value,'$.id') "
            "AND c.version=json_extract(allowed.value,'$.version') "
            "AND json_extract(c.payload,'$.publication_sha256')="
            "json_extract(allowed.value,'$.publication_sha256') "
            if cards is not None
            else ""
        )
        args = [json.dumps(cards)] if cards is not None else []
        where = ""
        if selected is not None:
            where = "AND c.book_id IN (SELECT value FROM json_each(?)) "
            args.append(json.dumps(sorted(selected)))
        # JSON binds avoid SQLite's variable limit for a large current library. No runtime tables are written.
        return (
            "WITH cards AS (SELECT c.*,"
            + (
                "COALESCE(json_extract(allowed.value,'$.needs_update'),0)"
                if cards is not None
                else "0"
            )
            + " AS needs_update FROM research_cards c "
            + gate
            + "JOIN research_runs r ON r.id=c.run_id WHERE c.state IN ('verified','human_accepted') "
            "AND r.state<>'cancelled' "
            "AND c.version=(SELECT MAX(v.version) FROM research_cards v WHERE v.id=c.id) "
            "AND EXISTS(SELECT 1 FROM research_catalogue_documents d WHERE d.card_id=c.id "
            "AND d.card_version=c.version AND d.active=1) "
            + where
            + "), members AS (SELECT m.*,c.needs_update FROM research_catalogue_members m "
            "JOIN cards c ON c.id=m.card_id AND c.version=m.card_version WHERE m.active=1 "
            "AND json_array_length(m.payload,'$.evidence_ids')>0 "
            "AND NOT EXISTS(SELECT 1 FROM json_each(m.payload,'$.evidence_ids') ref "
            "WHERE NOT EXISTS(SELECT 1 FROM json_each(c.payload,'$.evidence') proof "
            "WHERE json_extract(proof.value,'$.id')=ref.value))), "
            "entities AS (SELECT e.* FROM research_catalogue_entities e WHERE e.redirect IS NULL "
            "AND e.version=(SELECT MAX(v.version) FROM research_catalogue_entities v WHERE v.id=e.id) "
            "AND EXISTS(SELECT 1 FROM members m WHERE m.entity_id=e.id)), "
            "edges AS (SELECT e.*,c.needs_update,"
            "COALESCE(json_extract(e.payload,'$.date'),json_extract(sm.payload,'$.date'),"
            "json_extract(tm.payload,'$.date')) AS event_date,"
            "json_extract(c.payload,'$.formation_date') AS formation_date,"
            "COALESCE(json_extract(e.payload,'$.event_status'),json_extract(sm.payload,'$.event_status'),"
            "json_extract(tm.payload,'$.event_status')) AS event_status,"
            "COALESCE(json_extract(e.payload,'$.status_note'),json_extract(sm.payload,'$.status_note'),"
            "json_extract(tm.payload,'$.status_note')) AS status_note FROM research_catalogue_edges e "
            "JOIN cards c ON c.id=e.card_id AND c.version=e.card_version "
            "LEFT JOIN members sm ON sm.id=json_extract(e.payload,'$.source_member_id') "
            "LEFT JOIN members tm ON tm.id=json_extract(e.payload,'$.target_member_id') WHERE e.active=1 "
            "AND (EXISTS(SELECT 1 FROM entities n WHERE n.id=e.source_id) "
            "OR EXISTS(SELECT 1 FROM cards n WHERE n.id=e.source_id)) "
            "AND (EXISTS(SELECT 1 FROM entities n WHERE n.id=e.target_id) "
            "OR EXISTS(SELECT 1 FROM cards n WHERE n.id=e.target_id)) "
            "AND json_array_length(e.payload,'$.evidence')>0 "
            "AND NOT EXISTS(SELECT 1 FROM json_each(e.payload,'$.evidence') proof "
            "WHERE NOT EXISTS(SELECT 1 FROM json_each(c.payload,'$.evidence') own "
            "WHERE json_extract(own.value,'$.id')=json_extract(proof.value,'$.id'))) "
            "AND NOT EXISTS(SELECT 1 FROM json_each(e.payload,'$.evidence_refs') ref "
            "WHERE NOT EXISTS(SELECT 1 FROM cards owner,json_each(owner.payload,'$.evidence') proof "
            "WHERE owner.id=json_extract(ref.value,'$.card_id') "
            "AND owner.version=json_extract(ref.value,'$.card_version') "
            "AND json_extract(proof.value,'$.id')=json_extract(ref.value,'$.evidence_id'))) "
            "AND (json_extract(e.payload,'$.target_card_id') IS NULL OR EXISTS(SELECT 1 FROM cards target "
            "WHERE target.id=json_extract(e.payload,'$.target_card_id') "
            "AND target.version=COALESCE(json_extract(e.payload,'$.target_card_version'),target.version) "
            "AND json_extract(target.payload,'$.publication_sha256')="
            "COALESCE(json_extract(e.payload,'$.target_publication_sha256'),"
            "json_extract(target.payload,'$.publication_sha256'))))) ",
            args,
        )

    @staticmethod
    def _date_fields(value):
        position = date_position(value)
        return {
            "date": value
            or {"text": "", "normalized": None, "certainty": "unknown", "evidence_ids": []},
            "date_start": position.start,
            "date_end": position.end,
            "date_precision": position.precision,
            "undated": position.undated,
        }

    @staticmethod
    def _timeline_participants(connection, cte, args, page):
        """Read source-specific roles in one batch inside the same ledger snapshot."""
        events = [item for item in page if item["item_kind"] == "event"]
        if not events:
            return {}
        rows = connection.execute(
            cte
            + "SELECT m.id,m.entity_id,m.card_id,m.card_version,json_extract(m.payload,'$.local_id'),"
            "json_extract(m.payload,'$.name'),json_extract(m.payload,'$.evidence_ids'),"
            "json_extract(m.payload,'$.participant_ids'),json_extract(m.payload,'$.participant_roles') "
            "FROM members m JOIN research_catalogue_entities identity ON identity.id=m.entity_id "
            "WHERE m.card_id IN (SELECT value FROM json_each(?)) AND identity.redirect IS NULL "
            "AND identity.version=(SELECT MAX(v.version) FROM research_catalogue_entities v WHERE v.id=identity.id)",
            [*args, json.dumps(sorted({item["card_id"] for item in events}))],
        )
        # Parse each current source member once; a JSON self-join reparses every same-card pair.
        sources, members = {}, {}
        for identity, entity, card, version, local_id, name, proofs, participants, roles in rows:
            source = {
                "entity_id": entity,
                "name": name,
                "evidence_ids": json.loads(proofs) if proofs else [],
            }
            sources[identity] = (
                json.loads(participants) if participants else [],
                json.loads(roles) if roles else [],
            )
            members[(card, version, local_id)] = source
        grouped = {}
        for event in events:
            participants, source_roles = sources.get(event["id"], ([], []))
            roles = {}
            for role in source_roles:
                roles.setdefault(role["mention_id"], role)
            for local_id in participants:
                participant = members.get((event["card_id"], event["card_version"], local_id))
                if participant is None:
                    continue
                group = grouped.setdefault(
                    event["id"], {"participants": [], "participant_count": 0, "evidence_ids": []}
                )
                group["participant_count"] += 1
                if len(group["participants"]) >= 20:
                    continue
                role = roles.get(local_id, {})
                group["participants"].append(
                    {
                        "entity_id": participant["entity_id"],
                        "name": participant["name"],
                        "role": role.get("role", "participant"),
                    }
                )
                group["evidence_ids"].extend(
                    [*participant["evidence_ids"], *role.get("evidence_ids", [])]
                )
        return grouped

    @staticmethod
    def _nodes(connection, cte, args, identities):
        rows = connection.execute(
            cte + "SELECT e.id,e.kind,e.name,e.version,"
            "json_group_array(DISTINCT m.book_id),json_group_array(DISTINCT m.card_id),MAX(m.needs_update),"
            "MIN(json_extract(c.payload,'$.title')) "
            "FROM entities e JOIN members m ON m.entity_id=e.id "
            "JOIN cards c ON c.id=m.card_id AND c.version=m.card_version "
            "WHERE e.id IN (SELECT value FROM json_each(?)) GROUP BY e.id ORDER BY e.id",
            [*args, json.dumps(sorted(identities))],
        )
        result = {}
        for row in rows:
            books, cards = sorted(json.loads(row[4])), sorted(json.loads(row[5]))
            result[row[0]] = {
                "id": row[0],
                "canonical_id": row[0],
                "kind": row[1],
                "name": row[2],
                "version": row[3],
                "book_ids": books[:20],
                "card_ids": cards[:20],
                "book_count": len(books),
                "card_count": len(cards),
                "metadata_truncated": len(books) > 20 or len(cards) > 20,
                "needs_update": bool(row[6]),
                "source_current": True,
                "source_title": row[7],
            }
        for row in connection.execute(
            cte
            + "SELECT id,version,book_id,json_extract(payload,'$.title'),needs_update FROM cards "
            "WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id",
            [*args, json.dumps(sorted(identities))],
        ):
            result[row[0]] = {
                "id": row[0],
                "card_id": row[0],
                "kind": "card",
                "name": row[3],
                "version": row[1],
                "book_ids": [row[2]],
                "card_ids": [row[0]],
                "book_count": 1,
                "card_count": 1,
                "metadata_truncated": False,
                "needs_update": bool(row[4]),
                "source_current": True,
                "source_title": row[3],
            }
        return result

    def graph(
        self,
        identity,
        *,
        hops=1,
        limit=100,
        book_ids=None,
        node_kinds=None,
        predicate=None,
        date_from=None,
        date_to=None,
        include_undated=True,
        include_source_cards=False,
    ):
        if (
            type(hops) is not int
            or hops not in {1, 2}
            or type(limit) is not int
            or not 1 <= limit <= 200
        ):
            raise ValueError("关系网络支持1–2层展开，每次最多返回200条关系。")
        kinds = set(node_kinds) if node_kinds is not None else None
        if kinds is not None and not kinds <= {
            "person",
            "organization",
            "place",
            "document",
            "event",
            "card",
        }:
            raise ValueError("关系网络包含不支持的实体类型。")
        window = DateWindow(date_from, date_to, include_undated=include_undated)
        cte, args = self._scope(book_ids)
        with self.ledger.connect() as connection:
            if not ResearchCatalogue.exists(connection):
                raise FileNotFoundError(identity)
            identity = ResearchCatalogue._resolve(connection, identity)["id"]
            root = self._nodes(connection, cte, args, {identity}).get(identity)
            if root is None:
                raise FileNotFoundError(identity)
            reached, frontier, edges = {identity}, {identity}, {}
            truncated = False
            for _ in range(hops):
                if not frontier:
                    break
                rows = connection.execute(
                    cte + "SELECT e.id,e.source_id,e.target_id,e.kind,e.card_id,e.card_version,"
                    "json_extract(e.payload,'$.book_id'),json_array_length(e.payload,'$.evidence'),"
                    "json_extract(e.payload,'$.certainty'),"
                    "COALESCE(e.event_date,e.formation_date),"
                    "e.needs_update,COALESCE((SELECT n.kind FROM entities n WHERE n.id=e.source_id),'card'),"
                    "COALESCE((SELECT n.kind FROM entities n WHERE n.id=e.target_id),'card'),"
                    "CASE WHEN e.event_date IS NULL THEN 'formation' ELSE 'event' END,"
                    "e.event_status,e.status_note FROM edges e WHERE "
                    "(e.source_id IN (SELECT value FROM json_each(?)) "
                    "OR e.target_id IN (SELECT value FROM json_each(?))) "
                    + ("AND e.kind=? " if predicate else "")
                    + "ORDER BY e.id",
                    [
                        *args,
                        json.dumps(sorted(frontier)),
                        json.dumps(sorted(frontier)),
                        *([predicate] if predicate else []),
                    ],
                )
                following = set()
                for row in rows:
                    if row[0] in edges:
                        continue
                    pair = [row[1], row[2]]
                    if "card" in row[11:13] and not include_source_cards:
                        continue
                    if kinds is not None and any(
                        key != identity and kind not in kinds for key, kind in zip(pair, row[11:13])
                    ):
                        continue
                    value = json.loads(row[9]) if row[9] else None
                    if not window.includes(date_position(value)):
                        continue
                    if len(edges) >= limit:
                        truncated = True
                        break
                    edges[row[0]] = {
                        "id": row[0],
                        "source_id": row[1],
                        "target_id": row[2],
                        "kind": row[3],
                        "predicate": row[3],
                        "card_id": row[4],
                        "card_version": row[5],
                        "book_id": row[6],
                        "evidence_count": row[7],
                        "certainty": row[8],
                        "date": value,
                        "date_basis": row[13],
                        "event_status": row[14] or "unknown",
                        "status_note": row[15] or "",
                        "source_current": True,
                        "needs_update": bool(row[10]),
                    }
                    following.update(set(pair) - reached)
                    reached.update(pair)
                frontier = following
            nodes = self._nodes(connection, cte, args, reached)
            return {
                "entity_id": identity,
                "hops": hops,
                "limit": limit,
                "truncated": truncated,
                "nodes": [
                    nodes[key] for key in sorted(nodes, key=lambda key: (key != identity, key))
                ],
                "edges": list(edges.values()),
                "usage": "evidence-linked research leads",
            }

    def relation_context(self, identity):
        cte, args = self._scope(None)
        with self.ledger.connect() as connection:
            row = connection.execute(
                cte + "SELECT COALESCE(event_date,formation_date),"
                "CASE WHEN event_date IS NULL THEN 'formation' ELSE 'event' END,"
                "event_status,status_note FROM edges WHERE id=?",
                [*args, identity],
            ).fetchone()
        if row is None:
            raise FileNotFoundError(identity)
        return {
            "date": json.loads(row[0]) if row[0] else None,
            "date_basis": row[1],
            "event_status": row[2] or "unknown",
            "status_note": row[3] or "",
        }

    def entity_index(self, *, q=None, kind=None, book_ids=None, offset=0, limit=50):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError("实体列表页码无效，每页支持1–100条记录。")
        if kind is not None and kind not in {
            "person",
            "organization",
            "place",
            "event",
            "document",
        }:
            raise ValueError("请选择支持的实体类型。")
        cte, args = self._scope(book_ids)
        where, filters = [], []
        if kind:
            where.append("e.kind=?")
            filters.append(kind)
        if q:
            where.append(
                "(instr(lower(e.name),lower(?))>0 OR EXISTS(SELECT 1 FROM json_each(e.payload,'$.aliases') alias "
                "WHERE instr(lower(json_extract(alias.value,'$.name')),lower(?))>0 "
                "AND EXISTS(SELECT 1 FROM json_each(alias.value,'$.evidence_refs') ref JOIN members m "
                "ON m.entity_id=e.id AND m.card_id=json_extract(ref.value,'$.card_id') "
                "AND m.card_version=json_extract(ref.value,'$.card_version') "
                "JOIN json_each(m.payload,'$.evidence_ids') proof "
                "ON proof.value=json_extract(ref.value,'$.evidence_id'))))"
            )
            filters.extend([q, q])
        condition = " WHERE " + " AND ".join(where) if where else ""
        with self.ledger.connect() as connection:
            if not ResearchCatalogue.exists(connection):
                return {"items": [], "total": 0, "offset": offset, "limit": limit}
            total = connection.execute(
                cte + "SELECT COUNT(*) FROM entities e" + condition, [*args, *filters]
            ).fetchone()[0]
            identities = [
                row[0]
                for row in connection.execute(
                    cte
                    + "SELECT e.id FROM entities e"
                    + condition
                    + " ORDER BY e.name,e.id LIMIT ? OFFSET ?",
                    [*args, *filters, limit, offset],
                )
            ]
            nodes = self._nodes(connection, cte, args, identities) if identities else {}
        return {
            "items": [nodes[key] for key in identities],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def timeline(
        self,
        *,
        entity_id=None,
        book_ids=None,
        date_basis="event",
        date_from=None,
        date_to=None,
        include_undated=True,
        offset=0,
        limit=100,
    ):
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError("时间线页码无效，每页支持1–200条记录。")
        if date_basis not in {"event", "formation"}:
            raise ValueError("请选择事件发生时间或文献形成时间。")
        window = DateWindow(date_from, date_to, include_undated=include_undated)
        cte, args = self._scope(book_ids)
        scope_args = tuple(args)
        entity = None
        with self.ledger.connect() as connection:
            if not ResearchCatalogue.exists(connection):
                if entity_id:
                    raise FileNotFoundError(entity_id)
                rows = []
            else:
                if entity_id:
                    entity_id = ResearchCatalogue._resolve(connection, entity_id)["id"]
                    entity = self._nodes(connection, cte, args, {entity_id}).get(entity_id)
                    if entity is None:
                        raise FileNotFoundError(entity_id)
                if date_basis == "event":
                    sql = "SELECT m.id,m.entity_id,m.card_id,m.card_version,m.book_id,json_extract(m.payload,'$.run_id'),e.name,"
                    sql += (
                        "json_extract(m.payload,'$.description'),json_extract(m.payload,'$.date'),"
                    )
                    sql += "json_extract(m.payload,'$.event_status'),json_extract(m.payload,'$.status_note'),"
                    sql += "json_extract(m.payload,'$.evidence_ids'),m.needs_update FROM members m "
                    sql += "JOIN entities e ON e.id=m.entity_id WHERE e.kind='event' "
                    if entity_id:
                        sql += "AND (e.id=? OR EXISTS(SELECT 1 FROM edges edge WHERE "
                        sql += "json_extract(edge.payload,'$.event_id')=e.id "
                        sql += "AND edge.card_id=m.card_id AND (edge.source_id=? OR edge.target_id=?))) "
                        args += [entity_id] * 3
                else:
                    sql = "SELECT c.id,NULL,c.id,c.version,c.book_id,c.run_id,json_extract(c.payload,'$.title'),"
                    sql += "json_extract(c.payload,'$.summary'),json_extract(c.payload,'$.formation_date'),NULL,NULL,"
                    sql += "json_extract(c.payload,'$.formation_date.evidence_ids'),c.needs_update FROM cards c WHERE 1=1 "
                    if entity_id:
                        sql += "AND EXISTS(SELECT 1 FROM members m WHERE m.card_id=c.id AND "
                        sql += "(m.entity_id=? OR EXISTS(SELECT 1 FROM edges edge WHERE edge.card_id=c.id "
                        sql += "AND json_extract(edge.payload,'$.event_id')=m.entity_id "
                        sql += "AND (edge.source_id=? OR edge.target_id=?)))) "
                        args += [entity_id] * 3
                rows = connection.execute(cte + sql, args)
            items = []
            for row in rows:
                value = json.loads(row[8]) if row[8] else None
                position = date_position(value)
                if not window.includes(position):
                    continue
                ids = json.loads(row[11]) if row[11] else []
                ids = list(dict.fromkeys([*ids, *((value or {}).get("evidence_ids") or [])]))
                items.append(
                    {
                        "id": row[0],
                        "entity_id": row[1],
                        "canonical_id": row[1],
                        "card_id": row[2],
                        "card_version": row[3],
                        "book_id": row[4],
                        "run_id": row[5],
                        "name": row[6],
                        "description": row[7] or "",
                        "event_status": row[9] or "unknown",
                        "status_note": row[10] or "",
                        "evidence_ids": ids,
                        "evidence_count": len(ids),
                        "needs_update": bool(row[12]),
                        "source_current": True,
                        "item_kind": "event" if date_basis == "event" else "document",
                        "date_basis": date_basis,
                        **self._date_fields(value),
                        "_sort": position.lower or (10000, 1, 1),
                    }
                )
            items.sort(key=lambda item: (item["_sort"], item["id"]))
            total = len(items)
            undated = sum(item["undated"] for item in items)
            page = items[offset : offset + limit]
            participants = self._timeline_participants(connection, cte, scope_args, page)
            for item in page:
                item.pop("_sort")
                group = participants.get(item["id"], {})
                item["participants"] = group.get("participants", [])
                item["participant_count"] = group.get("participant_count", 0)
                item["evidence_ids"] = list(
                    dict.fromkeys([*item["evidence_ids"], *group.get("evidence_ids", [])])
                )
                item["evidence_count"] = len(item["evidence_ids"])
        return {
            "items": page,
            "total": total,
            "dated_total": total - undated,
            "undated_total": undated,
            "offset": offset,
            "limit": limit,
            "date_basis": date_basis,
            "entity_id": entity_id,
            "entity_name": entity["name"] if entity else None,
            "truncated": offset + limit < total,
        }
