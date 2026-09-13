import tempfile
import unittest
from pathlib import Path

from bundles import inventory, member, seal, verify


class BundleTests(unittest.TestCase):
    def test_complete_component_set_and_file_hashes_are_required_before_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for module in ("ingestion", "retrieval", "cards", "access"):
                (root / module).mkdir()
                (root / module / "native-backup.bin").write_bytes(module.encode())
            seal(root, {module: module for module in ("ingestion", "retrieval", "cards", "access")},
                 quiescence_receipt="offline-test-1")
            self.assertTrue(verify(root)["verified"])
            with self.assertRaises(ValueError):
                member(root, "../outside")
            with self.assertRaises(ValueError):
                inventory(root, {name: "ingestion" for name in ("ingestion", "retrieval", "cards", "access")})
            original = (root / "ingestion/native-backup.bin").read_bytes()
            (root / "ingestion/native-backup.bin").unlink()
            with self.assertRaises(ValueError):
                verify(root)
            (root / "ingestion/native-backup.bin").write_bytes(original)
            extra = root / "ingestion/unpaired.bin"
            extra.write_bytes(b"unpaired")
            with self.assertRaises(ValueError):
                verify(root)
            extra.unlink()
            (root / "ingestion/native-backup.bin").write_bytes(b"other recovery point")
            with self.assertRaisesRegex(ValueError, "integrity"):
                verify(root)
