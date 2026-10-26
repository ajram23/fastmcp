"""Closing a session's transport cancels its in-flight tool calls instead of waiting them out."""

import anyio
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from fastmcp import FastMCP


@pytest.mark.asyncio
async def test_closing_the_transport_cancels_an_in_flight_tool_call() -> None:
    server = FastMCP("cancel-on-close")
    started, cancelled = anyio.Event(), anyio.Event()

    @server.tool
    async def slow() -> str:
        started.set()
        try:
            await anyio.sleep(30)
        except anyio.get_cancelled_exc_class():
            cancelled.set()
            raise
        return "done"

    async def call_until_closed(session: ClientSession) -> None:
        # The call can't complete once the streams close; only the server-side cancellation is asserted.
        try:
            await session.call_tool("slow", {})
        except Exception:
            pass

    async with anyio.create_task_group() as tg:
        async with create_client_server_memory_streams() as (
            client_streams,
            server_streams,
        ):
            tg.start_soon(
                lambda: server._mcp_server.run(
                    *server_streams, server._mcp_server.create_initialization_options()
                )
            )
            async with ClientSession(*client_streams) as session:
                await session.initialize()
                tg.start_soon(call_until_closed, session)
                await started.wait()
        with anyio.fail_after(5):
            await cancelled.wait()
        tg.cancel_scope.cancel()
