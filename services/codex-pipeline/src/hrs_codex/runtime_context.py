"""Per-workspace execution context. Login remains in the user's existing CODEX_HOME."""

import json
import os
import sys
import tomllib
from pathlib import Path

from hrs_codex.runtime_policy import coordinator_instructions

TOOLS = [
    "dispatch_work",
    "open_task_packet",
    "freeze_observations",
    "submit_packet",
    "finish_worker",
    "wait_for_changes",
    "candidate_text",
    "original_image",
    "original_detail",
    "original_details",
    "approved_page_text",
    "approved_pages_text",
    "approved_page_catalog",
    "report_task_failure",
    "document_status",
    "publish_document",
    "human_review_queue",
    "fallback_page",
]


def account_route():
    """Retain configured login routing, without copying plugins, hooks or credentials."""
    path = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    if not path.exists():
        return {}
    config = tomllib.loads(path.read_text("utf-8"))
    name = config.get("model_provider")
    if not name or name not in config.get("model_providers", {}):
        return {}
    provider = config["model_providers"][name]
    result = {"model_provider": name}
    for key in (
        "name",
        "base_url",
        "wire_api",
        "requires_openai_auth",
        "supports_websockets",
        "env_key",
    ):
        if key in provider:
            result[f"model_providers.{name}.{key}"] = provider[key]
    return result


def command(executable, root, document_id, logs, model, project):
    from hrs_codex.execution_policy import execution_policy

    policy = execution_policy(root)
    runtime = root / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / ".hrs-runtime-root").touch()
    settings = {
        **account_route(),
        "project_doc_max_bytes": 0,
        "project_root_markers": [".hrs-runtime-root"],
        "features.shell_tool": False,
        "features.apps": False,
        "plugins._default.enabled": False,
        "skills.bundled.enabled": False,
        "skills.include_instructions": False,
        "agents.max_threads": max(8, policy["capacity"]),
        "mcp_servers.historical_research.command": sys.executable,
        "mcp_servers.historical_research.args": ["-m", "hrs_codex.cli", "--root", str(root), "mcp"],
        "mcp_servers.historical_research.default_tools_approval_mode": "approve",
        "mcp_servers.historical_research.required": True,
        "mcp_servers.historical_research.enabled_tools": TOOLS,
    }
    if policy["overlap_reading"]:
        settings["model_reasoning_effort"] = "low"
    skill_roots = [
        Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills",
        Path.home() / ".agents/skills",
        project / ".agents/skills",
    ]
    skills = sorted(
        {
            path.resolve().as_posix()
            for directory in skill_roots
            for path in directory.rglob("SKILL.md")
        }
    )
    # TOML inline tables, not JSON objects, are required by -c.
    disabled_skills = (
        "[" + ",".join("{path=" + json.dumps(path) + ",enabled=false}" for path in skills) + "]"
    )
    arguments = [
        executable,
        "exec",
        "--ignore-user-config",
        "--strict-config",
        "--skip-git-repo-check",
        "--cd",
        str(runtime),
        "--sandbox",
        "workspace-write",
        "--json",
        "--output-last-message",
        str(logs / "summary.txt"),
        "--add-dir",
        str(root),
        "--model",
        model or "gpt-6.1-sol",
    ]
    for key, value in settings.items():
        arguments.extend(["-c", f"{key}={json.dumps(value)}"])
    arguments.extend(["-c", "skills.config=" + disabled_skills])
    for name in ("historical_ocr", "historical_review", "historical_organization"):
        path = runtime / f"{name}.toml"
        selected_model = "gpt-6.1-terra" if name == "historical_ocr" else "gpt-6.1-sol"
        path.write_text(
            f'name = "{name}"\nmodel = "{selected_model}"\nmodel_reasoning_effort = "medium"\ndeveloper_instructions = "Use the assigned session_token with open_task_packet; its stage instructions are authoritative. Do not request the whole project workflow or development skills."\n',
            encoding="utf-8",
        )
        arguments.extend(
            [
                "-c",
                f"agents.{name}.config_file={json.dumps(str(path))}",
                "-c",
                f"agents.{name}.description={json.dumps('Native historical source worker: ' + name)}",
            ]
        )
    return (
        arguments,
        coordinator_instructions(document_id, batch_pages=policy["batch_pages"]),
        runtime,
    )
