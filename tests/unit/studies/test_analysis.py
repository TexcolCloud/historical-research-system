import hashlib
from types import SimpleNamespace

import pytest

from hrs.library.errors import ServiceError
from hrs.processing.store import Store
from hrs.studies.analysis import AnalysisService, AnalysisWorkflow
from hrs.studies.analysis_schemas import AnalysisAdoption, AnalysisCandidate, AnalysisVerification
from hrs.studies.store import StudyStore


def test_verification_is_a_fresh_source_first_native_worker(tmp_path):
    native = Store(tmp_path / "native")
    store = StudyStore(native)
    topic = store.create("topic", {"title": "个人专题"})
    frozen = store.create(
        "analysis_input",
        {
            "sources": [
                {
                    "id": "m",
                    "title": "材料",
                    "text": "原文只记载计划。",
                    "text_sha256": "hash",
                    "pages": [],
                    "evidence": [{"id": "e1", "quote": "计划"}],
                }
            ],
        },
        topic_id=topic["id"],
    )
    job = store.create(
        "analysis",
        {
            "mode": "summary",
            "prompt": "",
            "material_ids": ["m"],
            "state": "queued",
            "input_id": frozen["id"],
            "suggestions": [],
            "tasks": {},
        },
        topic_id=topic["id"],
    )
    flow = AnalysisWorkflow(store)
    flow.begin(job["id"], "execution")
    author = f"{job['id']}-execution-author"
    verifier = f"{job['id']}-execution-verify"
    with pytest.raises(ServiceError):
        flow.claim(job["id"], author, "author")
    flow.attest(job["id"], author, "thread-one")
    claim = flow.claim(job["id"], author, "author")
    with pytest.raises(ServiceError):
        flow.submit(job["id"], claim["token"], author, {"suggestions": []})
    flow.source(job["id"], claim["token"], author, "m", 0, 4000)
    flow.submit(
        job["id"],
        claim["token"],
        author,
        {
            "suggestions": [
                {
                    "id": "s",
                    "title": "材料边界",
                    "text": "材料记载计划。",
                    "limitations": "不能证明已经实施。",
                    "evidence_refs": [{"material_id": "m", "evidence_id": "e1"}],
                }
            ]
        },
    )
    with pytest.raises(ServiceError):
        flow.attest(job["id"], verifier, "thread-one")
    flow.attest(job["id"], verifier, "thread-two")
    check = flow.claim(job["id"], verifier, "verify")
    with pytest.raises(ServiceError):
        flow.candidate(job["id"], check["token"], verifier, [])
    flow.source(job["id"], check["token"], verifier, "m", 0, 4000)
    flow.candidate(
        job["id"],
        check["token"],
        verifier,
        [{"material_id": "m", "text_sha256": "hash", "summary": "只记载计划，未证实实施。"}],
    )
    done = flow.submit(
        job["id"],
        check["token"],
        verifier,
        {"checks": [{"suggestion_id": "s", "supported": True, "issues": []}]},
    )
    assert done["state"] == "ready" and done["suggestions"][0]["verified"] is True
    assert "snapshot" not in store.read("analysis", job["id"])


@pytest.fixture
def native_job(tmp_path, monkeypatch):
    native = Store(tmp_path / "native")
    store = StudyStore(native)
    topic = store.create("topic", {"title": "Synthetic source-first test"})
    pixels = {1: b"original page one", 2: b"original page two"}
    monkeypatch.setattr(
        native, "original_page", lambda document, page: ({"page": page}, pixels[page])
    )
    source = {
        "id": "m",
        "card_version": 1,
        "title": "Frozen source",
        "text": "甲" * 10003,
        "text_sha256": "source-hash",
        "document_id": "document",
        "pages": [1, 2],
        "images": {str(page): hashlib.sha256(data).hexdigest() for page, data in pixels.items()},
        "evidence": [{"id": "e1", "quote": "甲"}],
    }
    frozen = store.create("analysis_input", {"sources": [source]}, topic_id=topic["id"])
    candidate = {
        "suggestions": [
            {
                "id": "s",
                "title": "个人判断",
                "text": "有限解读。",
                "limitations": "不能证明结果。",
                "evidence_refs": [{"material_id": "m", "evidence_id": "e1"}],
            }
        ]
    }
    job = store.create(
        "analysis",
        {
            "mode": "summary",
            "state": "queued",
            "tasks": {},
            "input_id": frozen["id"],
            "suggestions": [],
        },
        topic_id=topic["id"],
    )
    flow = AnalysisWorkflow(store)
    flow.begin(job["id"], "execution")
    author = f"{job['id']}-execution-author"
    flow.attest(job["id"], author, "author-thread")
    claim = flow.claim(job["id"], author, "author")
    return SimpleNamespace(
        native=native,
        store=store,
        topic=topic,
        job_id=job["id"],
        flow=flow,
        author=author,
        token=claim["token"],
        candidate=candidate,
        pixels=pixels,
    )


def test_cancel_accepts_prior_ui_version_after_native_source_receipts(native_job):
    case = native_job
    prior = case.store.read("analysis", case.job_id)["version"]
    case.flow.source(case.job_id, case.token, case.author, "m", 0, 5000)
    case.flow.source(case.job_id, case.token, case.author, "m", 5000, 5003)
    assert case.store.read("analysis", case.job_id)["version"] > prior
    service = AnalysisService(SimpleNamespace(store=case.store))
    cancelled = service.cancel(case.job_id, prior)
    assert cancelled["state"] == "cancelled"
    assert case.store.ledger.capacity_used() == 0
    with pytest.raises(ServiceError):
        case.flow.submit(case.job_id, case.token, case.author, case.candidate)


def test_verifier_requires_complete_text_all_originals_and_frozen_observations(native_job):
    case = native_job
    case.flow.source(case.job_id, case.token, case.author, "m", 0, 5000)
    case.flow.source(case.job_id, case.token, case.author, "m", 5001, 5003)
    with pytest.raises(ServiceError, match="完整阅读"):
        case.flow.submit(case.job_id, case.token, case.author, case.candidate)
    case.flow.source(case.job_id, case.token, case.author, "m", 5000, 1)
    case.flow.submit(case.job_id, case.token, case.author, case.candidate)
    verifier = f"{case.job_id}-execution-verify"
    with pytest.raises(ServiceError, match="新的原生子代理"):
        case.flow.attest(case.job_id, verifier, "author-thread")
    case.flow.attest(case.job_id, verifier, "verification-thread")
    token = case.flow.claim(case.job_id, verifier, "verify")["token"]
    for offset in (0, 5000):
        case.flow.source(case.job_id, token, verifier, "m", offset, 6000)
    observations = [{"material_id": "m", "text_sha256": "source-hash", "summary": "独立来源观察。"}]
    case.flow.original(case.job_id, token, verifier, "m", 1)
    with pytest.raises(ServiceError, match="原件页面"):
        case.flow.candidate(case.job_id, token, verifier, observations)
    case.pixels[2] = b"changed original"
    with pytest.raises(ServiceError, match="哈希"):
        case.flow.original(case.job_id, token, verifier, "m", 2)
    case.pixels[2] = b"original page two"
    case.flow.original(case.job_id, token, verifier, "m", 2)
    assert case.flow.candidate(case.job_id, token, verifier, observations) == case.candidate
    with pytest.raises(ServiceError, match="不能改写"):
        case.flow.candidate(case.job_id, token, verifier, [{**observations[0], "summary": "改写"}])
    case.flow.submit(
        case.job_id,
        token,
        verifier,
        {"checks": [{"suggestion_id": "s", "supported": True, "issues": []}]},
    )
    assert case.store.ledger.capacity_used() == 0


def test_successful_adoption_retry_survives_later_source_revision(native_job):
    case = native_job
    job = case.store.read("analysis", case.job_id)
    job = case.store.save(
        "analysis",
        case.job_id,
        {
            "state": "ready",
            "suggestions": [{**case.candidate["suggestions"][0], "verified": True, "issues": []}],
        },
        expected_version=job["version"],
    )
    material = {"source_current": True, "card_version": 1}

    def create_record(topic, value, **context):
        return case.store.create(
            "record",
            {**value, "origin": context["origin"], "analysis_id": context["analysis_id"]},
            topic_id=topic,
            item_id=context["item_id"],
        )

    service = AnalysisService(
        SimpleNamespace(
            store=case.store, material=lambda *args: material, create_record=create_record
        )
    )
    request = AnalysisAdoption(expected_version=job["version"], suggestion_id="s")
    material["source_current"] = False
    with pytest.raises(ServiceError, match="来源已变化"):
        service.adopt(case.job_id, request)
    assert case.store.list("record", topic_id=case.topic["id"])["total"] == 0
    material["source_current"] = True
    adopted = service.adopt(case.job_id, request)
    material["source_current"] = False
    assert service.adopt(case.job_id, request)["id"] == adopted["id"]
    assert case.store.list("record", topic_id=case.topic["id"])["total"] == 1


@pytest.fixture
def personal_analysis(native_job):
    case = native_job
    job = case.store.read("analysis", case.job_id)
    job = case.store.save(
        "analysis",
        case.job_id,
        {
            "state": "ready",
            "suggestions": [{**case.candidate["suggestions"][0], "verified": True, "issues": []}],
        },
        expected_version=job["version"],
    )

    def create_record(topic, value, **context):
        return case.store.create(
            "record",
            {**value, "origin": context["origin"], "analysis_id": context["analysis_id"]},
            topic_id=topic,
            item_id=context["item_id"],
        )

    service = AnalysisService(
        SimpleNamespace(
            store=case.store,
            material=lambda *args: {"source_current": True, "card_version": 1},
            create_record=create_record,
        )
    )
    return case, service, job


def test_ignored_suggestion_is_durable_reversible_and_not_adoptable(personal_analysis):
    case, service, job = personal_analysis
    ignored = service.disposition(case.job_id, "s", job["version"], ignored=True)
    assert ignored["suggestions"][0]["ignored"] is True
    assert service.read(case.job_id)["suggestions"][0]["ignored"] is True
    assert case.store.read("analysis", case.job_id)["suggestions"] == job["suggestions"]
    with pytest.raises(ServiceError, match="忽略"):
        service.adopt(
            case.job_id, AnalysisAdoption(expected_version=ignored["version"], suggestion_id="s")
        )
    restored = service.disposition(case.job_id, "s", ignored["version"], ignored=False)
    assert restored["suggestions"][0]["ignored"] is False
    adopted = service.adopt(
        case.job_id, AnalysisAdoption(expected_version=restored["version"], suggestion_id="s")
    )
    assert adopted["origin"] == "ai_adopted"


def test_edited_adoption_preserves_verified_suggestion_and_source_references(personal_analysis):
    case, service, job = personal_analysis
    request = AnalysisAdoption(
        expected_version=job["version"],
        suggestion_id="s",
        title="我的限定解读",
        text="个人补充：这一计划的实施仍需其他资料。",
        limitations="仅作研究札记，尚不能证明实施。",
    )
    record = service.adopt(case.job_id, request)
    assert record["text"] == request.text and record["limitations"] == request.limitations
    assert record["source_refs"] == ["m"] and record["analysis_id"] == case.job_id
    assert case.store.read("analysis", case.job_id)["suggestions"] == job["suggestions"]
    assert service.adopt(case.job_id, request)["id"] == record["id"]


def test_suggestion_disposition_retries_do_not_rewrite_history_or_override_newer_choices(
    personal_analysis,
):
    case, service, job = personal_analysis
    ignored = service.disposition(case.job_id, "s", job["version"], ignored=True)
    retry = service.disposition(case.job_id, "s", job["version"], ignored=True)
    assert retry["version"] == ignored["version"]
    with pytest.raises(ServiceError, match="状态已变化"):
        service.disposition(case.job_id, "s", job["version"], ignored=False)
    with pytest.raises(ServiceError, match="版本无效"):
        service.disposition(case.job_id, "s", ignored["version"] + 1, ignored=True)
    with pytest.raises(ServiceError, match="没有这条"):
        service.disposition(case.job_id, "missing", ignored["version"], ignored=True)
    restored = service.disposition(case.job_id, "s", ignored["version"], ignored=False)
    service.adopt(
        case.job_id, AnalysisAdoption(expected_version=restored["version"], suggestion_id="s")
    )
    with pytest.raises(ServiceError, match="已采纳"):
        service.disposition(case.job_id, "s", service.read(case.job_id)["version"], ignored=True)


@pytest.mark.parametrize("field", ["title", "text", "limitations"])
def test_candidate_rejects_blank_analysis_claims_and_limits(native_job, field):
    candidate = native_job.candidate
    candidate["suggestions"][0][field] = "  "
    with pytest.raises(ValueError):
        AnalysisCandidate.model_validate(candidate)


def test_verification_rejects_empty_issue_explanations():
    with pytest.raises(ValueError):
        AnalysisVerification.model_validate(
            {"checks": [{"suggestion_id": "s", "supported": False, "issues": ["  "]}]}
        )


def test_successful_native_submission_advances_shared_recovery_probe(native_job, monkeypatch):
    case = native_job
    runtime = case.native.runtime_control
    retry = runtime.failure("rate_limit", "synthetic-model-pressure", model="gpt-6.1-sol")[
        "retry_at"
    ]
    monkeypatch.setattr("time.time", lambda: retry + 1)
    token = case.flow.claim(case.job_id, case.author, "author")["token"]
    for offset in (0, 5000):
        case.flow.source(case.job_id, token, case.author, "m", offset, 6000)
    case.flow.submit(case.job_id, token, case.author, case.candidate)
    assert runtime.snapshot()["models"]["gpt-6.1-sol"]["state"] == "recovering"
    assert case.store.ledger.capacity_used() == 0
