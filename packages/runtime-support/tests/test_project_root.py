"""Installed runtime and fresh clones do not require private agent instructions."""
from pathlib import Path

import pytest

from hrs_runtime import local_vision


def test_explicit_root_supports_installed_package(monkeypatch, tmp_path):
    monkeypatch.setenv("HRS_PROJECT_ROOT", str(tmp_path))
    assert local_vision.project_root() == tmp_path.resolve()


def test_source_checkout_uses_tracked_marker(monkeypatch, tmp_path):
    monkeypatch.delenv("HRS_PROJECT_ROOT", raising=False)
    (tmp_path / ".env.platform.example").touch()
    (tmp_path / "services").mkdir()
    module = tmp_path / "packages/runtime-support/src/hrs_runtime/local_vision.py"
    monkeypatch.setattr(local_vision, "__file__", str(module))
    assert local_vision.project_root() == tmp_path


def test_private_instruction_file_does_not_define_runtime_root(monkeypatch, tmp_path):
    monkeypatch.delenv("HRS_PROJECT_ROOT", raising=False)
    (tmp_path / "AGENTS.md").touch()
    monkeypatch.setattr(local_vision, "__file__", str(tmp_path / "runtime.py"))
    monkeypatch.setattr(Path, "is_file", lambda path: path == tmp_path / "AGENTS.md")
    with pytest.raises(RuntimeError, match="HRS_PROJECT_ROOT"):
        local_vision.project_root()
