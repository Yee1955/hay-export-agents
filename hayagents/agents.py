"""Runs one agent turn on the Codex SDK, with the hay MCP server as its only tool source.

Codex is the model runtime, using your ChatGPT sign-in (no API key). It is locked
down: read-only sandbox, approvals denied, shell / apps / browser / plugins off, an
empty working folder. Its only way to business data is the MCP server, and every
MCP call goes through the gate.

Replay skips the model and re-sends the recorded tool calls through the real MCP
server, gate and database, so the demo still works with no network.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from . import db, mcp_client, rules

HERE = Path(__file__).resolve().parent
REPLAYS = HERE.parent / "replays"
AGENTS = ("ceo_agent", "procurement_agent")

LOCKED_FEATURES = ["shell_tool", "unified_exec", "apps", "plugins", "multi_agent", "goals", "image_generation",
                   "browser_use", "browser_use_external", "computer_use", "in_app_browser", "sleep_tool",
                   "view_image", "code_mode_host", "hooks", "skill_search", "tool_suggest", "remote_plugin",
                   "tool_search_always_defer_mcp_tools"]


def _codex_config(agent: str, goal_id: str, task_id: str | None, effort: str) -> dict:
    env = {"HAY_AGENT": agent, "HAY_GOAL": goal_id, "HAY_TASK": task_id or "",
           "HAY_DB": str(db.DEFAULT_DB.resolve()), "PYTHONPATH": str(HERE.parent)}
    return {
        "features": {name: False for name in LOCKED_FEATURES},
        "web_search": "disabled",
        "model_reasoning_effort": effort,
        "mcp_servers": {"hay": {
            "command": sys.executable, "args": ["-m", "hayagents.erp_server"], "env": env,
            "default_tools_approval_mode": "approve",      # the gate does the real checking
        }},
    }


def run(agent: str, goal_id: str, task_id: str | None, prompt: str, *, record: str | None = None) -> dict:
    """One live agent turn. Records the tool calls so the turn can be replayed later."""
    from openai_codex import ApprovalMode, Codex, Sandbox

    pol, conn = rules.policy(), db.connect()
    if conn.execute("SELECT tokens_used FROM budgets WHERE goal_id=?", (goal_id,)).fetchone()[0] \
            >= pol["budgets"]["token_limit"]:
        db.log_event(conn, actor=agent, action="start_turn", decision="denied", goal_id=goal_id,
                     task_id=task_id, reason="token budget used up")
        raise RuntimeError("token budget used up: agent stopped before calling the model")

    route = pol["model_routing"][agent]
    db.log_event(conn, actor="router", action="model_routing", decision="ok", goal_id=goal_id, task_id=task_id,
                 detail={"agent": agent, **route}, policy_version=pol["version"])
    print(f"  model: {route['model']} ({route['reasoning_effort']}): {route['why']}")

    with Codex() as codex:
        thread = codex.thread_start(
            model=route["model"], sandbox=Sandbox.read_only, approval_mode=ApprovalMode.deny_all,
            cwd=tempfile.mkdtemp(prefix="hay-agent-"), ephemeral=True,
            config=_codex_config(agent, goal_id, task_id, route["reasoning_effort"]),
            developer_instructions=(HERE / "prompts" / f"{agent}.md").read_text())
        result = thread.run(prompt)

    calls = []
    for item in result.items:
        item = getattr(item, "root", item)
        if type(item).__name__ == "McpToolCallThreadItem":
            calls.append({"call_id": item.id, "tool": item.tool, "args": item.arguments or {}})
            _show(item.tool, item.arguments or {}, _text(item.result), item.id[-8:])

    tokens = result.usage.total.total_tokens if result.usage else 0
    conn.execute("UPDATE budgets SET tokens_used = tokens_used + ? WHERE goal_id=?", (tokens, goal_id))
    db.log_event(conn, actor=agent, action="model_turn", decision="ok", goal_id=goal_id, task_id=task_id,
                 detail={"tool_calls": len(calls), "model": route["model"]}, tokens=tokens)
    conn.close()
    turn = {"agent": agent, "prompt": prompt, "calls": calls, "final": result.final_response, "tokens": tokens}
    if record:
        REPLAYS.mkdir(exist_ok=True)
        (REPLAYS / record).write_text(json.dumps(turn, indent=2))
    return turn


def replay(record: str, *, agent: str, goal_id: str, task_id: str | None) -> dict:
    """Re-send a recorded turn's tool calls. The role comes from the caller, never from the file."""
    turn = json.loads((REPLAYS / record).read_text())
    if turn.get("agent") != agent or agent not in AGENTS:
        raise RuntimeError(f"recording is for {turn.get('agent')!r}, expected {agent!r}: refusing to replay")
    print("  REPLAY: recorded model decisions, live MCP server, gate and database")
    calls = [(c["tool"], c["args"]) for c in turn["calls"]]
    for (tool, args), result in zip(calls, mcp_client.call_many(agent, calls, goal_id=goal_id, task_id=task_id)):
        _show(tool, args, json.dumps(result))
    return turn


def _text(result) -> str:
    if result is None:
        return ""
    data = result.model_dump(mode="json")
    if data.get("structured_content"):
        return json.dumps(data["structured_content"].get("result", data["structured_content"]))
    return " ".join(c.get("text", "") for c in data.get("content", [])).replace("\n", " ")


def _show(tool, args, result_text, call_id=""):
    print(f"  -> {tool}({json.dumps(args)[:150]})" + (f"  [{call_id}]" if call_id else ""))
    if result_text:
        print(f"     <- {result_text[:200]}")
