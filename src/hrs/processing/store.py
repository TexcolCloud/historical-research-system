"""Public workflow facade. Storage, evidence and business commands have explicit owners."""

from pathlib import Path

from filelock import FileLock

from hrs.processing.checkpoints import atomic_json as atomic_json
from hrs.processing.dispatch import Dispatch
from hrs.processing.hashing import digest as digest
from hrs.processing.hashing import file_digest as file_digest
from hrs.processing.human_review_workflow import HumanReviewWorkflow
from hrs.processing.navigation_workflow import NavigationWorkflow
from hrs.processing.publication_workflow import PublicationWorkflow
from hrs.processing.run_workflow import RunWorkflow
from hrs.processing.source_import import SourceImport
from hrs.processing.task_inspection import TaskInspection
from hrs.processing.task_workflow import TaskWorkflow
from hrs.processing.workflow_context import WorkflowContext


class Store:
    def __init__(self, root: Path):
        self.context = WorkflowContext(root)
        self.checkpoints = self.context.repository
        self.root, self.lock = self.checkpoints.root, self.checkpoints.lock
        if self.checkpoints.created:
            from hrs.processing.execution_policy import configure_execution

            with self.lock:
                if not (self.root / "execution-policy.json").exists():
                    configure_execution(self.root, capacity=14)
        self.navigation = NavigationWorkflow(self.context)
        self.runs = RunWorkflow(self.context, self.navigation)
        self.publications = PublicationWorkflow(self.context, self.runs)
        self.human = HumanReviewWorkflow(
            self.context, self.navigation, self.publications, self.runs
        )
        self.source_import = SourceImport(self.context)
        self.task_inspection = TaskInspection(self.context)
        self.tasks = TaskWorkflow(self.context, self.navigation)
        self.dispatch = Dispatch(self)

    @property
    def runtime_control(self):
        from hrs.processing.runtime_control import RuntimeControl

        return RuntimeControl(self.root, self.checkpoints.ledger)

    def document_directory(self, document_id):
        return self.checkpoints.directory(document_id)

    def artifact_path(self, snapshot, relative):
        return self.checkpoints.evidence_path(snapshot, relative)

    def verify_evidence(self, snapshot, pages=None):
        return self.context.evidence.verify(snapshot, pages)

    def review_packet(self, snapshot, page):
        """Build read-only review material from an already validated snapshot."""
        return self.human.packet(snapshot, page)

    def execution_lock(self, document_id, timeout=0):
        return FileLock(self.document_directory(document_id) / ".executor.lock", timeout=timeout)

    def freeze_policy(self, document_id, policy):
        with self.lock:
            document = self.checkpoints.read(document_id)
            if not document.get("processing_policy") and document["state"] != "published":
                document["processing_policy"] = policy
                self.checkpoints.write(document)

    def recover_run(self, document_id, recovery_id, *, automatic=False):
        from hrs.processing.recovery import recover

        return recover(self.context, document_id, recovery_id, automatic=automatic)

    def status(self, document_id: str) -> dict:
        doc = self.context.repository.read(document_id)
        doc["directory"] = str(self.context.repository.directory(document_id))
        doc["counts"] = {
            state: sum(t["state"] == state for t in doc["tasks"])
            for state in ("ready", "leased", "completed", "pending", "superseded")
        }
        return doc

    def list_documents(self) -> list[dict]:
        documents = []
        for document_id in self.checkpoints.ids():
            doc = self.checkpoints.read(document_id)
            documents.append({key: doc[key] for key in ("id", "title", "state", "created_at")})
        return documents

    def register_manifest(self, manifest: dict, title: str = ""):
        return self.source_import.register_manifest(manifest, title)

    def register(
        self,
        source: Path,
        pages: list[dict],
        title: str = "",
        *,
        expected_source_sha256: str | None = None,
        original_name: str | None = None,
        media_type: str | None = None,
    ):
        return self.source_import.register(
            source,
            pages,
            title,
            expected_source_sha256=expected_source_sha256,
            original_name=original_name,
            media_type=media_type,
        )

    def original_page(
        self,
        document_id: str,
        page: int,
        *,
        task_id: str | None = None,
        lease_token: str | None = None,
    ):
        return self.task_inspection.original_page(
            document_id, page, task_id=task_id, lease_token=lease_token
        )

    def review_candidate(
        self, document_id: str, task_id: str, lease_token: str, original_reading: dict
    ):
        return self.task_inspection.review_candidate(
            document_id, task_id, lease_token, original_reading
        )

    def candidate_text(
        self, document_id: str, task_id: str, lease_token: str, *, start: int = 0, limit: int = 2000
    ):
        return self.task_inspection.candidate_text(
            document_id, task_id, lease_token, start=start, limit=limit
        )

    def get_task(self, document_id: str, task_id: str):
        return self.task_inspection.get_task(document_id, task_id)

    def approved_page_text(self, document_id: str, page: int):
        return self.task_inspection.approved_page_text(document_id, page)

    def scheduling_plan(self, document_id: str):
        return self.tasks.scheduling_plan(document_id)

    def scheduling_snapshot(self, document_id: str):
        return self.tasks.scheduling_snapshot(document_id)

    def upgrade_review_policy(self, document_id: str, protected_pages=()):
        return self.tasks.upgrade_review_policy(document_id, protected_pages)

    def report_task_failure(
        self, document_id: str, task_id: str, lease_token: str, code: str, *, details=None
    ):
        return self.tasks.report_task_failure(
            document_id, task_id, lease_token, code, details=details
        )

    def claim_task(
        self,
        document_id: str,
        stage: str,
        worker_id: str,
        lease_seconds: int = 900,
        *,
        source_first: bool = False,
        scheduled: bool = False,
    ):
        return self.tasks.claim_task(
            document_id,
            stage,
            worker_id,
            lease_seconds,
            source_first=source_first,
            scheduled=scheduled,
        )

    def submit_task(self, document_id: str, task_id: str, lease_token: str, result: dict):
        return self.tasks.submit_task(document_id, task_id, lease_token, result)

    def retry_task(self, document_id: str, task_id: str):
        return self.tasks.retry_task(document_id, task_id)

    def fallback_page(
        self, document_id: str, page: int, reason: str, *, new_evidence: str | None = None
    ):
        return self.tasks.fallback_page(document_id, page, reason, new_evidence=new_evidence)

    def refresh_navigation(self, document_id: str):
        return self.navigation.refresh_navigation(document_id)

    def navigation_decision(
        self,
        document_id: str,
        entry_id: str,
        *,
        expected_sha256: str,
        target_page: int | None,
        target_anchor: str = "",
        decision_id: str | None = None,
    ):
        return self.navigation.navigation_decision(
            document_id,
            entry_id,
            expected_sha256=expected_sha256,
            target_page=target_page,
            target_anchor=target_anchor,
            decision_id=decision_id,
        )

    def pause_document(self, document_id: str):
        return self.runs.pause_document(document_id)

    def request_run(self, document_id: str, *, resume: bool = False):
        return self.runs.request_run(document_id, resume=resume)

    def claim_run(self, owner: str, lease_seconds: int = 900, *, document_id: str | None = None):
        return self.runs.claim_run(owner, lease_seconds, document_id=document_id)

    def renew_run(self, document_id: str, token: str, lease_seconds: int = 900):
        return self.runs.renew_run(document_id, token, lease_seconds)

    def finish_run(self, document_id: str, token: str, error: str | dict | None = None):
        return self.runs.finish_run(document_id, token, error)

    def cancel_document(self, document_id: str):
        return self.runs.cancel_document(document_id)

    def forget_document(self, document_id: str):
        return self.runs.forget_document(document_id)

    def begin_revision(
        self,
        document_id: str,
        reason: str,
        pages: list[int] | None = None,
        *,
        user_errata: bool = False,
    ):
        return self.publications.begin_revision(document_id, reason, pages, user_errata=user_errata)

    def propose_revision(self, document_id: str, changes: list[dict]):
        return self.publications.propose_revision(document_id, changes)

    def publish(self, document_id: str):
        return self.publications.publish(document_id)

    def search(self, query: str, limit: int = 20):
        return self.publications.search(query, limit)

    def human_queue(self):
        return self.human.human_queue()

    def request_human_review(
        self, document_id: str, page: int, issue: str, evidence: dict, worker_id: str
    ):
        return self.human.request_human_review(document_id, page, issue, evidence, worker_id)

    def human_packet(self, document_id: str, page: int):
        return self.human.human_packet(document_id, page)

    def record_human_review(
        self,
        document_id: str,
        page: int,
        *,
        input_sha256: str,
        reviewer: str = "本地使用者",
        markdown: str,
        note: str = "本地确认",
        verdict: str,
        decision_scope: str = "content",
    ):
        return self.human.record_human_review(
            document_id,
            page,
            input_sha256=input_sha256,
            reviewer=reviewer,
            markdown=markdown,
            note=note,
            verdict=verdict,
            decision_scope=decision_scope,
        )

    def record_recovery_failure(self, document_id, expected_request, detail):
        return self.runs.record_recovery_failure(document_id, expected_request, detail)
