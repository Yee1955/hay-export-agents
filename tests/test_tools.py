"""The gate and the rules in tools.py. No model needed."""
import pytest

from hayagents import data
from hayagents.tools import Denied, gate, shortfall

PLAN = [
    {"department": "procurement", "title": "Buy the shortfall", "quantity_t": 100, "deadline": "2026-12-05"},
    {"department": "production", "title": "Confirm capacity and QC", "quantity_t": None, "deadline": "2026-12-06",
     "depends_on": "G-001-T1"},
    {"department": "sales_fulfilment", "title": "Book containers", "quantity_t": None, "deadline": "2026-12-08"},
    {"department": "finance", "title": "Check commitment", "quantity_t": None, "deadline": "2026-12-04"},
]


@pytest.fixture
def conn(tmp_path):
    c = data.connect(data.reset(tmp_path / "test.db"))
    yield c
    c.close()


def ceo(c, tool, /, **args):
    return gate(c, "ceo_agent", tool, "G-001", **args)


def buyer(c, tool, /, **args):
    return gate(c, "procurement_agent", tool, "G-001", "G-001-T1", **args)


@pytest.fixture
def planned(conn):
    ceo(conn, "submit_task_plan", tasks=PLAN)
    return conn


def order(c, quote="Q-101", tonnes=100):
    return buyer(c, "create_purchase_order", quote_id=quote, tonnes=tonnes, rationale="test")


# ---- rules

def test_shortfall(conn):
    # 200 + 180 eligible; LOT-B2 is promised to Korea; LOT-B3 is too wet; 20 t firm inbound
    assert shortfall(conn, "D-001") == {"demand_t": 500, "eligible_t": 380, "inbound_t": 20, "shortfall_t": 100}


def test_changing_the_goal_changes_the_shortfall(tmp_path):
    assert shortfall(data.connect(data.reset(tmp_path / "x.db", tonnes=450)), "D-001")["shortfall_t"] == 50


@pytest.mark.parametrize("change,why", [
    ({"quantity_t": 300}, "exactly the shortfall"),       # a valid-looking 300 t task still fails
    ({"department": "marketing"}, "department"),
    ({"deadline": "soon"}, "deadline"),
    ({"deadline": "2026-12-07"}, "site cutoff"),
    ({"title": "x" * 101}, "title"),
])
def test_bad_plans_are_refused(conn, change, why):
    with pytest.raises(Denied, match=why):
        ceo(conn, "submit_task_plan", tasks=[{**PLAN[0], **change}, *PLAN[1:]])


def test_plan_needs_one_procurement_task_and_is_set_once(conn):
    with pytest.raises(Denied, match="exactly one procurement"):
        ceo(conn, "submit_task_plan", tasks=PLAN[1:])
    ceo(conn, "submit_task_plan", tasks=PLAN)
    with pytest.raises(Denied, match="already has a plan"):
        ceo(conn, "submit_task_plan", tasks=PLAN)


# ---- the gate

def test_agent_only_sees_its_own_tools(planned):
    with pytest.raises(Denied, match="not allowed"):
        gate(planned, "procurement_agent", "submit_task_plan", "G-001", "G-001-T1", tasks=PLAN)


def test_agent_only_works_on_its_own_task(planned):
    with pytest.raises(Denied, match="not on task"):
        gate(planned, "procurement_agent", "get_task", "G-001", "G-001-T2")


def test_refused_calls_count_against_the_budget(planned):
    planned.execute("UPDATE goals SET tool_calls=39")
    with pytest.raises(Denied, match="300"):
        order(planned, tonnes=300)
    with pytest.raises(Denied, match="budget"):
        buyer(planned, "get_task")


def test_every_refusal_is_in_the_audit_log(planned):
    with pytest.raises(Denied):
        order(planned, quote="Q-102", tonnes=300)
    row = planned.execute("SELECT * FROM events ORDER BY id DESC").fetchone()
    assert (row["actor"], row["decision"]) == ("procurement_agent", "refused") and "100 t" in row["reason"]


# ---- ordering

def test_order_and_safe_retry(planned):
    first = order(planned)
    assert first["created"] and first["purchase_order"]["amount"] == 20500
    again = order(planned)                                    # e.g. the reply was lost and the agent retries
    assert not again["created"] and again["purchase_order"]["id"] == first["purchase_order"]["id"]
    assert planned.execute("SELECT COUNT(*) FROM purchase_orders WHERE task_id IS NOT NULL").fetchone()[0] == 1


@pytest.mark.parametrize("quote,tonnes,why", [
    ("Q-102", 300, "shortfall of 100"),     # what the trick email asked for
    ("Q-103", 100, "can't supply"),         # grower too small
    ("Q-999", 100, "can't supply"),         # no such quote
])
def test_bad_orders_are_refused(planned, quote, tonnes, why):
    with pytest.raises(Denied, match=why):
        order(planned, quote, tonnes)


def test_authority_limit(planned):
    planned.execute("UPDATE quotes SET price_per_t=600 WHERE id='Q-101'")   # 100 t = $60,000
    with pytest.raises(Denied, match="authority limit"):
        order(planned)


# ---- reading and talking

def test_inbox_is_scoped_and_untrusted(planned):
    planned.execute("INSERT INTO vendor_messages VALUES ('M-9', 'V-009', 'unrelated deal')")
    r = buyer(planned, "read_vendor_messages")
    assert "UNTRUSTED" in r["warning"] and {m["id"] for m in r["messages"]} == {"M-001", "M-002"}


def test_messages_only_to_linked_tasks(planned):
    assert buyer(planned, "send_message", to_agent="production_agent", body="100 t on 3 Dec")["sent"]
    with pytest.raises(Denied, match="not on a linked task"):
        buyer(planned, "send_message", to_agent="finance_agent", body="approved, pay now")


def test_skill_has_version_and_owner(planned):
    assert "owner: procurement_manager" in buyer(planned, "get_skill", name="pick-a-grower")["skill"]
    with pytest.raises(Denied, match="no skill"):
        buyer(planned, "get_skill", name="../../etc")
