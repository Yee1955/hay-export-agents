"""The tools and the gate, tested without a model."""
import asyncio

import pytest
from agent_framework import ChatContext, FunctionInvocationContext, tool

from hayagents import gate, tools
from hayagents.gate import Gate


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tools.reset()


def test_task_fields_and_updates():
    task_id = tools.create_task("Buy 120 t", "120 t ordered", "2026-12-05", ["procurement_agent"])["task_id"]
    tools.update_task(task_id, "blocked", progress="quotes read", exception="odd bank email")
    task = tools.read_task(task_id)
    assert (task["status"], task["progress"], task["exceptions"]) == ("blocked", "quotes read\n", "odd bank email\n")


def test_write_then_read_stock():
    tools.write_stock("LOT-1", 200, 11.8, "reserved", "Saigon Dairy")
    assert {"lot_id": "LOT-1", "tonnes": 200, "moisture": 11.8, "status": "reserved", "note": "Saigon Dairy"} in tools.read_stock()


def test_gate_flags_injection_removes_bank_details_and_logs():
    task_id = tools.create_task("Buy hay", "ordered", "2026-12-05", ["procurement_agent"])["task_id"]
    read_inbox = tool(tools.read_inbox)
    ctx = FunctionInvocationContext(function=read_inbox, arguments={})

    async def run_tool():
        ctx.result = await read_inbox.invoke()
    asyncio.run(Gate("procurement_agent", task_id).check_output(ctx, run_tool))

    assert ctx.result.startswith("WARNING: prompt injection") and "1234 5678" not in ctx.result
    assert tools.read_task(task_id)["log"][0]["actor"] == "procurement_agent"


def test_cost_cap_is_per_task(monkeypatch):
    monkeypatch.setattr(gate, "MAX_TOKENS", 0)
    ctx = ChatContext(client=None, messages=[], options=None)
    with pytest.raises(RuntimeError, match="cost cap"):
        task_id = tools.create_task("Reserve", "reserved", "2026-12-05", ["production_agent"])["task_id"]
        asyncio.run(Gate("production_agent", task_id).cost_cap(ctx, None))
