"""Native runtime configuration/restart checks; synthetic inputs do not certify content."""

import json
import tomllib

from hrs.codex.runtime_context import command
from hrs.processing.hashing import digest
from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from hrs.research.execution import ResearchRunner, role_configs


def test_reverification_role_cannot_override_a_frozen_initial_verification_model():
    legacy = role_configs({"version": 4, "models": {"verify": "frozen-verify"}})
    assert legacy["research_verifier"]["model"] == "frozen-verify"
    assert "research_reverification" not in legacy
    complete = role_configs(
        {"version": 5, "models": {"verify": "first-pass", "reverify": "final-pass"}}
    )
    assert complete["research_verifier"]["model"] == "first-pass"
    assert complete["research_reverification"]["model"] == "final-pass"


def test_frozen_roles_are_isolated_between_concurrent_policies(tmp_path):
    root = tmp_path / "native"
    Store(root)
    project = tmp_path / "project"
    project.mkdir()
    first = role_configs({"models": {"author": "frozen-model-a"}})
    second = role_configs({"models": {"author": "frozen-model-b"}})
    commands = []
    for index, roles in enumerate((first, second)):
        args, prompt, _ = command(
            "codex",
            root,
            "run",
            tmp_path / str(index),
            "gpt-6.1-sol",
            project,
            roles=roles,
            tools=["research_claim", "research_submit"],
            instructions="Dispatch only",
        )
        assert prompt == "Dispatch only"
        assert "--json" in args
        paths = [
            json.loads(arg.split("=", 1)[1])
            for arg in args
            if arg.startswith("agents.research_card_author.config_file=")
        ]
        commands.append(paths[0])
    assert commands[0] != commands[1]
    with open(commands[0], "rb") as stream:
        assert tomllib.load(stream)["model"] == "frozen-model-a"
    with open(commands[1], "rb") as stream:
        assert tomllib.load(stream)["model"] == "frozen-model-b"


def test_prelaunch_failure_has_durable_bounded_restart_budget(tmp_path, monkeypatch):
    from hrs.research import execution

    store = Store(tmp_path / "native")
    text = "Synthetic source for scheduling tests."
    workflow = ResearchWorkflow(store)
    run = workflow.enqueue(
        {
            "book_id": "synthetic-book",
            "title": "Synthetic",
            "source_run_id": "source-run",
            "document_id": "d" * 24,
            "publication_sha256": digest(text),
            "validation_scope": "content",
            "content_verified": True,
            "articles": [
                {
                    "id": "article",
                    "title": "Synthetic",
                    "text": text,
                    "text_sha256": digest(text),
                    "parts": [
                        {"start": 0, "end": len(text), "text": text, "source": {"pages": [1]}}
                    ],
                }
            ],
        }
    )

    def unavailable(*args, **kwargs):
        raise OSError("Synthetic executable unavailable")

    monkeypatch.setattr(execution, "run_research", unavailable)
    runner = ResearchRunner(store)
    for _ in range(3):
        runner.retry_at.clear()
        monkeypatch.setattr(
            runner.workflow, "pending_runs", lambda: [workflow.status(run["run_id"])]
        )
        runner.tick()
    status = workflow.status(run["run_id"])
    assert status["state"] == "technical_blocked"
    assert status["runtime_failures"] == 3
    assert status["counts"]["completed"] == 0
    workflow.resume(run["run_id"])
    assert workflow.status(run["run_id"])["runtime_failures"] == 0
