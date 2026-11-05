"""Dependency directions protect the public workflow from its transport adapters."""

import ast
from pathlib import Path

import hrs_codex


def test_execution_and_application_do_not_import_transport_entrypoints():
    root = Path(hrs_codex.__file__).parent
    for name in ("runner.py", "application.py", "execution.py"):
        path = root / name
        assert path.exists(), f"Missing owned workflow module: {name}"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in {
                    "hrs_codex.cli",
                    "hrs_codex.server",
                    "hrs_codex.human_review",
                }, (name, node.lineno)


def test_domain_rules_do_not_depend_on_persistence_or_entrypoints():
    root = Path(hrs_codex.__file__).parent
    allowed = {
        "hashing",
        "contracts",
        "errors",
        "workflow_rules",
        "task_results",
        "progress",
        "navigation",
        "organization",
        "review_locations",
    }
    for name in ("hashing", "contracts", "errors", "workflow_rules", "task_results", "progress"):
        for node in ast.walk(ast.parse((root / f"{name}.py").read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("hrs_codex."):
                assert node.module.split(".")[1] in allowed, (name, node.module)
