"""Golden digests copied from the pre-refactor 7e4871e implementation."""

from pathlib import Path

from hrs.library.runs.outputs import fingerprint
from hrs.processing.hashing import digest
from hrs.processing.navigation import fingerprint as navigation_fingerprint


def test_native_and_platform_json_whitespace_remains_a_distinct_protocol():
    value = {"字": ["a", 2], "flag": True}
    assert digest(value) == "3bd16f68534ff5a49dcbe0931923ba07b2e12b9a75f1521b7b17c67855f35117"
    assert (
        navigation_fingerprint(value)
        == "ef3325b7d139cfe78582811f0deb5d2292fe0940ccd0e078934d1fe72b109489"
    )
    assert fingerprint(value) == navigation_fingerprint(value)
    assert (
        fingerprint({"path": Path("source.pdf"), "missing": None})
        == "868560c8ed3fe3622da112f40e30fc920496a8663a062a2e2c76d7991576f2f4"
    )
