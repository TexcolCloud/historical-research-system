"""Narrow native tools for frozen personal analysis; no source approval operations."""

import json
from functools import lru_cache, wraps

from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent
from pydantic import ValidationError

from hrs.library.errors import ServiceError
from hrs.studies.analysis import AnalysisWorkflow, public_analysis
from hrs.studies.store import StudyStore


def register_study_tools(server, native_store):
    @lru_cache(maxsize=1)
    def workflow():
        return AnalysisWorkflow(StudyStore(native_store))

    def checked(function):
        @wraps(function)
        def call(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except ServiceError as error:
                raise ToolError(str(error.detail)) from error
            except ValidationError as error:
                raise ToolError(str(error)) from error

        return call

    @server.tool()
    @checked
    def study_analysis_status(analysis_id: str) -> dict:
        """Read bounded analysis state; source texts and candidates stay stage-gated."""
        return public_analysis(workflow().store.read("analysis", analysis_id))

    @server.tool()
    @checked
    def study_analysis_claim(analysis_id: str, worker_id: str, stage: str) -> dict:
        """Claim the author or independent verify stage after host identity binding."""
        if stage not in {"author", "verify"}:
            raise ToolError("Expected author or verify")
        return workflow().claim(analysis_id, worker_id, stage)

    @server.tool()
    @checked
    def study_analysis_source(
        analysis_id: str,
        token: str,
        worker_id: str,
        material_id: str,
        offset: int = 0,
        limit: int = 4000,
    ) -> dict:
        """Read complete frozen source windows; advance end to total for each material."""
        return workflow().source(analysis_id, token, worker_id, material_id, offset, limit)

    @server.tool(structured_output=False)
    @checked
    def study_analysis_original(
        analysis_id: str, token: str, worker_id: str, material_id: str, page: int
    ) -> list:
        """Open source pixels; independent verification must view all retained source pages."""
        metadata, data = workflow().original(analysis_id, token, worker_id, material_id, page)
        return [
            TextContent(type="text", text=json.dumps(metadata)),
            Image(data=data, format="png").to_image_content(),
        ]

    @server.tool()
    @checked
    def study_analysis_candidate(
        analysis_id: str, token: str, worker_id: str, observations: list[dict]
    ) -> dict:
        """Freeze independent source observations, then expose bounded suggestions."""
        return workflow().candidate(analysis_id, token, worker_id, observations)

    @server.tool()
    @checked
    def study_analysis_submit(analysis_id: str, token: str, worker_id: str, result: dict) -> dict:
        """Commit only the claimed machine suggestion/check; never human acceptance."""
        return workflow().submit(analysis_id, token, worker_id, result)
