"""The installed public lookup accepts the same explicit multi-book scope as the UI."""

import asyncio

from mcp import Client

from tests.integration.codex.test_mcp import server_parameters


def test_scoped_lookup_declares_multiple_books_and_never_falls_back_unscoped(tmp_path):
    async def scenario():
        async with Client(
            server_parameters(tmp_path / "native"), mode="legacy", read_timeout_seconds=15
        ) as client:
            tools = await client.list_tools()
            search = next(tool for tool in tools.tools if tool.name == "search_library")
            assert "book_ids" in search.input_schema["properties"]
            result = await client.call_tool(
                "search_library",
                {
                    "query": "拨款",
                    "book_ids": ["00000000-0000-0000-0000-000000000001"],
                },
            )
            assert result.is_error
            text = " ".join(block.text for block in result.content if hasattr(block, "text"))
            assert "requires its index" in text

    asyncio.run(scenario())


def test_quote_lookup_publishes_its_bounded_batch_and_result_limits(tmp_path):
    async def scenario():
        async with Client(
            server_parameters(tmp_path / "native"), mode="legacy", read_timeout_seconds=15
        ) as client:
            listing = await client.list_tools()
            lookup = next(tool for tool in listing.tools if tool.name == "research_locate_quotes")
            schema = lookup.input_schema["properties"]
            assert schema["quotes"]["maxItems"] == 20
            assert schema["quotes"]["items"]["maxLength"] == 4000
            assert schema["limit"]["maximum"] == 20
            assert schema["offset"]["minimum"] == 0
            rejected = await client.call_tool(
                "research_locate_quotes",
                {
                    "task_token": "unclaimed",
                    "worker_id": "worker",
                    "quotes": ["原文"],
                    "limit": 100,
                },
            )
            assert rejected.is_error
            message = " ".join(item.text for item in rejected.content if hasattr(item, "text"))
            assert "20" in message and "100" in message

    asyncio.run(scenario())
