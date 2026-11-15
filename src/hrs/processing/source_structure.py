"""Versioned source facts over complete page Markdown; no retrieval decisions live here."""

from copy import deepcopy
from typing import Literal, NotRequired, TypedDict, get_args

from hrs.processing.hashing import digest

SCHEMA_VERSION = 1
Role = Literal[
    "body",
    "heading",
    "toc",
    "index",
    "bibliography",
    "frontmatter",
    "header",
    "footer",
    "page_number",
    "note",
    "table",
    "figure",
    "caption",
    "watermark",
    "stamp",
    "marginalia",
    "unknown",
]
Layer = Literal[
    "source", "author_note", "editor_note", "translator_note", "annotation", "capture", "unknown"
]
Subkind = Literal[
    "title_page",
    "copyright",
    "imprint",
    "preface",
    "foreword",
    "afterword",
    "acknowledgements",
    "appendix",
    "other",
    "unknown",
]
Presence = Literal["present", "absent", "unknown"]
RelationKind = Literal["note_of", "caption_of", "continues", "reading_next", "table_continues"]
ROLES = frozenset(get_args(Role))
LAYERS = frozenset(get_args(Layer))
SUBKINDS = frozenset(get_args(Subkind))
RELATION_KINDS = frozenset(get_args(RelationKind))


class Coverage(TypedDict):
    role: Role
    status: Presence
    reason: NotRequired[str]


class Fragment(TypedDict):
    fragment_id: str
    role: Role
    layer: Layer
    start: int
    end: int
    text_sha256: str
    bbox: NotRequired[list[float]]
    subkind: NotRequired[Subkind]


class Relation(TypedDict):
    kind: RelationKind
    from_fragment_id: str
    to_fragment_id: str
    status: Literal["verified", "unknown"]
    evidence: str


class PageStructure(TypedDict):
    schema_version: int
    coverage: list[Coverage]
    fragments: list[Fragment]
    relations: list[Relation]


def prior_structure_workers(document, page):
    """Use retained authority, including earlier structure versions, at worker admission."""
    source = document["pages"][page - 1]
    workers = {
        (source.get(field) or {}).get("worker_id") for field in ("ocr", "candidate", "approved")
    }
    records = source.get("source_structure_history", []) + (
        [source["source_structure"]] if source.get("source_structure") else []
    )
    authored_tasks = {record["source_task_id"] for record in records}
    workers.update(
        task.get("receipt", {}).get("worker_id")
        for task in document["tasks"]
        if task.get("page") == page
        and (task["task_id"] in authored_tasks or "source_structure" in (task.get("result") or {}))
    )
    return workers - {None}


def validate_structure(value, text, *, page=None):
    """Validate exact offsets and explicit coverage once at every write entrance."""
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "coverage", "fragments", "relations"}
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError(
            "Source structure requires schema_version=1 and coverage/fragments/relations"
        )
    coverage, fragments, relations = (value[key] for key in ("coverage", "fragments", "relations"))
    if not all(isinstance(rows, list) for rows in (coverage, fragments, relations)):
        raise ValueError("Structure coverage, fragments and relations must be lists")
    statuses = {}
    for row in coverage:
        if not isinstance(row, dict) or not set(row) <= {"role", "status", "reason"}:
            raise ValueError("Invalid source structure coverage fields")
        role, status = row.get("role"), row.get("status")
        if (
            not isinstance(role, str)
            or role not in ROLES
            or role in statuses
            or not isinstance(status, str)
            or status not in {"present", "absent", "unknown"}
        ):
            raise ValueError(
                "Structure coverage requires distinct fixed roles and explicit presence"
            )
        if "reason" in row and (not isinstance(row["reason"], str) or not row["reason"].strip()):
            raise ValueError("Structure coverage reason must be nonempty text")
        statuses[role] = status
    if set(statuses) != ROLES:
        raise ValueError("Structure coverage must explicitly cover every fixed role")
    identifiers = set()
    value = deepcopy(value)
    fragments, relations = value["fragments"], value["relations"]
    for row in fragments:
        if isinstance(row, dict) and "exact" in row:
            literal = row.pop("exact")
            prefix, suffix = row.pop("prefix", ""), row.pop("suffix", "")
            if any(not isinstance(part, str) for part in (literal, prefix, suffix)) or not literal:
                raise ValueError("Structure literal and optional prefix/suffix must be text")
            matches, cursor = [], 0
            while (start := text.find(literal, cursor)) >= 0:
                if text[:start].endswith(prefix) and text[start + len(literal) :].startswith(
                    suffix
                ):
                    matches.append(start)
                cursor = start + 1
            if len(matches) != 1:
                raise ValueError(
                    "Structure exact fragment must be uniquely located in full page Markdown"
                )
            start = matches[0]
            derived = {"start": start, "end": start + len(literal), "text_sha256": digest(literal)}
            if any(key in row and row[key] != expected for key, expected in derived.items()):
                raise ValueError(
                    "Structure fragment supplied offsets/hash differ from its exact literal"
                )
            row.update(derived)
        if (
            not isinstance(row, dict)
            or not {"fragment_id", "role", "layer", "start", "end", "text_sha256"} <= set(row)
            or not set(row)
            <= {"fragment_id", "role", "layer", "start", "end", "text_sha256", "bbox", "subkind"}
        ):
            raise ValueError("Invalid source structure fragment fields")
        identity = row["fragment_id"]
        if not isinstance(identity, str) or not identity.strip() or identity in identifiers:
            raise ValueError("Structure fragment IDs must be distinct nonempty text")
        if page is not None and not identity.startswith(f"p{page}:"):
            raise ValueError("Structure fragment IDs must use their physical page prefix pN:")
        identifiers.add(identity)
        if (
            not isinstance(row["role"], str)
            or row["role"] not in ROLES
            or not isinstance(row["layer"], str)
            or row["layer"] not in LAYERS
        ):
            raise ValueError("Unknown structure role or source layer")
        if (
            "subkind" in row
            and (not isinstance(row["subkind"], str) or row["subkind"] not in SUBKINDS)
        ) or (row["role"] == "frontmatter" and "subkind" not in row):
            raise ValueError("Frontmatter requires an explicit fixed subkind")
        start, end = row["start"], row["end"]
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise ValueError(
                "Structure fragment offsets must locate a nonempty exact Markdown span"
            )
        if digest(text[start:end]) != row["text_sha256"]:
            raise ValueError("Structure fragment text hash mismatch")
        if statuses[row["role"]] != "present":
            raise ValueError("A structure fragment requires present role coverage")
        if "bbox" in row:
            box = row["bbox"]
            if (
                not isinstance(box, list)
                or len(box) != 4
                or any(type(n) not in {int, float} or not 0 <= n <= 1 for n in box)
                or not (box[0] < box[2] and box[1] < box[3])
            ):
                raise ValueError("Structure bbox must be normalized inspected original coordinates")
    if any(
        status == "present" and not any(f["role"] == role for f in fragments)
        for role, status in statuses.items()
    ):
        raise ValueError("Present structure coverage requires at least one exact fragment")
    seen = set()
    for row in relations:
        if (
            not isinstance(row, dict)
            or set(row) != {"kind", "from_fragment_id", "to_fragment_id", "status", "evidence"}
            or not isinstance(row.get("kind"), str)
            or row["kind"] not in RELATION_KINDS
            or row.get("status") not in ("verified", "unknown")
        ):
            raise ValueError("Invalid fixed source structure relation")
        if not all(
            isinstance(row[key], str) and row[key].strip()
            for key in ("from_fragment_id", "to_fragment_id", "evidence")
        ):
            raise ValueError("Structure relations require fragment identities and source evidence")
        if row["from_fragment_id"] not in identifiers:
            raise ValueError("Structure relation must originate in this page's fragments")
        if (
            row["status"] == "verified"
            and page is not None
            and row["to_fragment_id"].startswith(f"p{page}:")
            and row["to_fragment_id"] not in identifiers
        ):
            raise ValueError("Verified page relationship requires both exact source fragments")
        key = (row["kind"], row["from_fragment_id"], row["to_fragment_id"])
        if key in seen or key[1] == key[2]:
            raise ValueError("Duplicate or reflexive structure relation")
        seen.add(key)
    return value


def retain_structure(page, task, result, receipt, text):
    if "source_structure" not in result:
        return
    if not receipt.get("source_reading_sha256"):
        raise ValueError("Source structure requires frozen independent original-image observations")
    value = validate_structure(result["source_structure"], text, page=page["page"])
    for relation in value["relations"]:
        if relation["status"] == "verified" and not relation["to_fragment_id"].startswith(
            f"p{page['page']}:"
        ):
            target = next(
                (
                    row
                    for row in task.get("inputs", {}).get("relation_context", [])
                    if any(
                        f["fragment_id"] == relation["to_fragment_id"]
                        for f in row.get("fragments", [])
                    )
                ),
                None,
            )
            if not target or str(target["page"]) not in task.get("image_accesses", {}):
                raise ValueError(
                    "Verified cross-page structure relations require the frozen target and original-image receipt"
                )
    previous = page.get("source_structure")
    if previous:
        page.setdefault("source_structure_history", []).append(previous)
    page["source_structure"] = {
        **value,
        "page": page["page"],
        "image_sha256": page["image_sha256"],
        "markdown_sha256": digest(text),
        "source_task_id": task["task_id"],
        "source_result_sha256": digest(result),
        "source_reading_sha256": receipt.get("source_reading_sha256"),
        "created_at": receipt["created_at"],
    }


def retain_document_relations(document, task, result, receipt):
    """Existing organization/structure workers may link original-inspected frozen fragments."""
    if "structure_relations" not in result:
        return
    rows = result["structure_relations"]
    if not isinstance(rows, list):
        raise ValueError("Document structure relations must be a list")
    snapshot = structure_snapshot(document)
    fragments = {
        f["fragment_id"]: (page, f) for page in snapshot["pages"] for f in page["fragments"]
    }
    bindings = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"kind", "from_fragment_id", "to_fragment_id", "status", "evidence"}
            or not isinstance(row.get("kind"), str)
            or row["kind"] not in RELATION_KINDS
            or row.get("status") not in ("verified", "unknown")
        ):
            raise ValueError("Invalid document source structure relation")
        if not all(
            isinstance(row[key], str) and row[key].strip()
            for key in ("from_fragment_id", "to_fragment_id", "evidence")
        ):
            raise ValueError("Document structure relation requires explicit source evidence")
        if row["from_fragment_id"] == row["to_fragment_id"]:
            raise ValueError("Document structure relation cannot be reflexive")
        for key in ("from_fragment_id", "to_fragment_id"):
            identity = row[key]
            if identity not in fragments:
                raise ValueError(
                    "Document structure relation must reference current bound source fragments"
                )
            page, fragment = fragments[identity]
            frozen_pages = task.get("inputs", {}).get("source_structure", {}).get("pages", [])
            if task["stage"] == "structure":
                frozen_pages = task["inputs"].get("relation_context", [])
                if task["page"] == page["page"]:
                    frozen_pages = [page]
            expected = next(
                (
                    item
                    for source in frozen_pages
                    for item in source.get("fragments", [])
                    if item["fragment_id"] == identity
                ),
                None,
            )
            if expected != fragment:
                raise ValueError(
                    "Document structure relation fragment changed from its frozen source"
                )
            if row["status"] == "verified":
                access = task.get("image_accesses", {}).get(str(page["page"]))
                if task.get("page") == page["page"]:
                    access = task.get("image_access")
                if not access or access.get("image_sha256") != page["image_sha256"]:
                    raise ValueError(
                        "Verified document relation requires original-image receipts for both endpoints"
                    )
                if (
                    document["pages"][page["page"] - 1]["approved"]["worker_id"]
                    == receipt["worker_id"]
                ):
                    raise ValueError(
                        "Structure relationships require independent source inspection"
                    )
            bindings[identity] = {
                "page": page["page"],
                "image_sha256": page["image_sha256"],
                "markdown_sha256": page["markdown_sha256"],
                "fragment_sha256": digest(fragment),
            }
    if rows:
        document.setdefault("structure_relation_batches", []).append(
            {
                "relations": deepcopy(rows),
                "bindings": bindings,
                "source_task_id": task["task_id"],
                "source_result_sha256": digest(result),
                "created_at": receipt["created_at"],
            }
        )
        document["structure_version"] = document.get("structure_version", 0) + 1


def verify_structure_records(page, tasks):
    """Recheck the actual native result/observation receipts, including historical versions."""
    records = page.get("source_structure_history", []) + (
        [page["source_structure"]] if page.get("source_structure") else []
    )
    for record in records:
        task = tasks.get(record.get("source_task_id"), {})
        result, receipt = task.get("result") or {}, task.get("receipt") or {}
        reading = task.get("source_reading") or {}
        if not reading or (
            record.get("source_reading_sha256") != reading.get("sha256")
            or receipt.get("source_reading_sha256") != reading.get("sha256")
            or record.get("source_result_sha256") != digest(result)
            or receipt.get("result_sha256") != digest(result)
            or record.get("image_sha256") != task.get("inputs", {}).get("image_sha256")
        ):
            raise ValueError(
                "Source structure lacks its frozen native result/original-reading receipt"
            )
        text = (
            result.get("markdown")
            if result.get("verdict") == "corrections"
            else task["inputs"].get("markdown")
        )
        if not isinstance(text, str) or record.get("markdown_sha256") != digest(text):
            raise ValueError("Source structure Markdown binding mismatch")
        value = validate_structure(result.get("source_structure"), text, page=page["page"])
        if any(record.get(key) != value[key] for key in value):
            raise ValueError("Source structure differs from its retained native result")


def verify_relation_records(document, tasks):
    for batch in document.get("structure_relation_batches", []):
        task = tasks.get(batch.get("source_task_id"), {})
        result = task.get("result") or {}
        if (
            batch.get("source_result_sha256") != digest(result)
            or task.get("receipt", {}).get("result_sha256") != digest(result)
            or result.get("structure_relations") != batch.get("relations")
        ):
            raise ValueError("Document structure relationship result receipt mismatch")
        for identity, binding in batch.get("bindings", {}).items():
            references = task.get("inputs", {}).get("pages", [])
            if task.get("page"):
                references = [task["inputs"], *task["inputs"].get("relation_context", [])]
            source = next((row for row in references if row["page"] == binding["page"]), {})
            if any(
                source.get(key) != binding.get(key) for key in ("image_sha256", "markdown_sha256")
            ):
                raise ValueError("Document structure relation source binding mismatch")
            frozen_pages = task.get("inputs", {}).get("source_structure", {}).get("pages", [])
            if task["stage"] == "structure":
                frozen_pages = task["inputs"].get("relation_context", [])
                if task["page"] == binding["page"]:
                    frozen_pages = [
                        validate_structure(
                            result.get("source_structure"),
                            task["inputs"]["markdown"],
                            page=task["page"],
                        )
                    ]
            fragment = next(
                (
                    item
                    for row in frozen_pages
                    for item in row.get("fragments", [])
                    if item["fragment_id"] == identity
                ),
                None,
            )
            if not fragment or digest(fragment) != binding.get("fragment_sha256"):
                raise ValueError("Document structure relation fragment binding mismatch")
            if any(
                row["status"] == "verified"
                and identity in (row["from_fragment_id"], row["to_fragment_id"])
                for row in batch["relations"]
            ):
                access = (
                    task.get("image_access")
                    if task.get("page") == binding["page"]
                    else task.get("image_accesses", {}).get(str(binding["page"]))
                )
                if not access or access.get("image_sha256") != binding["image_sha256"]:
                    raise ValueError("Document structure relation lacks its original-image receipt")


def _legacy_structure(page):
    """One-way compatibility: literal legacy facts yield fragments; omission stays unknown."""
    text = page.get("approved", {}).get("markdown", "")
    observation = page.get("observation") or {}
    coverage = {role: {"role": role, "status": "unknown"} for role in sorted(ROLES)}
    fragments = []
    if observation.get("image_sha256") == page["image_sha256"] and observation.get(
        "markdown_sha256"
    ) == page.get("approved", {}).get("sha256"):
        literals = [("heading", "source", value) for value in observation.get("headings", [])] + [
            ("header", "source", value) for value in observation.get("running_headers", [])
        ]
        literals += [
            ("footer", "source", value) for value in observation.get("running_footers", [])
        ]
        outer_lines = [line.strip() for line in text.splitlines() if line.strip()]
        if (
            observation.get("printed_label")
            and outer_lines
            and observation["printed_label"] in (outer_lines[0], outer_lines[-1])
        ):
            literals.append(("page_number", "source", observation["printed_label"]))
        for role, layer, literal in literals:
            if not isinstance(literal, str) or not literal or text.count(literal) != 1:
                continue
            start = text.index(literal)
            fragments.append(
                {
                    "fragment_id": f"p{page['page']}:legacy:{len(fragments)}",
                    "role": role,
                    "layer": layer,
                    "start": start,
                    "end": start + len(literal),
                    "text_sha256": digest(literal),
                }
            )
            coverage[role]["status"] = "present"
    return {
        "schema_version": SCHEMA_VERSION,
        "coverage": list(coverage.values()),
        "fragments": fragments,
        "relations": [],
    }


def structure_snapshot(document):
    """Export current source-bound facts without modifying content or frozen policies."""
    pages = []
    for page in document["pages"]:
        stored = page.get("source_structure")
        approved = page.get("approved") or {}
        bound = bool(
            stored
            and stored.get("image_sha256") == page["image_sha256"]
            and stored.get("markdown_sha256") == approved.get("sha256")
        )
        facts = (
            {
                key: deepcopy(stored[key])
                for key in ("schema_version", "coverage", "fragments", "relations")
            }
            if bound
            else _legacy_structure(page)
        )
        pages.append(
            {
                **facts,
                "page": page["page"],
                "image_sha256": page["image_sha256"],
                "markdown_sha256": approved.get("sha256"),
                "status": "reviewed" if bound else "stale" if stored else "unreviewed",
                **(
                    {
                        "source_task_id": stored["source_task_id"],
                        "source_result_sha256": stored["source_result_sha256"],
                        "source_reading_sha256": stored["source_reading_sha256"],
                    }
                    if bound
                    else {}
                ),
            }
        )
    fragments = {f["fragment_id"]: (page, f) for page in pages for f in page["fragments"]}
    identifiers = set(fragments)
    tasks = {task["task_id"]: task for task in document.get("tasks", [])}
    for page in pages:
        for relation in page["relations"]:
            identity = relation["to_fragment_id"]
            if relation["status"] != "verified" or identity.startswith(f"p{page['page']}:"):
                continue
            task = tasks.get(page.get("source_task_id"), {})
            context = next(
                (
                    row
                    for row in task.get("inputs", {}).get("relation_context", [])
                    if any(item["fragment_id"] == identity for item in row.get("fragments", []))
                ),
                {},
            )
            current = fragments.get(identity)
            if (
                not current
                or any(
                    context.get(key) != current[0].get(key)
                    for key in ("image_sha256", "markdown_sha256")
                )
                or not any(item == current[1] for item in context.get("fragments", []))
            ):
                relation["status"] = "unknown"
    for batch in document.get("structure_relation_batches", []):
        for relation in batch["relations"]:
            endpoints = [relation["from_fragment_id"], relation["to_fragment_id"]]
            if not all(identity in fragments for identity in endpoints):
                continue
            bound = all(
                batch["bindings"].get(identity)
                == {
                    "page": fragments[identity][0]["page"],
                    "image_sha256": fragments[identity][0]["image_sha256"],
                    "markdown_sha256": fragments[identity][0]["markdown_sha256"],
                    "fragment_sha256": digest(fragments[identity][1]),
                }
                for identity in endpoints
            )
            if bound:
                source_page = fragments[endpoints[0]][0]
                source_page["relations"] = [
                    r
                    for r in source_page["relations"]
                    if (r["kind"], r["from_fragment_id"], r["to_fragment_id"])
                    != (relation["kind"], *endpoints)
                ] + [deepcopy(relation)]
    for page in pages:
        for relation in page["relations"]:
            if relation["to_fragment_id"] not in identifiers:
                relation["status"] = "unknown"
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "document_id": document["id"],
        "source_sha256": document["source"]["sha256"],
        "structure_version": document.get("structure_version", 0),
        "pages": pages,
    }
    return {**snapshot, "snapshot_sha256": digest(snapshot)}
