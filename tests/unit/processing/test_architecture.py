"""Dependency directions keep durable processing independent of transport adapters."""

import ast
from pathlib import Path

import hrs.codex
import hrs.processing


def test_processing_does_not_import_transports_or_business_storage():
    root = Path(hrs.processing.__file__).parent
    forbidden = ("hrs.codex", "hrs.api", "hrs.library", "hrs.search", "hrs.storage", "fastapi", "sqlalchemy")
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = ([node.module or ""] if isinstance(node, ast.ImportFrom) else
                     [item.name for item in node.names] if isinstance(node, ast.Import) else [])
            for name in names:
                assert not any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden), (path.name, name)


def test_execution_does_not_import_transport_entrypoints():
    path = Path(hrs.codex.__file__).parent / "execution.py"
    forbidden = {"hrs.codex.cli", "hrs.codex.mcp", "hrs.codex.workbench"}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            assert node.module not in forbidden, node.lineno


def test_domain_rules_do_not_depend_on_persistence_or_entrypoints():
    root = Path(hrs.processing.__file__).parent
    allowed = {"hashing", "contracts", "errors", "workflow_rules", "task_results", "progress",
               "navigation", "organization", "review_locations", "review_contract"}
    for name in ("hashing", "contracts", "errors", "workflow_rules", "task_results", "progress"):
        for node in ast.walk(ast.parse((root / f"{name}.py").read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("hrs.processing."):
                assert node.module.split(".")[2] in allowed, (name, node.module)
