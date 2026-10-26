# Orbit fork of FastMCP

This repository is a fork of [PrefectHQ/fastmcp](https://github.com/PrefectHQ/fastmcp),
pinned to tag `v2.14.4`, carrying two changes on branch `orbit-2.14.4`:

## What's changed vs. upstream v2.14.4

**Backport of [fastmcp#3479](https://github.com/PrefectHQ/fastmcp/pull/3479)
(FastMCP 3.2.0) — component-list cache for proxy managers.**

`ProxyToolManager`, `ProxyResourceManager`, and `ProxyPromptManager` in
`src/fastmcp/server/proxy.py` now memoize their `get_tools()` /
`get_resources()` / `get_resource_templates()` / `get_prompts()` results
for `cache_ttl` seconds (default: **300s**, matches FastMCP 3.2.0).

Because `ToolManager.get_tool(key)` delegates to `get_tools()` and indexes
the returned dict (`src/fastmcp/tools/tool_manager.py:65-76`), this caches
the per-call dispatch lookup transparently. On the Orbit agent path this
was the primary source of the ~14.5s per-tool-call latency regression —
each call triggered an unnecessary upstream `list_tools()` cascade.

Tests: `tests/server/test_proxy_cache.py` (8 tests covering hit/miss/expiry/
isolation/invalidation/`get_tool` lookup cache).

**Cancel in-flight tool calls when a session's transport closes.**

`LowLevelServer.run` in `src/fastmcp/server/low_level.py` overrides mcp's
`Server.run` and drops its `finally: tg.cancel_scope.cancel()`. Without it, a
client that ends its session (streamable-HTTP `DELETE`) leaves its tool call
running to completion, because the task group joins the handler instead of
cancelling it. Upstream v2.14.4, v2.14.7 and v3.4.10 all lack it. The fork
restores the `finally`.

Tests: `tests/server/test_low_level_cancel.py`.

## Orbit tag

`v2.14.4.post2` — adds the transport-close cancel. `v2.14.4.post1` — first stable release of this backport. Named as a PEP 440
post-release of upstream `2.14.4` so that `uv-dynamic-versioning` (style=
`pep440`, configured in upstream `pyproject.toml`) can parse it during
`pip install` from the Git URL. A custom suffix like `v2.14.4-orbit.1`
normalizes to `2.14.4orbit1`, which is not PEP 440 compliant and breaks
install-time version resolution.

```
fastmcp @ git+https://github.com/ajram23/fastmcp@v2.14.4.post2
```

## When to retire this fork

Delete the fork and switch back to upstream PyPI `fastmcp` once Orbit
upgrades to `fastmcp>=3.2.0`, which carries the same cache natively
(in `ProxyProvider` under `src/fastmcp/server/providers/proxy.py`).
