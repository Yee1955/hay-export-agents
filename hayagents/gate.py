"""The safety gate. Plain Python, no AI.

Every tool call from an agent (or from the operator after a human approves) comes
through gate(). It checks the allow-list, the task scope and the budget, runs the
tool, and writes an audit row either way.
"""
from __future__ import annotations

from . import orders, rules, tools
from .db import log_event, transaction
from .errors import Denied, ReplyLost

TOOLS = {**tools.TOOLS, **orders.TOOLS}


def gate(conn, *, agent: str, goal_id: str | None, task_id: str | None, tool: str, args: dict):
    pol = rules.policy()

    def audit(decision, reason=None):
        log_event(conn, actor=agent, action=tool, decision=decision, reason=reason, goal_id=goal_id,
                  task_id=task_id, detail={"args": _short(args)}, policy_version=pol["version"])

    try:
        goal_id = _check(conn, pol, agent, goal_id, task_id, tool)
        if tool in orders.TOOLS:                     # orders run their own all-or-nothing transaction
            result = TOOLS[tool](conn, agent=agent, goal_id=goal_id, task_id=task_id, **args)
            audit("allowed")
        else:
            with transaction(conn):                  # the change and its audit row commit together
                result = TOOLS[tool](conn, agent=agent, goal_id=goal_id, task_id=task_id, **args)
                audit("allowed")
        return result
    except Denied as e:
        audit("denied", str(e))
        raise
    except ReplyLost as e:
        audit("reply_lost", f"{e} was saved but the reply was lost")
        raise


def _check(conn, pol, agent, goal_id, task_id, tool) -> str | None:
    """Allow-list, task scope and budget. Returns the goal the call belongs to."""
    if tool not in pol["agent_tools"].get(agent, []):
        raise Denied(f"{agent} is not allowed to use {tool}")
    if agent == "operator":
        return goal_id
    if agent == "ceo_agent" and goal_id is None:
        raise Denied("ceo_agent must be started on a goal")
    if agent != "ceo_agent":
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone() if task_id else None
        if task is None:
            raise Denied(f"{agent} must be started on a task")
        if task["agent"] != agent:
            raise Denied(f"{agent} is not allocated to task {task_id}")
        goal_id = task["goal_id"]
    budget = conn.execute("SELECT * FROM budgets WHERE goal_id=?", (goal_id,)).fetchone()
    if budget["tool_calls"] >= pol["budgets"]["tool_call_limit"]:
        raise Denied("tool-call budget for this goal is used up")
    if budget["tokens_used"] >= pol["budgets"]["token_limit"]:
        raise Denied("token budget for this goal is used up")
    conn.execute("UPDATE budgets SET tool_calls=tool_calls+1 WHERE goal_id=?", (goal_id,))
    return goal_id


def _short(args: dict) -> dict:
    """Keep raw message bodies out of the audit log."""
    return {k: (v[:80] + "...") if isinstance(v, str) and len(v) > 80 else v for k, v in args.items()}
