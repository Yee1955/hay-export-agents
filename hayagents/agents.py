"""Runs one agent on the Codex SDK, with the hay MCP server as its only tool source.

    python -m hayagents.agents ceo_agent
    python -m hayagents.agents procurement_agent

Codex is the model runtime, on your ChatGPT sign-in (no API key). It is locked down:
read-only sandbox, approvals denied, shell / apps / browser / plugins off, an empty
working folder. Its only way to business data is the MCP server, and every call there
goes through the gate in tools.py.
"""
import json
import sys
import tempfile
from pathlib import Path

from openai_codex import ApprovalMode, Codex, Sandbox

from . import data
from .data import log, one

HERE = Path(__file__).resolve().parent
GOAL = "G-001"
MODELS = {"ceo_agent": ("gpt-6.2-sol", "medium"),          # model routing: planning needs more reasoning
          "procurement_agent": ("gpt-6.2-sol", "low")}    # reading data and placing one order needs less
LOCKED = ["shell_tool", "unified_exec", "apps", "plugins", "multi_agent", "goals", "image_generation",
          "browser_use", "browser_use_external", "computer_use", "in_app_browser", "sleep_tool", "view_image",
          "code_mode_host", "hooks", "skill_search", "tool_suggest", "remote_plugin",
          "tool_search_always_defer_mcp_tools"]


def run(agent: str) -> str:
    conn = data.connect()
    task = one(conn, "SELECT id FROM tasks WHERE goal_id=? AND agent=?", GOAL, agent)
    task_id = task["id"] if task else None
    model, effort = MODELS[agent]
    env = {"HAY_AGENT": agent, "HAY_GOAL": GOAL, "HAY_TASK": task_id or "", "HAY_DB": str(data.DB),
           "PYTHONPATH": str(HERE.parent)}
    config = {"features": dict.fromkeys(LOCKED, False), "web_search": "disabled", "model_reasoning_effort": effort,
              "mcp_servers": {"hay": {"command": sys.executable, "args": ["-m", "hayagents.tools"], "env": env,
                                      "default_tools_approval_mode": "approve"}}}   # the gate does the real checking
    with Codex() as codex:
        result = codex.thread_start(
            model=model, sandbox=Sandbox.read_only, approval_mode=ApprovalMode.deny_all, cwd=tempfile.mkdtemp(),
            ephemeral=True, config=config, developer_instructions=(HERE / "prompts" / f"{agent}.md").read_text(),
        ).run(f"Work on goal {GOAL}" + (f", task {task_id}." if task_id else "."))

    for item in result.items:                     # print each tool call the agent made, and its result
        item = getattr(item, "root", item)
        if type(item).__name__ == "McpToolCallThreadItem":
            reply = "".join(getattr(part, "text", "") for part in item.result.content) if item.result else ""
            print(f"-> {item.tool}({json.dumps(item.arguments or {})[:150]})\n   <- {' '.join(reply.split())[:200]}")
    tokens = result.usage.total.total_tokens if result.usage else 0
    conn.execute("UPDATE goals SET tokens_used = tokens_used + ? WHERE id=?", (tokens, GOAL))
    log(conn, agent, "model_turn", "done", detail={"model": model, "effort": effort}, tokens=tokens)
    return result.final_response


if __name__ == "__main__":
    print("\n" + run(sys.argv[1]))
