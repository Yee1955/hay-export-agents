"""The tools and the gate, tested without a model."""
import asyncio
import json

import pytest
from agent_framework import Agent, ChatContext, ChatResponse, FunctionInvocationContext, Message, tool

from hayagents import gate, tools, web
from hayagents.codex_client import CodexChatClient
from hayagents.gate import Gate
from hayagents.team import task_efforts


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


def test_authority_limit_unless_a_person_approved_the_spend(monkeypatch):
    monkeypatch.setattr(tools, "AGENT", "procurement_agent")              # may order up to $50,000
    task_id = tools.create_task("Buy 300 t", "ordered", "2026-12-05", ["procurement_agent"])["task_id"]
    assert tools.write_stock("HART-120", 120, 11.5, "ordered", "Hart", 205, task_id) == "HART-120 saved"
    assert tools.write_stock("MALLEE-300", 300, 12.8, "ordered", "Mallee", 195, task_id).startswith("Refused: $58,500")
    assert tools.write_stock("NO-PRICE", 10, 12, "ordered", task_id=task_id).startswith("Refused")
    approved = tools.create_task("Buy 300 t", "ordered", "2026-12-05", ["procurement_agent"], approved_spend=60000)
    assert tools.write_stock("MALLEE-300", 300, 12.8, "ordered", "Mallee", 195, approved["task_id"]) == "MALLEE-300 saved"
    monkeypatch.setattr(tools, "AGENT", "production_agent")               # may not order at all
    assert tools.write_stock("HART-1", 1, 11.5, "ordered", "Hart", 205, task_id).startswith("Refused")


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


def test_effort_saved_with_legacy_tasks():
    original = tools.create_task("Reserve", "reserved", "2026-12-05", ["production_agent"])["task_id"]
    tools.q("ALTER TABLE tasks DROP COLUMN efforts")
    added = tools.create_task("Order", "ordered", "2026-12-05", ["procurement_agent"],
                              {"procurement_agent": "high"})["task_id"]
    assert task_efforts(tools.read_task(original)) == {"production_agent": "medium", "coordinator": "low"}
    assert task_efforts(tools.read_task(added)) == {"procurement_agent": "high", "coordinator": "low"}


def test_live_turns_are_per_task_and_clear_on_completion(monkeypatch):
    async def check_turns():
        roles = ["production_agent", "coordinator"]
        ids = [tools.create_task(role, "done", "2026-12-05", ["production_agent"])["task_id"] for role in roles]
        entered = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()

        async def answer(index, messages, options):
            entered[index].set()
            await release.wait()
            return ChatResponse(messages=[Message("assistant", ["Finished"])])

        async def run(index, role):
            client = CodexChatClient("test", "low")
            monkeypatch.setattr(client, "ask", lambda messages, options: answer(index, messages, options))
            worker = Agent(client, name=role, middleware=Gate(role, ids[index]).middleware)
            await worker.run("test")

        runs = asyncio.gather(*(run(index, role) for index, role in enumerate(roles)))
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), timeout=2)
            response = await web.tasks(None)
            tasks = json.loads(response.body)["tasks"]
            assert [task["active_agents"] for task in tasks] == [[role] for role in roles]
        finally:
            release.set()
            await runs
        assert not gate.active_turns

    asyncio.run(check_turns())
