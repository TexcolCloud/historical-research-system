"""Explicit user recovery requests, delivered once per durable workflow identity."""

from sqlalchemy import insert, select, update

from hrs.library.errors import ServiceError
from hrs.storage import models as db


def recovery_reason(failure):
    from hrs.processing.errors import EvidenceMismatch

    message = str(failure)
    if (
        isinstance(failure, EvidenceMismatch)
        or "hash mismatch" in message
        or "differs from" in message
    ):
        return "原件或证据与保存记录不一致。请在故障详情中查看受影响文件，恢复原文件后再点击“从断点恢复”。"
    if isinstance(failure, OSError):
        return "无法读取恢复所需文件或启动本机服务。请在故障详情中查看路径和原因，修复后再点击“从断点恢复”。"
    if any("\u4e00" <= char <= "\u9fff" for char in message):
        return message
    return "恢复检查未能完成。请展开故障详情查看原因，修复后再点击“从断点恢复”。"


def retry_run(engine, run_id, request_id):
    run_id = str(run_id)
    key = f"retry:{run_id}:{request_id}"
    with engine.begin() as connection:
        book_id = connection.scalar(select(db.runs.c.book_id).where(db.runs.c.id == run_id))
        from hrs.library.deletion import require_active

        if book_id is not None:
            require_active(connection, book_id)
        run = (
            connection.execute(select(db.runs).where(db.runs.c.id == run_id).with_for_update())
            .mappings()
            .one_or_none()
        )
        if run is None:
            raise ServiceError(404, "任务不存在。")
        if run["kind"] == "cards":
            raise ServiceError(410, "史料卡制作已停用，历史结果与证据仍保留。")
        if not (
            (run["result"] or {}).get("codex_document_id")
            or (run["result"] or {}).get("processing_backend") == "codex"
        ):
            raise ServiceError(409, "旧执行链已退役，请保留历史结果并将原件导入原生处理流程。")
        if connection.scalar(select(db.outbox.c.id).where(db.outbox.c.dedup_key == key)):
            return dict(run)
        if run["state"] != "failed":
            raise ServiceError(
                409, "仅未完成的失败任务需要重试，正在处理和已完成的任务不会重复执行。"
            )
        attempt = run["recovery_attempt"] + 1
        values = {
            "state": "queued",
            "error": None,
            "recovery_attempt": attempt,
            "revision": run["revision"] + 1,
        }
        if (run["result"] or {}).get("codex_document_id"):
            values["result"] = {
                **run["result"],
                "codex_recovery": {
                    "state": "checking",
                    "request_id": str(request_id),
                    "attempt": attempt,
                    "reason": "正在检查执行器、登录状态和保存的检查点。",
                },
            }
        connection.execute(update(db.runs).where(db.runs.c.id == run_id).values(**values))
        published = (run["result"] or {}).get("published")
        if run["kind"] == "book" and not published:
            connection.execute(
                update(db.books).where(db.books.c.id == run["book_id"]).values(state="queued")
            )
        connection.execute(
            insert(db.outbox).values(
                dedup_key=key,
                run_id=run_id,
                kind="retry_run",
                payload={
                    "recovery_attempt": attempt,
                    "run_kind": run["kind"],
                    "request_id": str(request_id),
                },
            )
        )
        connection.execute(
            insert(db.events).values(
                book_id=run["book_id"],
                run_id=run_id,
                kind="run.changed",
                payload={
                    "state": "queued",
                    "book_state": "ready" if published else "queued",
                    "stage": run["stage"],
                    "run_kind": run["kind"],
                    "revision": values["revision"],
                },
            )
        )
    return {**run, **values}


def preflight(store):
    """Local readiness checks; no paid model request and no content decision."""
    import shutil
    import subprocess

    executable = shutil.which("codex")
    if not executable:
        raise ValueError("未找到 Codex 执行程序，请安装并登录后恢复。")
    try:
        login = subprocess.run(
            [executable, "login", "status"], capture_output=True, timeout=10, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("Codex 登录检查超时，请检查本机服务后重试。") from error
    if login.returncode:
        raise ValueError("Codex 登录已失效，请重新登录后恢复。")


def native_report(settings, engine, run_id):
    from hrs.library.books import get_run
    from hrs.processing.recovery import plan
    from hrs.processing.store import Store
    from hrs.processing.workspace import resolve_workspace

    run = get_run(engine, run_id)
    identity = (run["result"] or {}).get("codex_document_id")
    if not identity:
        if (run["result"] or {}).get("processing_backend") == "codex":
            return {
                "state": "available",
                "reason": "原件准备尚未完成。恢复后将重新校验已保存的上传文件，再建立原生处理检查点。",
                "action": "重试原件准备",
                "stage": "verify_upload",
                "preserved_ocr": 0,
                "preserved_approved": 0,
                "unfinished_tasks": 1,
            }
        return {
            "state": "blocked",
            "reason": "旧执行链已退役。原件、历史结果和人工决定保留，需要继续处理时请重新导入原件。",
            "action": "导入原件到原生流程",
            "unfinished_tasks": 0,
        }
    saved = (run["result"] or {}).get("codex_recovery", {})
    try:
        store = Store(resolve_workspace(settings.codex_root))
        doc = store.status(identity)
    except (OSError, ValueError):
        return {
            "state": "blocked",
            "reason": "保存的检查点暂不可读，请恢复工作区文件后重试。",
            "action": "检查原件与工作区文件",
            "preserved_ocr": None,
            "preserved_approved": None,
        }
    report = plan(doc)
    request = doc.get("run_request", {})
    exhausted = request.get("technical_failures", 0) >= request.get("retry_limit", 3)
    reason = (
        "自动检查点恢复仍未成功，请修复故障后从断点恢复。"
        if request.get("automatic_checkpoint_used")
        else "自动重试已停止。恢复时将校验已保存结果，只继续未完成任务。"
    )
    if request.get("recovery_error"):
        reason = "检查点校验未通过，请检查原件与证据文件后恢复。"
    if request.get("state") == "retryable_error" and not exhausted:
        report.update(
            state="retrying",
            retry_at=request.get("not_before"),
            reason="正在等待下一次自动重试，已保存结果保留，无需重复点击。",
        )
    elif (
        exhausted
        and request.get("checkpoint_recovery_enabled")
        and not request.get("automatic_checkpoint_used")
    ):
        report.update(
            state="checkpoint",
            retry_at=request.get("not_before"),
            reason="普通重试已结束，等待从已保存检查点进行最后一次自动恢复。",
        )
    else:
        report["reason"] = reason
    if saved.get("state") in {"checking", "waiting", "blocked"} or (
        saved.get("state") == "resumed" and not exhausted
    ):
        report.update(saved)
    if report["state"] == "waiting":
        report["reason"] = "上一次处理尚未结束，系统会在结束后继续恢复检查。"
    if report["unfinished_tasks"]:
        from hrs.library.projection.facts import runtime_control_for_document

        control = runtime_control_for_document(doc, store.runtime_control.snapshot())
        report["runtime_control"] = control
        if control and saved.get("state") not in {"checking", "waiting"}:
            if control["state"] == "blocked":
                blocked = next(s for s in control["scopes"] if s["state"] == "blocked")
                code = blocked.get("code")
                report.update(
                    state="blocked",
                    retry_at=None,
                    reason=(
                        "登录状态需要修复，请重新登录后从断点恢复。"
                        if code in {"authentication", "auth", "unauthorized", "login_required"}
                        else "账户额度已用尽，请在额度恢复后从断点恢复。"
                        if code in {"quota_exhausted", "insufficient_quota", "quota"}
                        else "自动恢复已停止。请检查故障原因后从断点恢复。"
                    ),
                )
            elif control["state"] in {"cooldown", "probing"} and not exhausted:
                report.update(
                    state="retrying",
                    retry_at=control.get("next_retry_at"),
                    reason="受限模型正在等待或尝试恢复，已保存结果保留，无需重复点击。",
                )
        if saved.get("state") not in {"checking", "waiting"} and any(
            task.get("technical_blocked") and task["state"] in {"ready", "leased"}
            for task in doc["tasks"]
        ):
            report.update(
                state="blocked",
                retry_at=None,
                reason="部分任务的自动尝试次数已达上限。已保存结果保留，请检查故障后从断点恢复。",
            )
    report.setdefault("action", "从断点恢复")
    return report


def resume_native(bridge, event, run):
    """Dispatch one durable recovery request; never hold SQL locks across readiness or evidence I/O."""
    import time

    from hrs.processing.recovery import RecoveryWait, plan

    with bridge.engine.connect() as connection:
        if connection.scalar(select(db.outbox.c.delivered).where(db.outbox.c.id == event["id"])):
            return
    identity = run["result"]["codex_document_id"]
    saved = run["result"].get("codex_recovery", {})
    if saved.get("state") == "waiting" and saved.get("retry_at", 0) > time.time():
        if plan(bridge.store.status(identity))["retry_at"]:
            return
    try:
        snapshot = bridge.store.status(identity)
        receipt_id = "platform:" + str(event["id"])
        receipt = snapshot.get("recoveries", {}).get(receipt_id)
        if not receipt:
            current = plan(snapshot)
            if current["retry_at"]:
                raise RecoveryWait(current["retry_at"])
            preflight(bridge.store)
            receipt = bridge.store.recover_run(identity, receipt_id)
        status = {**receipt, "state": "resumed", "reason": "检查通过，已从保存的断点加入处理队列。"}
        state, delivered, error = "queued", True, None
    except RecoveryWait as waiting:
        status = {"state": "waiting", "retry_at": waiting.retry_at, "reason": str(waiting)}
        state, delivered, error = "queued", False, None
    except (ValueError, OSError, RuntimeError) as failure:
        message = recovery_reason(failure)
        status = {"state": "blocked", "reason": message, "action": "修复后从断点恢复"}
        state, delivered, error = (
            "failed",
            True,
            {"code": "recovery_preflight", "detail": str(failure)},
        )
        try:
            paused = bridge.store.status(identity).get("run_request", {}).get("pause_requested")
        except (OSError, ValueError):
            paused = False
        if paused:
            status = {"state": "paused", "reason": "本书已暂停，恢复请求已取消。"}
            state, error = "paused", None
    with bridge.engine.begin() as connection:
        from hrs.library.deletion import DELETING

        book_state = connection.scalar(
            select(db.books.c.state).where(db.books.c.id == run["book_id"]).with_for_update()
        )
        if book_state is None or book_state in DELETING:
            return
        latest = (
            connection.execute(select(db.runs).where(db.runs.c.id == run["id"]).with_for_update())
            .mappings()
            .one()
        )
        result = {
            **(latest["result"] or {}),
            "codex_recovery": status,
            "bridge_failures": 0,
            "bridge_not_before": 0,
        }
        if (latest["result"] or {}).get("codex_recovery") != status or delivered:
            revision = latest["revision"] + 1
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == run["id"])
                .values(state=state, error=error, result=result, revision=revision)
            )
            if not result.get("published"):
                connection.execute(
                    update(db.books).where(db.books.c.id == run["book_id"]).values(state=state)
                )
            connection.execute(
                insert(db.events).values(
                    book_id=run["book_id"],
                    run_id=run["id"],
                    kind="run.changed",
                    payload={
                        "state": state,
                        "book_state": "ready" if result.get("published") else state,
                        "stage": run["stage"],
                        "revision": revision,
                    },
                )
            )
        if delivered:
            connection.execute(
                update(db.outbox).where(db.outbox.c.id == event["id"]).values(delivered=True)
            )
