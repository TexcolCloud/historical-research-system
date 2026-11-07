"""Per-workspace execution context. Login remains in the user's existing CODEX_HOME."""

import json
import os
import sys
import tomllib
from pathlib import Path

from hrs.codex.runtime_policy import coordinator_instructions

TOOLS = [
    "dispatch_work",
    "open_task_packet",
    "freeze_observations",
    "navigation_candidates",
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


def command(
    executable, root, document_id, logs, model, project, *,
    tools=None, roles=None, instructions=None, reasoning_effort=None,
):
    from hrs.processing.runtime_control import effective_policy

    policy = effective_policy(root)
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
        "mcp_servers.historical_research.args": ["-m", "hrs", "--root", str(root), "mcp"],
        # Stdio MCP starts with a filtered environment. Forward names, never secret
        # values in CLI arguments, so its platform adapters use this same deployment.
        "mcp_servers.historical_research.env_vars": sorted(
            name for name in os.environ
            if name.startswith(("HRS_", "PLATFORM_", "INGEST_")) or name == "PYTHONUTF8"
        ),
        "mcp_servers.historical_research.default_tools_approval_mode": "approve",
        "mcp_servers.historical_research.required": True,
        "mcp_servers.historical_research.enabled_tools": TOOLS if tools is None else tools,
    }
    if policy["overlap_reading"]:
        settings["model_reasoning_effort"] = "low"
    if reasoning_effort is not None:
        settings["model_reasoning_effort"] = reasoning_effort
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
    role_settings = roles or {
        name: {
            "model": "gpt-6.1-terra" if name == "historical_ocr" else "gpt-6.1-sol",
            "effort": "medium",
            "instructions": "Use the assigned session_token with open_task_packet; its stage instructions are authoritative. Do not request the whole project workflow or development skills.",
        }
        for name in ("historical_ocr", "historical_review", "historical_organization")
    }
    for name, role in role_settings.items():
        # Different coordinators may run different frozen policies concurrently.
        from hrs.processing.hashing import digest

        path = runtime / f"{name}-{digest(role)[:16]}.toml"
        path.write_text(
            "\n".join((
                f"name = {json.dumps(name)}",
                f"model = {json.dumps(role['model'])}",
                f"model_reasoning_effort = {json.dumps(role['effort'])}",
                f"developer_instructions = {json.dumps(role['instructions'])}",
                "",
            )),
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
        instructions if instructions is not None else coordinator_instructions(document_id, batch_pages=policy["batch_pages"]),
        runtime,
    )
