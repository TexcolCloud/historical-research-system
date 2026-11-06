"""The default application must not load legacy inference or orchestration SDKs."""

import ast
import subprocess
import sys
from pathlib import Path

import hrs.storage


def test_codex_application_imports_without_legacy_sdks():
    code = """
import sys
from importlib.abc import MetaPathFinder
class LegacyUnavailable(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'agents', 'temporalio'}:
            raise ImportError('Legacy dependency loaded by default: ' + fullname)
sys.meta_path.insert(0, LegacyUnavailable())
import hrs.api.main
import hrs.storage.bootstrap
import hrs.api.doctor
import hrs.library.exports
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_storage_transports_do_not_import_application_or_library_lifecycle():
    root = Path(hrs.storage.__file__).parent
    forbidden = ("hrs.api", "hrs.library", "hrs.processing", "hrs.codex", "hrs.search")
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else ([item.name for item in node.names] if isinstance(node, ast.Import) else [])
            )
            for name in names:
                assert not any(
                    name == prefix or name.startswith(prefix + ".") for prefix in forbidden
                ), (path.name, name)
