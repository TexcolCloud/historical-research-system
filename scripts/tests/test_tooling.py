import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import hrs
from engineering_tests import pytest_collection_modifyitems


class ToolingTests(unittest.TestCase):
    def test_installed_release_does_not_shadow_its_runtime_with_checkout_sources(self):
        with patch.dict(os.environ, {"HRS_ENV_ROOT": "isolated-environments", "PYTHONPATH": ""}):
            self.assertNotIn("runtime-support", hrs.child_env()["PYTHONPATH"])

    def test_junit_missing_empty_skipped_and_failed_are_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "suite.xml"
            self.assertFalse(hrs.test_result("frontend", 0, report)["passed"])
            for counts in ['tests="0"', 'tests="2" skipped="1"', 'tests="2" failures="1"', 'tests="2" errors="1"']:
                report.write_text(f'<testsuites><testsuite {counts}/></testsuites>')
                self.assertFalse(hrs.test_result("frontend", 0, report)["passed"])
            report.write_text('<testsuite tests="2"/>')
            self.assertTrue(hrs.test_result("frontend", 0, report)["passed"])

    def test_tiers_explicitly_exclude_direct_and_parameterized_s3(self):
        def item(fixtures, callspec=None):
            return SimpleNamespace(fixturenames=fixtures, callspec=callspec,
                                   path=Path("test_storage.py"), keywords={}, name="storage")
        direct = item(["isolated_database_url", "s3_options"])
        indirect = item(["isolated_database_url"], SimpleNamespace(params={"storage_runtime": "s3"}))
        database = item(["isolated_database_url"])
        unit = item([])
        with patch.dict(os.environ, {"HRS_TEST_TIER": "integration"}):
            hook = Mock()
            config = SimpleNamespace(hook=hook)
            items = [direct, indirect, database, unit]
            pytest_collection_modifyitems(config, items)
            self.assertEqual(items, [database, unit])
            hook.pytest_deselected.assert_called_once_with(items=[direct, indirect])
    def test_local_runner_rejects_remote_origins_and_url_credentials(self):
        for value in ["http://example.org", "http://user:pass@localhost", "http://localhost/a", "http://localhost?key=secret"]:
            with patch.dict(os.environ, {"WORKBENCH_CARDS_ORIGIN": value}):
                with self.assertRaises(ValueError):
                    hrs.origin("cards")
        with patch.dict(os.environ, {"WORKBENCH_CARDS_ORIGIN": "http://127.0.0.1:23456"}):
            self.assertEqual(hrs.origin("cards"), "http://127.0.0.1:23456")

    def test_down_only_signals_its_exact_owned_stop_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(hrs, "ROOT", Path(directory)):
            state = Path(directory) / "state/engineering"
            state.mkdir(parents=True)
            (state / "supervisor.lock").write_text('{"stop_file":"../other"}')
            with self.assertRaises(ValueError):
                hrs.down()
            name = "a" * 32 + ".stop"
            (state / "supervisor.lock").write_text('{"stop_file":"' + name + '"}')
            self.assertEqual(hrs.down(), 0)
            self.assertTrue((state / name).is_file())
            self.assertFalse((state.parent / "other").exists())

    def test_integration_requires_explicit_database_selection_before_running(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(hrs, "run") as run:
            with self.assertRaisesRegex(ValueError, "INGEST_TEST_DATABASE_URL"):
                hrs.test(["ingestion"], "integration", Path("must-not-be-created"))
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
