"""Exercise the wheel's MCP resources from outside the source checkout."""

import asyncio
import json
import os
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest
from mcp import Client, StdioServerParameters


def test_wheel_mcp_preserves_resources_and_workspace_selection_outside_checkout(tmp_path):
    selected = os.environ.get("HRS_TEST_WHEEL")
    if not selected:
        pytest.skip("Set HRS_TEST_WHEEL to the built wheel for the packaging smoke check.")
    wheel = Path(selected).resolve()
    installed = tmp_path / "installed-wheel"
    with ZipFile(wheel) as archive:
        source_root = Path(__file__).resolve().parents[3] / "src"
        expected_modules = {
            path.relative_to(source_root).as_posix() for path in (source_root / "hrs").rglob("*.py")
        }
        packaged_modules = {
            name for name in archive.namelist() if name.startswith("hrs/") and name.endswith(".py")
        }
        assert packaged_modules == expected_modules, (
            "Wheel contains missing or retired Python modules"
        )
        assert all(
            (installed / name).resolve().is_relative_to(installed.resolve())
            for name in archive.namelist()
        )
        instructions = archive.read("hrs/codex/workflow.md").decode("utf-8")
        archive.extractall(installed)
    deployment, workspace, outside = (
        tmp_path / name for name in ("deployment", "evidence", "outside")
    )
    deployment.mkdir()
    outside.mkdir()
    launch = (
        "import pathlib,runpy,sys,hrs; "
        f"assert pathlib.Path(hrs.__file__).resolve().is_relative_to(pathlib.Path({str(installed)!r})); "
        f"sys.argv=['hrs','--root',{str(workspace)!r},'mcp']; "
        "runpy.run_module('hrs',run_name='__main__')"
    )

    async def scenario():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-c", launch],
            cwd=outside,
            env={
                "PYTHONPATH": str(installed),
                "PYTHONUTF8": "1",
                "HRS_PROJECT_ROOT": str(deployment),
                "HRS_CODEX_ROOT": str(workspace),
            },
        )
        async with Client(parameters, mode="legacy", read_timeout_seconds=15) as client:
            result = await client.call_tool("workflow_instructions")
            assert not result.is_error
            assert result.content[0].text == instructions
            status = await client.call_tool("workspace_status")
            assert not status.is_error
            value = status.structured_content or json.loads(status.content[0].text)
            if set(value) == {"result"}:
                value = value["result"]
            assert Path(value["root"]) == workspace
            assert value["configured_root"] == str(workspace)
            assert value["is_default"]
            assert (
                value["workspace_id"]
                == json.loads((workspace / ".workspace.json").read_text())["workspace_id"]
            )

    asyncio.run(scenario())
