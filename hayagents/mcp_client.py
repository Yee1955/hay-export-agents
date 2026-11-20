"""A small MCP client for the trusted operator and for replay mode.

It starts the same erp_server the agents use, so execution and replay go through
the real MCP path, the gate and the database.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_LOG = os.environ.get("HAY_SERVER_LOG", "hay-server.log")


def server_params(agent: str, goal_id: str | None = None, task_id: str | None = None) -> StdioServerParameters:
    env = {**os.environ, "HAY_AGENT": agent, "HAY_GOAL": goal_id or "", "HAY_TASK": task_id or ""}
    return StdioServerParameters(command=sys.executable, args=["-m", "hayagents.erp_server"], env=env)


def _decode(result) -> dict:
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured.get("result", structured)
    text = "".join(getattr(c, "text", "") for c in result.content)
    try:
        return json.loads(text)
    except ValueError:        # the server raised (e.g. a lost reply): no usable answer
        return {"error": text, "is_error": result.is_error}


async def _session(agent, goal_id, task_id, calls):
    with open(SERVER_LOG, "a") as errlog:     # server tracebacks go to a log file, not the screen
        async with stdio_client(server_params(agent, goal_id, task_id), errlog=errlog) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                tools = [t.name for t in (await s.list_tools()).tools]
                return tools, [_decode(await s.call_tool(name, args)) for name, args in calls]


def call_many(agent: str, calls: list[tuple[str, dict]], goal_id=None, task_id=None) -> list[dict]:
    return asyncio.run(_session(agent, goal_id, task_id, calls))[1]


def call(agent: str, tool: str, args: dict, goal_id=None, task_id=None) -> dict:
    return call_many(agent, [(tool, args)], goal_id, task_id)[0]


def list_tools(agent: str, goal_id=None, task_id=None) -> list[str]:
    return asyncio.run(_session(agent, goal_id, task_id, []))[0]
