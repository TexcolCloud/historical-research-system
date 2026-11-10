"""Keep research contracts and deterministic projections independent of execution."""

import ast
from pathlib import Path

import pytest

import hrs.research


@pytest.mark.parametrize(
    "module",
    [
        "schemas",
        "policy",
        "packets",
        "card_validation",
        "evidence_export",
        "exporting",
        "dossier_export",
    ],
)
def test_research_contracts_and_projections_do_not_import_lifecycle_or_storage(module):
    root = Path(hrs.research.__file__).parent
    forbidden = (
        "hrs.codex",
        "hrs.api.main",
        "hrs.api.deps",
        "hrs.api.routes",
        "hrs.storage",
        "hrs.processing.store",
        "hrs.processing.ledger",
        "hrs.processing.research_workflow",
        "hrs.research.service",
        "sqlalchemy",
        "fastapi",
    )
    for node in ast.walk(ast.parse((root / f"{module}.py").read_text("utf-8"))):
        names = (
            [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else []
        )
        for name in names:
            assert not any(
                name == prefix or name.startswith(prefix + ".") for prefix in forbidden
            ), (module, name, node.lineno)
