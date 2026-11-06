"""A source release is built from a real repository plus its compiled UI only."""

import hashlib
import json
import subprocess
from zipfile import ZipFile

import pytest

from hrs import __version__
from hrs.release import bundle


def git(project, *arguments):
    return subprocess.check_output(["git", *arguments], cwd=project, text=True).strip()


def write(project, name, data):
    path = project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def repository(tmp_path):
    project = tmp_path / "source repository"
    project.mkdir()
    git(project, "init", "-q")
    files = {
        "README.md": b"Source deployment fixture\n",
        "pyproject.toml": b'[project]\nname="release-fixture"\nversion="0.0.0"\n',
        "uv.lock": b"version = 1\n",
        ".python-version": b"3.11\n",
        ".gitignore": b".env\n.env.*\n!.env.example\nmodels/\nstate/\nprivate/\nweb/dist/\n",
        ".gitattributes": b"* text=auto eol=lf\n",
        ".env.example": b"INGEST_DEV_DB_PASSWORD=\nINGEST_S3_ACCESS_KEY=\nINGEST_S3_SECRET_KEY=\n",
        "src/hrs/__main__.py": b"print('entry fixture')\n",
        "src/hrs/codex/workflow.md": b"Source-first runtime contract fixture\n",
        ".codex/config.toml": b'[mcp_servers.fixture]\ncommand="uv"\n',
        ".agents/skills/historical-research/SKILL.md": b"Runtime skill fixture\n",
        "web/package.json": b'{"name":"release-fixture","private":true}\n',
        "deploy/compose.storage.yml": b"services: {}\n",
        "tests/test_smoke.py": b"def test_fixture(): assert True\n",
    }
    for name, data in files.items():
        write(project, name, data)
    git(project, "add", ".")
    git(
        project,
        "-c",
        "user.name=Release Test",
        "-c",
        "user.email=release@example.invalid",
        "commit",
        "-qm",
        "temporary release fixture",
    )
    write(project, "web/dist/index.html", b"<html><script src='/assets/app.js'></script></html>")
    write(project, "web/dist/assets/app.js", b"document.body.dataset.release='fixture';")
    return project, files


def test_bundle_contains_source_runtime_and_compiled_ui_with_exact_manifest_hashes(
    repository, tmp_path
):
    project, source = repository
    private = b"RELEASE_PRIVATE_SENTINEL"
    for name in (".env", "models/weight.bin", "state/ledger.sqlite3", "private/notes.json"):
        write(project, name, private)
    destination = tmp_path / "source-release.zip"
    receipt = bundle(project, destination)
    assert receipt["bundle"] == str(destination)
    assert receipt["source_revision"] == git(project, "rev-parse", "HEAD")
    assert receipt["working_tree_modified"] is False
    with ZipFile(destination) as archive:
        manifest = json.loads(archive.read("release-manifest.json"))
        members = set(archive.namelist())
        assert members == {*manifest["files"], "release-manifest.json"}
        assert set(source).issubset(members)
        assert {"web/dist/index.html", "web/dist/assets/app.js"}.issubset(members)
        assert not members.intersection(
            {".env", "models/weight.bin", "state/ledger.sqlite3", "private/notes.json"}
        )
        assert private not in b"".join(archive.read(name) for name in members)
        assert manifest["kind"] == "hrs-source-deployment"
        assert manifest["version"] == __version__ == receipt["version"]
        for name, expected in manifest["files"].items():
            content = archive.read(name)
            assert expected == {
                "sha256": hashlib.sha256(content).hexdigest(),
                "byte_length": len(content),
            }
            assert content == (project / name).read_bytes()


def test_bundle_records_actual_uncommitted_source_and_refuses_overwriting_an_artifact(
    repository, tmp_path
):
    project, _ = repository
    changed = b"print('reviewable pending fix')\n"
    write(project, "src/hrs/__main__.py", changed)
    destination = tmp_path / "review.zip"
    receipt = bundle(project, destination)
    assert receipt["working_tree_modified"] is True
    with ZipFile(destination) as archive:
        assert archive.read("src/hrs/__main__.py") == changed
        assert json.loads(archive.read("release-manifest.json"))["working_tree_modified"] is True
    before = destination.read_bytes()
    with pytest.raises(FileExistsError):
        bundle(project, destination)
    assert destination.read_bytes() == before


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        "models/weights.bin",
        "state/private.sqlite3",
        "web/.env.local",
        "web/dist/.env.production",
    ],
)
def test_private_files_are_rejected_even_when_forced_into_git_or_the_build(
    repository, tmp_path, name
):
    project, _ = repository
    write(project, name, b"RELEASE_PRIVATE_SENTINEL")
    if not name.startswith("web/dist/"):
        git(project, "add", "--force", name)
    destination = tmp_path / "must-not-exist.zip"
    with pytest.raises(ValueError):
        bundle(project, destination)
    assert not destination.exists()


@pytest.mark.parametrize(
    "name", ["src/hrs/codex/workflow.md", ".codex/config.toml", "web/dist/index.html"]
)
def test_missing_application_resources_fail_before_creating_an_archive(repository, tmp_path, name):
    project, _ = repository
    (project / name).unlink()
    destination = tmp_path / "must-not-exist.zip"
    with pytest.raises(ValueError):
        bundle(project, destination)
    assert not destination.exists()


def test_bundle_refuses_a_source_symlink_outside_the_repository(repository, tmp_path):
    project, _ = repository
    outside = tmp_path / "private.py"
    outside.write_bytes(b"RELEASE_PRIVATE_SENTINEL")
    linked = project / "src/hrs/outside.py"
    try:
        linked.symlink_to(outside)
    except OSError:
        pytest.skip("This environment does not allow creating symlinks.")
    git(project, "add", linked.relative_to(project).as_posix())
    with pytest.raises(ValueError, match="escapes"):
        bundle(project, tmp_path / "must-not-exist.zip")
