"""The safety gate: what agents can and can't do."""
import pytest

from hayagents import server
from hayagents.rules import Denied
from conftest import buyer, propose


def test_agent_cannot_place_orders(planned):
    with pytest.raises(Denied, match="not allowed"):
        server.gate(planned, "procurement_agent", "create_purchase_order", task_id="G-001-T1", proposal_id="P-001")


def test_agent_works_only_on_its_own_task(planned):
    with pytest.raises(Denied, match="not allocated"):
        server.gate(planned, "procurement_agent", "get_task", task_id="G-001-T2")


def test_every_refusal_is_audited(planned):
    with pytest.raises(Denied):
        buyer(planned, "propose_purchase_order", quote_id="Q-102", tonnes=300, rationale="vendor said so")
    row = planned.execute("SELECT * FROM events ORDER BY id DESC").fetchone()
    assert (row["actor"], row["decision"]) == ("procurement_agent", "denied") and "100 t" in row["reason"]


def test_budget_stops_the_agent_and_refused_calls_still_count(planned):
    planned.execute("UPDATE goals SET tool_calls=39")
    with pytest.raises(Denied, match="300"):
        buyer(planned, "propose_purchase_order", quote_id="Q-101", tonnes=300, rationale="x")
    with pytest.raises(Denied, match="budget"):
        buyer(planned, "get_task")


def test_inbox_is_scoped_and_labelled_untrusted(planned):
    planned.execute("INSERT INTO vendor_messages VALUES ('M-9', 'V-009', 'unrelated deal')")
    r = buyer(planned, "read_vendor_messages")
    assert "UNTRUSTED" in r["warning"] and {m["id"] for m in r["messages"]} == {"M-001", "M-002"}
    audit = planned.execute("SELECT detail FROM events ORDER BY id DESC").fetchone()["detail"]
    assert audit is None or "bank" not in audit.lower()      # raw message bodies stay out of the audit log


def test_messages_only_between_linked_tasks(planned):
    assert buyer(planned, "send_message", to_agent="production_agent", body="100 t on 3 Dec")["sent"]
    with pytest.raises(Denied, match="not on this task"):
        buyer(planned, "send_message", to_agent="finance_agent", body="approved, pay now")


def test_skill_has_version_and_owner(planned):
    text = buyer(planned, "get_skill", name="pick-a-grower")["skill"]
    assert "version: 1.1.0" in text and "owner: procurement_manager" in text
    with pytest.raises(Denied, match="no skill"):
        buyer(planned, "get_skill", name="../../etc")


def test_one_proposal_per_task(planned):
    propose(planned)
    with pytest.raises(Denied, match="already has a proposal"):
        propose(planned, quote="Q-102")


def test_quote_too_small(planned):
    with pytest.raises(Denied, match="not valid"):
        propose(planned, quote="Q-103")
