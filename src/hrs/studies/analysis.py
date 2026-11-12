"""Personal analysis jobs: frozen sources, native worker gates and explicit adoption."""

import hashlib
import time
from copy import deepcopy
from uuid import NAMESPACE_URL, uuid4, uuid5

from hrs.library.errors import ServiceError
from hrs.studies.analysis_schemas import AnalysisCandidate, AnalysisVerification

TERMINAL = {"ready", "failed", "cancelled"}
MODEL = "gpt-6.1-sol"


def worker_owner(worker_id):
    """Make personal worker reservations visible to the shared native capacity ledger."""
    return "study-worker:" + worker_id


def release_workers(store, job):
    for task in job.get("tasks", {}).values():
        if task.get("worker_id"):
            store.native_store.runtime_control.release(worker_owner(task["worker_id"]))


def public_analysis(job):
    fields = (
        "id",
        "topic_id",
        "mode",
        "material_ids",
        "prompt",
        "state",
        "suggestions",
        "error",
        "version",
        "created_at",
        "updated_at",
    )
    value = {key: deepcopy(job[key]) for key in fields if key in job}
    value["suggestions"] = [
        {**suggestion, "adopted_record_id": job.get("adoptions", {}).get(suggestion["id"])}
        for suggestion in job.get("suggestions", [])
    ]
    return value


class AnalysisService:
    def __init__(self, studies):
        self.studies, self.store = studies, studies.store

    def create(self, topic_id, request):
        topic = self.studies.topic(topic_id)
        if topic["archived"]:
            raise ServiceError(409, "请先恢复专题，再发起分析。")
        snapshots, characters = [], 0
        for material_id in dict.fromkeys(request.material_ids):
            material = self.studies.material(topic_id, material_id)
            if not material["source_current"]:
                raise ServiceError(409, "选中材料已有新版本，请核对后重新选材。")
            card = self.studies.research.workflow.card(
                material["card_id"], version=material["card_version"]
            )
            publication = self.studies.research.workflow.snapshot(card["run_id"])
            article = next(
                (a for a in publication["articles"] if a["id"] == material["article_id"]), None
            )
            if article is None:
                article = next(
                    (
                        a
                        for a in self.studies.research.workflow.sources(card["run_id"])
                        if a["id"] == material["article_id"]
                    ),
                    None,
                )
            if article is None:
                raise ServiceError(409, "选中材料的完整冻结原文不可用。")
            text = article["text"]
            characters += len(text)
            if characters > 400000:
                raise ServiceError(
                    400, "本次原文超过40万字符，请缩小选材范围；不会截断原文进行分析。"
                )
            pages = sorted(set(article.get("pages", [])))
            document_id = publication.get("document_id")
            images = {}
            for page in pages:
                _, pixels = self.store.native_store.original_page(document_id, page)
                images[str(page)] = hashlib.sha256(pixels).hexdigest()
            snapshots.append(
                {
                    **material,
                    "text": text,
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "document_id": document_id,
                    "pages": pages,
                    "images": images,
                    "evidence": material.get("evidence", []),
                }
            )
        with self.store.transaction():
            frozen = self.store.create("analysis_input", {"sources": snapshots}, topic_id=topic_id)
            job = self.store.create(
                "analysis",
                {
                    "mode": request.mode,
                    "prompt": request.prompt,
                    "material_ids": [a["id"] for a in snapshots],
                    "input_id": frozen["id"],
                    "state": "queued",
                    "suggestions": [],
                    "tasks": {},
                    "error": None,
                    "policy": {
                        "version": 1,
                        "model": MODEL,
                        "source_first": True,
                        "stage_seconds": 900,
                    },
                },
                topic_id=topic_id,
            )
        return public_analysis(job)

    def read(self, job_id):
        return public_analysis(self.store.read("analysis", job_id))

    def list(self, topic_id, *, offset=0, limit=100):
        self.studies.topic(topic_id)
        page = self.store.list("analysis", topic_id=topic_id, offset=offset, limit=limit)
        return {**page, "items": [public_analysis(item) for item in page["items"]]}

    def cancel(self, job_id, expected_version):
        # Cancellation wins over background source-read receipts advancing the revision.
        with self.store.transaction():
            job = self.store.read("analysis", job_id)
            if job["state"] in TERMINAL:
                return public_analysis(job)
            if expected_version > job["version"]:
                raise ServiceError(409, "分析版本无效，请重新读取。")
            saved = self.store.save(
                "analysis", job_id, {"state": "cancelled"}, expected_version=job["version"]
            )
            release_workers(self.store, saved)
            return public_analysis(saved)

    def adopt(self, job_id, request):
        job = self.store.read("analysis", job_id)
        if job["state"] != "ready":
            raise ServiceError(409, "分析尚未完成，不能采纳。")
        suggestion = next((s for s in job["suggestions"] if s["id"] == request.suggestion_id), None)
        if suggestion is None or not suggestion["verified"]:
            raise ServiceError(409, "这条建议尚未得到来源支持，请先核对。")
        adopted_id = job.get("adoptions", {}).get(request.suggestion_id)
        if adopted_id:
            # A lost HTTP response can be retried after the source receives a later revision.
            return self.store.read("record", adopted_id)
        refs = list(dict.fromkeys(r["material_id"] for r in suggestion["evidence_refs"]))
        frozen = {
            source["id"]: source
            for source in self.store.read("analysis_input", job["input_id"])["sources"]
        }
        for key in refs:
            material = self.studies.material(job["topic_id"], key)
            if (
                not material["source_current"]
                or material["card_version"] != frozen[key]["card_version"]
            ):
                raise ServiceError(409, "来源已变化，请重新核对建议；旧建议和引用仍保留。")
        record_id = uuid5(NAMESPACE_URL, f"study-analysis:{job_id}:{request.suggestion_id}").hex
        with self.store.transaction():
            latest = self.store.read("analysis", job_id)
            adopted = latest.get("adoptions", {})
            if request.suggestion_id in adopted:
                return self.store.read("record", adopted[request.suggestion_id])
            if latest["version"] != request.expected_version:
                raise ServiceError(409, "分析已有新版本，请重新读取后采纳。")
            record = self.studies.create_record(
                job["topic_id"],
                {
                    "kind": request.kind,
                    "title": request.title or suggestion["title"],
                    "text": suggestion["text"],
                    "limitations": suggestion["limitations"],
                    "source_refs": refs,
                },
                origin="ai_adopted",
                analysis_id=job_id,
                item_id=record_id,
            )
            self.store.save(
                "analysis",
                job_id,
                {"adoptions": {**adopted, request.suggestion_id: record_id}},
                expected_version=latest["version"],
            )
        return record


class AnalysisWorkflow:
    """Native workers see frozen sources; candidates stay hidden until independent reading."""

    def __init__(self, store):
        self.store, self.native = store, store.native_store

    def _sources(self, job):
        return self.store.read("analysis_input", job["input_id"])["sources"]

    def _change(self, job_id, operation):
        with self.store.transaction():
            job = self.store.read("analysis", job_id)
            changes, result = operation(deepcopy(job))
            saved = self.store.save("analysis", job_id, changes, expected_version=job["version"])
            return result if result is not None else public_analysis(saved)

    def begin(self, job_id, execution_id):
        def change(job):
            if job["state"] != "queued":
                raise ServiceError(409, "分析已经开始或已停止。")
            return {
                "state": "running",
                "execution_id": execution_id,
                "started_at": time.time(),
                "tasks": {},
                "execution_attempt": job.get("execution_attempt", 0) + 1,
            }, None

        return self._change(job_id, change)

    def attest(self, job_id, worker_id, thread_id):
        def change(job):
            prefix = f"{job_id}-{job['execution_id']}-"
            stage = worker_id.removeprefix(prefix)
            if not worker_id.startswith(prefix) or stage not in {"author", "verify"}:
                raise ServiceError(409, "原生工作者身份不属于当前执行。")
            tasks = job["tasks"]
            if any(t.get("thread_id") == thread_id and k != stage for k, t in tasks.items()):
                raise ServiceError(409, "独立核验必须使用新的原生子代理。")
            if stage in tasks and tasks[stage].get("thread_id") != thread_id:
                raise ServiceError(409, "同一阶段不能替换已绑定的原生工作者。")
            tasks[stage] = {**tasks.get(stage, {}), "worker_id": worker_id, "thread_id": thread_id}
            return {"tasks": tasks}, None

        return self._change(job_id, change)

    def claim(self, job_id, worker_id, stage):
        def change(job):
            expected = {"author": "running", "verify": "verification"}.get(stage)
            task = job["tasks"].get(stage, {})
            if (
                expected is None
                or job["state"] != expected
                or task.get("worker_id") != worker_id
                or not task.get("thread_id")
            ):
                raise ServiceError(409, "等待宿主绑定当前原生子代理，或阶段尚未就绪。")
            owner = worker_owner(worker_id)
            deadline = task.get("deadline", time.time() + 900)
            if deadline <= time.time():
                raise ServiceError(409, "分析阶段已过期，请重新发起分析。")
            # Retries reuse a live reservation, including its probe generation and deadline.
            renewed = (
                task.get("token")
                and self.native.runtime_control.gate(MODEL)["state"] == "normal"
                and self.native.runtime_control.renew(MODEL, owner, expires_at=deadline + 30)
            )
            if not renewed:
                self.native.runtime_control.release(owner)
                admission = self.native.runtime_control.admit(
                    MODEL,
                    owner,
                    occupied=self.store.ledger.model_capacity_used(MODEL),
                    total_occupied=self.store.ledger.capacity_used(),
                    lease_seconds=max(1, deadline + 30 - time.time()),
                    priority="research",
                )
                if not admission["allowed"]:
                    return {}, {
                        "wait": True,
                        "reason": admission.get("reason"),
                        "retry_at": admission.get("retry_at"),
                    }
            try:
                token = task.get("token") or uuid4().hex
                job["tasks"][stage] = {
                    **task,
                    "token": token,
                    "reads": task.get("reads", {}),
                    "originals": task.get("originals", {}),
                    "deadline": deadline,
                }
                return {"tasks": job["tasks"]}, {
                    "token": token,
                    "stage": stage,
                    "mode": job["mode"],
                    "prompt": job.get("prompt", ""),
                    "sources": [
                        {k: s.get(k) for k in ("id", "title", "text_sha256", "pages")}
                        | {"length": len(s["text"])}
                        for s in self._sources(job)
                    ],
                    "result_schema": (
                        AnalysisCandidate if stage == "author" else AnalysisVerification
                    ).model_json_schema(),
                    "rules": "Read every complete source. Verify opens every source page before candidate. Preserve attribution, modality, uncertainty and source limits. Suggestions are personal analysis, never approval or proven historical truth.",
                }

            except BaseException:
                self.native.runtime_control.release(owner)
                raise

        return self._change(job_id, change)

    @staticmethod
    def _task(job, token, worker):
        stage = (
            "author"
            if job["state"] == "running"
            else "verify"
            if job["state"] == "verification"
            else None
        )
        task = job["tasks"].get(stage, {})
        if (
            task.get("token") != token
            or task.get("worker_id") != worker
            or task.get("deadline", 0) < time.time()
        ):
            raise ServiceError(409, "分析阶段已停止、过期或工作者不匹配。")
        return stage, task

    def _source(self, job, material_id):
        source = next((s for s in self._sources(job) if s["id"] == material_id), None)
        if source is None:
            raise ServiceError(404, "原文不属于本次冻结选材。")
        return source

    def source(self, job_id, token, worker, material_id, offset=0, limit=4000):
        def change(job):
            stage, task = self._task(job, token, worker)
            source = self._source(job, material_id)
            if (
                type(offset) is not int
                or not 0 <= offset <= len(source["text"])
                or not 1 <= limit <= 8000
            ):
                raise ServiceError(400, "原文读取范围无效。")
            end = min(len(source["text"]), offset + limit)
            task["reads"].setdefault(material_id, []).append([offset, end])
            return {"tasks": job["tasks"]}, {
                "id": material_id,
                "text": source["text"][offset:end],
                "start": offset,
                "end": end,
                "total": len(source["text"]),
                "text_sha256": source["text_sha256"],
                "evidence": source.get("evidence", []) if offset == 0 else [],
            }

        return self._change(job_id, change)

    def original(self, job_id, token, worker, material_id, page):
        job = self.store.read("analysis", job_id)
        self._task(job, token, worker)
        source = self._source(job, material_id)
        if page not in source["pages"]:
            raise ServiceError(400, "原图页不属于冻结原文。")
        metadata, data = self.native.original_page(source["document_id"], page)
        actual = hashlib.sha256(data).hexdigest()
        if actual != source.get("images", {}).get(str(page)):
            raise ServiceError(409, "冻结原图哈希已变化。")

        def change(latest):
            _, task = self._task(latest, token, worker)
            pages = task["originals"].setdefault(material_id, [])
            if page not in pages:
                pages.append(page)
            return {"tasks": latest["tasks"]}, None

        self._change(job_id, change)
        return metadata, data

    def _read_complete(self, job, task, *, originals=False):
        for source in self._sources(job):
            covered = 0
            for start, end in sorted(task.get("reads", {}).get(source["id"], [])):
                if start > covered:
                    break
                covered = max(covered, end)
            if covered < len(source["text"]):
                raise ServiceError(409, "须完整阅读每份冻结原文后继续。")
            if originals and set(source["pages"]) - set(
                task.get("originals", {}).get(source["id"], [])
            ):
                raise ServiceError(409, "独立核验须先查看每份来源的原件页面。")

    def candidate(self, job_id, token, worker, observations):
        def change(job):
            stage, task = self._task(job, token, worker)
            if stage != "verify":
                raise ServiceError(409, "只有独立核验者可以读取候选。")
            self._read_complete(job, task, originals=True)
            observed = {o.get("material_id"): o for o in observations}
            if set(observed) != {s["id"] for s in self._sources(job)} or any(
                observed[s["id"]].get("text_sha256") != s["text_sha256"]
                or not str(observed[s["id"]].get("summary", "")).strip()
                for s in self._sources(job)
            ):
                raise ServiceError(409, "须提交所有来源的独立观察和准确文本哈希。")
            if task.get("observations") and task["observations"] != observations:
                raise ServiceError(409, "候选曝光后不能改写独立来源观察。")
            task["observations"] = observations
            return {"tasks": job["tasks"]}, job["candidate"]

        return self._change(job_id, change)

    def submit(self, job_id, token, worker, result):
        def change(job):
            stage, task = self._task(job, token, worker)
            self._read_complete(job, task, originals=stage == "verify")
            if stage == "author":
                candidate = AnalysisCandidate.model_validate(result).model_dump()
                ids = [s["id"] for s in candidate["suggestions"]]
                if len(set(ids)) != len(ids):
                    raise ServiceError(400, "建议编号必须唯一。")
                valid = {
                    (s["id"], e["id"]) for s in self._sources(job) for e in s.get("evidence", [])
                }
                if any(
                    (r["material_id"], r["evidence_id"]) not in valid
                    for s in candidate["suggestions"]
                    for r in s["evidence_refs"]
                ):
                    raise ServiceError(409, "引用不属于冻结材料及其证据编号。")
                return {"state": "verification", "candidate": candidate}, None
            if not task.get("observations"):
                raise ServiceError(409, "须先提交独立观察并读取候选。")
            checks = AnalysisVerification.model_validate(result).model_dump()["checks"]
            suggestions = job["candidate"]["suggestions"]
            by_id = {c["suggestion_id"]: c for c in checks}
            if len(by_id) != len(checks) or set(by_id) != {s["id"] for s in suggestions}:
                raise ServiceError(409, "须逐项核验所有建议。")
            if any(not c["supported"] and not c["issues"] for c in checks):
                raise ServiceError(409, "未获支持的建议须说明具体问题。")
            if any(c["supported"] and c["issues"] for c in checks):
                raise ServiceError(409, "存在未解决问题的建议不能标为已获来源支持。")
            return {
                "state": "ready",
                "suggestions": [
                    {
                        **s,
                        "verified": by_id[s["id"]]["supported"],
                        "issues": by_id[s["id"]]["issues"],
                    }
                    for s in suggestions
                ],
                "verification": checks,
            }, None

        with self.store.transaction():
            answer = self._change(job_id, change)
            self.native.runtime_control.success(
                MODEL, worker_owner(worker), f"study:{job_id}:{token}:submitted"
            )
            return answer
