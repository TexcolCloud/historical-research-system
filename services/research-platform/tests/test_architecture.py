"""The default application must not load legacy inference or orchestration SDKs."""

import subprocess
import sys


def test_codex_application_imports_without_legacy_sdks():
    code = """
import sys
from importlib.abc import MetaPathFinder
class LegacyUnavailable(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'agents', 'temporalio'}:
            raise ImportError('Legacy dependency loaded by default: ' + fullname)
sys.meta_path.insert(0, LegacyUnavailable())
import hrs_platform.main
import hrs_platform.bootstrap
import hrs_platform.doctor
import hrs_platform.services.exports
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
