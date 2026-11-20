"""Tests for the issues found in the Codex code review (max reasoning)."""
import json

import pytest

from hayagents import agents, orders, people
from hayagents.errors import Denied
from hayagents.gate import gate
from conftest import GOOD_PLAN, ceo, operator, proc


def propose(c, quote="Q-101", tonnes=100):
    return proc(c, "propose_purchase_order", quote_id=quote, tonnes=tonnes, rationale="test")


def test_big_order_needs_both_signatures(planned):
    planned.execute("UPDATE quotes SET price_per_t=600 WHERE id='Q-101'")   # 100 t = $60,000
    p = propose(planned)
    assert p["tier"] == "ceo"
    people.approve_proposal(planned, p["proposal_id"], "ceo")
    with pytest.raises(Denied, match="still needs approval from: procurement_manager"):
        operator(planned, "create_purchase_order", proposal_id=p["proposal_id"])
    with pytest.raises(Denied, match="already signed"):
        people.approve_proposal(planned, p["proposal_id"], "ceo")
    people.approve_proposal(planned, p["proposal_id"], "procurement_manager")
    assert operator(planned, "create_purchase_order", proposal_id=p["proposal_id"])["created"]


def test_replay_cannot_borrow_another_role(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "REPLAYS", tmp_path)
    (tmp_path / "evil.json").write_text(json.dumps({"agent": "operator", "calls": []}))
    with pytest.raises(RuntimeError, match="refusing to replay"):
        agents.replay("evil.json", agent="procurement_agent", goal_id="G-001", task_id="G-001-T1")


def test_ceo_approves_only_the_version_reviewed(conn):
    people.confirm_goal(conn, "D-001")
    ceo(conn, "submit_task_plan", tasks=GOOD_PLAN)
    ceo(conn, "submit_task_plan", tasks=GOOD_PLAN)       # a second draft arrives meanwhile
    with pytest.raises(Denied, match="review it again"):
        people.approve_plan(conn, "G-001", "ceo", version=1)
    assert people.approve_plan(conn, "G-001", "ceo", version=2)


def test_only_one_open_proposal_per_task(planned):
    propose(planned)
    with pytest.raises(Denied, match="already waiting"):
        propose(planned, quote="Q-102")


def test_one_system_po_per_demand_whatever_its_status(planned):
    p = propose(planned)["proposal_id"]
    people.approve_proposal(planned, p, "procurement_manager")
    operator(planned, "create_purchase_order", proposal_id=p)
    planned.execute("UPDATE purchase_orders SET status='confirmed', receipt_date='2026-12-09' WHERE proposal_id=?", (p,))
    planned.execute("UPDATE tasks SET status='open' WHERE id='G-001-T1'")
    with pytest.raises(Denied, match="one PO per demand"):
        propose(planned)


def test_quote_change_after_approval_is_caught(planned):
    p = propose(planned)["proposal_id"]
    people.approve_proposal(planned, p, "procurement_manager")
    planned.execute("UPDATE quotes SET capacity_t=60 WHERE id='Q-101'")
    with pytest.raises(Denied, match="only supply 60"):
        operator(planned, "create_purchase_order", proposal_id=p)


def test_one_open_goal_per_demand(planned):
    with pytest.raises(Denied, match="already has an open goal"):
        people.confirm_goal(planned, "D-001")


def test_verify_uses_this_tasks_own_order(planned):
    ok, why = orders.verify_task(planned, "G-001-T1")
    assert not ok and "for this task" in why


def test_inbox_is_scoped_to_vendors_on_this_demand(planned):
    planned.execute("INSERT INTO vendors VALUES ('V-009','Unrelated Co','0000')")
    planned.execute("INSERT INTO vendor_messages VALUES ('M-009','V-009','2026-11-20T03:00:00+00:00','other deal')")
    ids = [m["id"] for m in proc(planned, "read_vendor_messages")["messages"]]
    assert "M-009" not in ids and "M-002" in ids


def test_department_agent_must_have_a_task(planned):
    with pytest.raises(Denied, match="must be started on a task"):
        gate(planned, agent="procurement_agent", goal_id=None, task_id=None, tool="read_vendor_messages", args={})
