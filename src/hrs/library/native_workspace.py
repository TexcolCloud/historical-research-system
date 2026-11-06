"""Bind SQL workflow commands to one native authority, including safe legacy adoption."""

from sqlalchemy import and_, or_, select, update

from hrs.library.errors import ServiceError
from hrs.processing.workspace import identity
from hrs.storage import models as db


class WrongWorkspace(ServiceError):
    def __init__(self):
        super().__init__(
            409,
            "本任务属于其他原生工作区，或旧关联尚未验证。请连接原工作区后继续；已发布内容仍可阅读和导出。",
        )


def workspace_runs(store):
    """Filter work before queue limits; foreign events must not starve this root."""
    scope = db.runs.c.result["codex_workspace_id"].astext
    documents = store.checkpoints.ids()
    return or_(
        scope == identity(store)["workspace_id"],
        and_(
            scope.is_(None),
            or_(
                db.runs.c.result["codex_document_id"].astext.in_(documents),
                db.runs.c.result["native_attach_document_id"].astext.in_(documents),
            ),
        ),
    )


def require_workspace(engine, run, store, *, connection=None):
    """Return a scoped run. Adopt legacy mappings only with matching native source proof.

    An explicit identity permits a missing checkpoint to report its ordinary recoverable
    file error. An unscoped old row never permits a fresh import into a different root.
    """
    workspace_id = identity(store)["workspace_id"]
    result = run["result"] or {}
    saved = result.get("codex_workspace_id")
    if saved:
        if saved != workspace_id:
            raise WrongWorkspace()
        return run
    document_id = result.get("codex_document_id") or result.get("native_attach_document_id")
    source_sha = (run["source"] or {}).get("sha256")
    if not document_id or not source_sha:
        raise WrongWorkspace()
    try:
        document = store.status(document_id)
    except (ValueError, OSError, KeyError) as error:
        raise WrongWorkspace() from error
    if document.get("source", {}).get("sha256") != source_sha:
        raise WrongWorkspace()

    def bind(target):
        latest = (
            target.execute(select(db.runs).where(db.runs.c.id == str(run["id"])).with_for_update())
            .mappings()
            .one()
        )
        retained = latest["result"] or {}
        if (
            retained.get("codex_workspace_id") not in {None, workspace_id}
            or (retained.get("codex_document_id") or retained.get("native_attach_document_id"))
            != document_id
            or (latest["source"] or {}).get("sha256") != source_sha
        ):
            raise WrongWorkspace()
        result = {**retained, "codex_workspace_id": workspace_id}
        target.execute(update(db.runs).where(db.runs.c.id == str(run["id"])).values(result=result))
        return {**latest, "result": result}

    if connection is not None:
        return bind(connection)
    with engine.begin() as target:
        return bind(target)
