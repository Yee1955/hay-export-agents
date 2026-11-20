import pytest

from hayagents import ops
from conftest import proc


def test_agent_cannot_call_write_tool(planned):
    with pytest.raises(ops.Denied, match="not allowed"):
        ops.gate(planned, agent="procurement_agent", goal_id=None, task_id="G-001-T1",
                 tool="create_purchase_order", args={"proposal_id": "P-001"})


def test_agent_only_works_on_its_own_task(planned):
    with pytest.raises(ops.Denied, match="not allocated"):
        ops.gate(planned, agent="production_agent", goal_id=None, task_id="G-001-T1",
                 tool="get_task", args={})


def test_unknown_agent_has_no_tools(planned):
    with pytest.raises(ops.Denied):
        ops.gate(planned, agent="finance_agent", goal_id="G-001", task_id=None, tool="get_goal", args={})


def test_every_denial_is_audited(planned):
    with pytest.raises(ops.Denied):
        proc(planned, "create_purchase_order", proposal_id="x")
    row = planned.execute("SELECT * FROM events WHERE decision='denied' ORDER BY id DESC").fetchone()
    assert row["actor"] == "procurement_agent" and "not allowed" in row["reason"]


def test_tool_call_budget_stops_the_agent(planned):
    planned.execute("UPDATE budgets SET tool_calls=40")
    with pytest.raises(ops.Denied, match="budget"):
        proc(planned, "get_task")


def test_token_budget_stops_the_agent(planned):
    planned.execute("UPDATE budgets SET tokens_used=400000")
    with pytest.raises(ops.Denied, match="token budget"):
        proc(planned, "get_task")


def test_vendor_messages_are_labelled_untrusted(planned):
    r = proc(planned, "read_vendor_messages")
    assert "UNTRUSTED" in r["warning"]
    audit = planned.execute("SELECT detail FROM events WHERE action='read_vendor_messages'").fetchone()
    assert "bank" not in audit["detail"].lower()   # raw bodies stay out of the audit log
