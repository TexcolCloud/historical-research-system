import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from hrs import MODULES
from release import dependency_export_command, verify, verify_install


class ReleaseTests(unittest.TestCase):
    def test_incomplete_or_mismatched_installed_release_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root, env = base / "release", base / "environments"
            root.mkdir()
            env.mkdir()
            (root / "release.json").write_text(json.dumps({"format": "hrs-release-v1", "files": []}), encoding="utf-8")
            checked = verify(root)
            receipt = {**checked, "environment_root": str(env), "web_root": str(root / "web"), "installed_modules": list(MODULES)}
            for module in MODULES.values():
                python = env / module / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                python.parent.mkdir(parents=True)
                python.write_bytes(b"path-fixture-not-an-interpreter")
            manifest = env / "install-receipt.json"
            manifest.write_text(json.dumps(receipt), encoding="utf-8")
            self.assertTrue(verify_install(root, env)["verified"])
            for changed in ({"release_sha256": "other"}, {"installed_modules": ["adapter"]}, {"web_root": str(base)}):
                manifest.write_text(json.dumps({**receipt, **changed}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    verify_install(root, env)

    def test_export_excludes_every_local_wheel_dependency(self):
        command = dependency_export_command(Path("services/review-workbench"))
        self.assertIn("historical-document-ingestion-service", command)
        self.assertIn("hrs-runtime", command)

    def test_release_rejects_changed_missing_and_extra_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            payload = root / "payload.whl"
            payload.write_bytes(b"wheel-fixture")
            record = {"format": "hrs-release-v1", "files": [{"path": payload.name, "sha256": hashlib.sha256(payload.read_bytes()).hexdigest()}]}
            (root / "release.json").write_text(json.dumps(record), encoding="utf-8")
            self.assertTrue(verify(root)["verified"])
            environment = root / "not-a-release-member"
            with self.assertRaises((ValueError, OSError)):
                verify_install(root, environment)
            payload.write_bytes(b"wrong release")
            with self.assertRaises(ValueError):
                verify(root)
            payload.unlink()
            with self.assertRaises(ValueError):
                verify(root)
