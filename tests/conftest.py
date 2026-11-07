"""Keep test runtime state outside both the checkout and the real user data directory."""

import pytest


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HRS_DATA_ROOT", str(tmp_path / "runtime-data"))
