"""Tests for the proxy component-list cache (backport of FastMCP 3.2.0 PR #3479).

Verifies that ProxyToolManager/ProxyResourceManager/ProxyPromptManager cache
their get_*() results for _cache_ttl seconds, with isolation between managers
and a working invalidate_cache() escape hatch.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.proxy import (
    FastMCPProxy,
    ProxyPromptManager,
    ProxyResourceManager,
    ProxyToolManager,
    _CacheEntry,
)


@pytest.fixture
def backend_with_tool() -> FastMCP:
    """A small FastMCP server exposing a single tool + resource + prompt."""
    server = FastMCP(name="backend")

    @server.tool
    def greet(name: str) -> str:
        return f"hello, {name}"

    @server.resource("file:///readme.txt")
    def readme() -> str:
        return "readme contents"

    @server.prompt
    def greeting(name: str) -> str:
        return f"Say hello to {name}"

    return server


def make_proxy_tool_mgr(backend: FastMCP, cache_ttl: float | None = None) -> ProxyToolManager:
    """Helper: build a ProxyToolManager whose factory yields a fresh in-memory Client."""
    return ProxyToolManager(
        client_factory=lambda: Client(backend),
        cache_ttl=cache_ttl,
    )


def make_proxy_resource_mgr(backend: FastMCP, cache_ttl: float | None = None) -> ProxyResourceManager:
    return ProxyResourceManager(
        client_factory=lambda: Client(backend),
        cache_ttl=cache_ttl,
    )


def make_proxy_prompt_mgr(backend: FastMCP, cache_ttl: float | None = None) -> ProxyPromptManager:
    return ProxyPromptManager(
        client_factory=lambda: Client(backend),
        cache_ttl=cache_ttl,
    )


@pytest.mark.asyncio
async def test_get_tool_uses_cached_list(backend_with_tool: FastMCP) -> None:
    """First call fetches from upstream; second call within TTL hits cache."""
    mgr = make_proxy_tool_mgr(backend_with_tool)

    with patch.object(Client, "list_tools", wraps=Client.list_tools, autospec=True) as spy:
        tools_a = await mgr.get_tools()
        tools_b = await mgr.get_tools()

    assert "greet" in tools_a
    assert "greet" in tools_b
    # Second call should not re-hit the backend
    assert spy.call_count == 1, f"expected 1 upstream list_tools, got {spy.call_count}"


@pytest.mark.asyncio
async def test_cache_expires_after_ttl(backend_with_tool: FastMCP) -> None:
    """Once ttl elapses the next call re-fetches from upstream."""
    mgr = make_proxy_tool_mgr(backend_with_tool, cache_ttl=0.05)

    with patch.object(Client, "list_tools", wraps=Client.list_tools, autospec=True) as spy:
        await mgr.get_tools()
        time.sleep(0.1)
        await mgr.get_tools()

    assert spy.call_count == 2


@pytest.mark.asyncio
async def test_cache_ttl_zero_disables_caching(backend_with_tool: FastMCP) -> None:
    """cache_ttl=0 should always re-fetch."""
    mgr = make_proxy_tool_mgr(backend_with_tool, cache_ttl=0)

    with patch.object(Client, "list_tools", wraps=Client.list_tools, autospec=True) as spy:
        await mgr.get_tools()
        await mgr.get_tools()
        await mgr.get_tools()

    assert spy.call_count == 3


@pytest.mark.asyncio
async def test_invalidate_cache_forces_refetch(backend_with_tool: FastMCP) -> None:
    """invalidate_cache() clears every manager slot; next get_tools re-fetches."""
    mgr = make_proxy_tool_mgr(backend_with_tool)

    with patch.object(Client, "list_tools", wraps=Client.list_tools, autospec=True) as spy:
        await mgr.get_tools()
        mgr.invalidate_cache()
        await mgr.get_tools()

    assert spy.call_count == 2


@pytest.mark.asyncio
async def test_cache_isolation_between_managers(backend_with_tool: FastMCP) -> None:
    """Tool and resource managers maintain independent caches."""
    tool_mgr = make_proxy_tool_mgr(backend_with_tool)
    resource_mgr = make_proxy_resource_mgr(backend_with_tool)

    await tool_mgr.get_tools()

    # Tool manager's cache does NOT satisfy resource manager's cache —
    # resource_mgr should still hit the upstream list_resources.
    with patch.object(Client, "list_resources", wraps=Client.list_resources, autospec=True) as spy:
        await resource_mgr.get_resources()

    assert spy.call_count == 1
    # Tool-manager state is untouched by resource-manager activity
    assert tool_mgr._tools_cache is not None
    assert resource_mgr._tools_cache is None


@pytest.mark.asyncio
async def test_get_tool_lookup_uses_cached_list(backend_with_tool: FastMCP) -> None:
    """ToolManager.get_tool(key) calls get_tools() internally — the cache must
    accelerate individual lookups, not just bulk get_tools calls.
    """
    mgr = make_proxy_tool_mgr(backend_with_tool)

    with patch.object(Client, "list_tools", wraps=Client.list_tools, autospec=True) as spy:
        await mgr.get_tool("greet")
        await mgr.get_tool("greet")
        await mgr.get_tool("greet")

    assert spy.call_count == 1


@pytest.mark.asyncio
async def test_resource_cache_independent_of_template_cache(backend_with_tool: FastMCP) -> None:
    mgr = make_proxy_resource_mgr(backend_with_tool)
    await mgr.get_resources()
    assert mgr._resources_cache is not None
    # Templates cache is independent
    assert mgr._templates_cache is None

    await mgr.get_resource_templates()
    assert mgr._templates_cache is not None


@pytest.mark.asyncio
async def test_prompts_cache(backend_with_tool: FastMCP) -> None:
    mgr = make_proxy_prompt_mgr(backend_with_tool)

    with patch.object(Client, "list_prompts", wraps=Client.list_prompts, autospec=True) as spy:
        await mgr.get_prompts()
        await mgr.get_prompts()

    assert spy.call_count == 1


def test_cache_entry_shape() -> None:
    """Quick sanity check on the _CacheEntry internal container."""
    entry = _CacheEntry({"a": 1}, time.monotonic())
    assert entry.is_fresh(ttl=60.0)
    # very short ttl — after a sleep, stale
    time.sleep(0.01)
    assert not entry.is_fresh(ttl=0.001)
