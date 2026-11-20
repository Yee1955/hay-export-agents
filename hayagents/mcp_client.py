"""Small MCP client used by the trusted operator (after a human approves) and by replay mode.

It starts the same erp_server the agents use, over stdio, so replay and execution
exercise the real MCP path, gate and SQLite.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def server_params(agent: str, goal_id: str | None = None, task_id: str | None = None) -> StdioServerParameters:
    env = {**os.environ, "HAY_AGENT": agent, "HAY_GOAL": goal_id or "", "HAY_TASK": task_id or ""}
    return StdioServerParameters(command=sys.executable, args=["-m", "hayagents.erp_server"], env=env)


def _decode(result) -> dict:
    if getattr(result, "structured_content", None) or getattr(result, "structuredContent", None):
        sc = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    text = "".join(getattr(c, "text", "") for c in result.content)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text, "is_error": getattr(result, "is_error", getattr(result, "isError", False))}


SERVER_LOG = os.environ.get("HAY_SERVER_LOG", "hay-server.log")


async def _session_calls(agent, goal_id, task_id, calls):
    with open(SERVER_LOG, "a") as errlog:   # server tracebacks go to a log file, not the screen
        return await _run(agent, goal_id, task_id, calls, errlog)


async def _run(agent, goal_id, task_id, calls, errlog):
    async with stdio_client(server_params(agent, goal_id, task_id), errlog=errlog) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            out = []
            for name, args in calls:
                out.append(_decode(await s.call_tool(name, args)))
            return tools, out


def call(agent: str, tool: str, args: dict, goal_id: str | None = None, task_id: str | None = None) -> dict:
    return asyncio.run(_session_calls(agent, goal_id, task_id, [(tool, args)]))[1][0]


def call_many(agent: str, calls: list[tuple[str, dict]], goal_id=None, task_id=None) -> list[dict]:
    return asyncio.run(_session_calls(agent, goal_id, task_id, calls))[1]


def list_tools(agent: str, goal_id=None, task_id=None) -> list[str]:
    return asyncio.run(_session_calls(agent, goal_id, task_id, []))[0]
