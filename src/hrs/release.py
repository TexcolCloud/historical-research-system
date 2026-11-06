"""Build a source deployment with compiled UI and an explicit, credential-free manifest."""

import hashlib
import json
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from hrs import __version__


def bundle(project, destination):
    project = Path(project).resolve()
    destination = (
        Path(destination).expanduser().resolve()
        if destination
        else project / "dist" / f"historical-research-system-{__version__}.zip"
    )
    distribution = project / "web/dist"
    if not (distribution / "index.html").is_file():
        raise ValueError("Build the frontend before creating a deployment bundle.")
    tracked = (
        subprocess.check_output(["git", "ls-files", "-z"], cwd=project).decode("utf-8").split("\0")
    )
    allowed_roots = {"src", "web", "deploy", "tests", ".codex", ".agents", ".github"}
    allowed_files = {
        "README.md",
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "ruff.toml",
        ".env.example",
        ".gitignore",
        ".gitattributes",
    }
    files = {}
    for name in tracked:
        if not name:
            continue
        path = project / name
        if name not in allowed_files and name.split("/")[0] not in allowed_roots:
            raise ValueError(f"Deployment manifest contains an unexpected tracked path: {name}")
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(project):
            raise ValueError("A deployment source file is missing or escapes the project.")
        files[name] = path
    for path in distribution.rglob("*"):
        if path.is_file():
            if path.is_symlink() or not path.resolve().is_relative_to(distribution):
                raise ValueError("Frontend deployment file escapes its distribution directory.")
            files[path.relative_to(project).as_posix()] = path
    required = {
        "pyproject.toml",
        "uv.lock",
        "src/hrs/__main__.py",
        "src/hrs/codex/workflow.md",
        ".codex/config.toml",
        ".agents/skills/historical-research/SKILL.md",
        "web/dist/index.html",
    }
    if not required.issubset(files):
        raise ValueError("The deployment is missing required application or runtime resources.")
    forbidden_parts = {
        "node_modules",
        ".venv",
        "__pycache__",
        ".cache",
        "state",
        "models",
        "output",
        "backups",
    }
    for name in files:
        parts = [part.casefold() for part in Path(name).parts]
        private_environment = name != ".env.example" and any(
            part.startswith(".env") for part in parts
        )
        if any(part in forbidden_parts for part in parts) or private_environment:
            raise ValueError("Private data or local environments cannot enter a deployment bundle.")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=project))
    manifest = {
        "schema_version": 1,
        "kind": "hrs-source-deployment",
        "version": __version__,
        "source_revision": revision,
        "working_tree_modified": dirty,
        "files": {},
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "x", compression=ZIP_DEFLATED, allowZip64=True) as archive:
        for name, path in sorted(files.items()):
            data = path.read_bytes()
            archive.writestr(name, data)
            manifest["files"][name] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "byte_length": len(data),
            }
        archive.writestr(
            "release-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    return {
        "bundle": str(destination),
        "version": __version__,
        "files": len(files),
        "source_revision": revision,
        "working_tree_modified": dirty,
    }
