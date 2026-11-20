"""Runs agent turns on the Codex SDK, and talks MCP to the hay server.

Codex is the model runtime, on your ChatGPT sign-in (no API key). It is locked down:
read-only sandbox, approvals denied, shell / apps / browser / plugins off, an empty
working folder. Its only way to business data is the MCP server, and every call there
goes through the gate.

Replay skips the model and re-sends a recorded turn's tool calls through the real MCP
server, gate and database, so the demo still works with no network.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from . import db
from .db import log, one
from .rules import BUDGET, MODELS

HERE = Path(__file__).resolve().parent
REPLAYS = HERE.parent / "replays"
SERVER = ["-m", "hayagents.server"]
LOCKED = ["shell_tool", "unified_exec", "apps", "plugins", "multi_agent", "goals", "image_generation",
          "browser_use", "browser_use_external", "computer_use", "in_app_browser", "sleep_tool", "view_image",
          "code_mode_host", "hooks", "skill_search", "tool_suggest", "remote_plugin",
          "tool_search_always_defer_mcp_tools"]


def _env(agent, goal_id, task_id) -> dict:
    return {"HAY_AGENT": agent, "HAY_GOAL": goal_id or "", "HAY_TASK": task_id or "", "HAY_DB": str(db.DB),
            "PYTHONPATH": str(HERE.parent)}


def run(agent: str, goal_id: str, task_id: str | None, prompt: str, record: str) -> dict:
    """One live agent turn. Its tool calls are saved to replays/<record> for offline replay."""
    from openai_codex import ApprovalMode, Codex, Sandbox

    conn, (model, effort, why) = db.connect(), MODELS[agent]
    if one(conn, "SELECT tokens_used FROM goals WHERE id=?", goal_id)[0] >= BUDGET["tokens"]:
        raise SystemExit("token budget used up: agent stopped before calling the model")
    print(f"  model: {model} ({effort}): {why}")

    config = {"features": dict.fromkeys(LOCKED, False), "web_search": "disabled", "model_reasoning_effort": effort,
              "mcp_servers": {"hay": {"command": sys.executable, "args": SERVER, "env": _env(agent, goal_id, task_id),
                                      "default_tools_approval_mode": "approve"}}}   # the gate does the real checking
    with Codex() as codex:
        result = codex.thread_start(
            model=model, sandbox=Sandbox.read_only, approval_mode=ApprovalMode.deny_all,
            cwd=tempfile.mkdtemp(), ephemeral=True, config=config,
            developer_instructions=(HERE / "prompts" / f"{agent}.md").read_text()).run(prompt)

    calls = []
    for item in result.items:
        item = getattr(item, "root", item)
        if type(item).__name__ == "McpToolCallThreadItem":
            calls.append({"tool": item.tool, "args": item.arguments or {}})
            _show(item.tool, item.arguments or {}, item.result and item.result.model_dump_json())
    tokens = result.usage.total.total_tokens if result.usage else 0
    conn.execute("UPDATE goals SET tokens_used = tokens_used + ? WHERE id=?", (tokens, goal_id))
    log(conn, agent, "model_turn", "ok", detail={"model": model, "effort": effort, "tool_calls": len(calls)},
        goal_id=goal_id, tokens=tokens)
    turn = {"agent": agent, "calls": calls, "final": result.final_response}
    (REPLAYS / record).write_text(json.dumps(turn, indent=2))
    return turn


def replay(record: str, agent: str, goal_id: str, task_id: str | None) -> dict:
    """Re-send a recorded turn. The agent comes from the caller, never from the file."""
    turn = json.loads((REPLAYS / record).read_text())
    if turn["agent"] != agent or agent not in MODELS:
        raise SystemExit(f"recording is for {turn['agent']!r}, not {agent!r}: refusing to replay")
    print("  REPLAY: recorded model decisions, live MCP server, gate and database")
    calls = [(c["tool"], c["args"]) for c in turn["calls"]]
    for (name, args), result in zip(calls, call(agent, calls, goal_id, task_id)):
        _show(name, args, json.dumps(result))
    return turn


def call(agent: str, calls: list[tuple[str, dict]], goal_id=None, task_id=None) -> list[dict]:
    """Send tool calls through the MCP server as `agent`, in one session. Used by the operator and replay."""
    return asyncio.run(_session(agent, goal_id, task_id, calls))


async def _session(agent, goal_id, task_id, calls):
    params = StdioServerParameters(command=sys.executable, args=SERVER, env={**os.environ, **_env(agent, goal_id, task_id)})
    with open("hay-server.log", "a") as errlog:       # server tracebacks go to a file, not the screen
        async with stdio_client(params, errlog=errlog) as (read, write), ClientSession(read, write) as s:
            await s.initialize()
            return [_decode(await s.call_tool(name, args)) for name, args in calls]


def _decode(result) -> dict:
    text = "".join(getattr(c, "text", "") for c in result.content)
    return {"error": text} if result.is_error else json.loads(text)


def _show(name, args, result_text):
    print(f"  -> {name}({json.dumps(args)[:150]})")
    if result_text:
        print(f"     <- {result_text[:200]}")
