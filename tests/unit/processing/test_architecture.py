"""Dependency directions keep durable processing independent of transport adapters."""

import ast
from pathlib import Path

import hrs.codex
import hrs.processing


def test_processing_does_not_import_transports_or_business_storage():
    root = Path(hrs.processing.__file__).parent
    forbidden = (
        "hrs.codex",
        "hrs.api",
        "hrs.library",
        "hrs.search",
        "hrs.storage",
        "fastapi",
        "sqlalchemy",
    )
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [item.name for item in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for name in names:
                assert not any(
                    name == prefix or name.startswith(prefix + ".") for prefix in forbidden
                ), (path.name, name)


def test_execution_does_not_import_transport_entrypoints():
    path = Path(hrs.codex.__file__).parent / "execution.py"
    forbidden = {"hrs.codex.cli", "hrs.codex.mcp", "hrs.codex.workbench"}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            assert node.module not in forbidden, node.lineno


def test_domain_rules_do_not_depend_on_persistence_or_entrypoints():
    root = Path(hrs.processing.__file__).parent
    allowed = {
        "hashing",
        "contracts",
        "errors",
        "workflow_rules",
        "task_results",
        "progress",
        "navigation",
        "navigation_matching",
        "navigation_verification",
        "navigation_spans",
        "navigation_coverage",
        "page_observation",
        "source_structure",
        "organization",
        "review_locations",
        "review_contract",
    }
    for name in ("hashing", "contracts", "errors", "workflow_rules", "task_results", "progress"):
        for node in ast.walk(ast.parse((root / f"{name}.py").read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "hrs.processing."
            ):
                assert node.module.split(".")[2] in allowed, (name, node.module)


def test_processing_module_dependencies_are_acyclic_even_inside_functions():
    root = Path(hrs.processing.__file__).parent
    paths = {
        "hrs.processing." + ".".join(path.relative_to(root).with_suffix("").parts): path
        for path in root.rglob("*.py")
        if path.name != "__init__.py"
    }
    edges = {name: set() for name in paths}
    for name, path in paths.items():
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                package = name.rsplit(".", 1)[0]
                base = (
                    (
                        ".".join(package.split(".")[: len(package.split(".")) - node.level + 1])
                        + ("." + node.module if node.module else "")
                    )
                    if node.level
                    else (node.module or "")
                )
                imports = [base, *(base + "." + alias.name for alias in node.names)]
            else:
                imports = []
            edges[name].update(target for target in imports if target in paths)
    completed = set()

    def visit(name, chain):
        assert name not in chain, "Processing dependency cycle: " + " -> ".join([*chain, name])
        if name in completed:
            return
        for target in edges[name]:
            visit(target, [*chain, name])
        completed.add(name)

    for name in paths:
        visit(name, [])
