import pytest

from hayagents import db, people, server

PLAN = [
    {"department": "procurement", "title": "Buy the shortfall", "quantity_t": 100, "deadline": "2026-12-05",
     "criterion": "po_covers_shortfall"},
    {"department": "production", "title": "Confirm capacity and QC", "quantity_t": None, "deadline": "2026-12-06",
     "criterion": "site_capacity_confirmed", "depends_on": "G-001-T1"},
    {"department": "sales_fulfilment", "title": "Book containers", "quantity_t": None, "deadline": "2026-12-08",
     "criterion": "booking_confirmed"},
    {"department": "finance", "title": "Check commitment", "quantity_t": None, "deadline": "2026-12-04",
     "criterion": "commitment_checked"},
]


@pytest.fixture
def conn(tmp_path):
    c = db.connect(db.reset(tmp_path / "test.db"))
    yield c
    c.close()


def ceo(c, tool, /, **args):
    return server.gate(c, "ceo_agent", tool, goal_id="G-001", **args)


def buyer(c, tool, /, **args):
    return server.gate(c, "procurement_agent", tool, task_id="G-001-T1", **args)


def operator(c, proposal_id, lose_reply=False):
    return server.gate(c, "operator", "create_purchase_order", proposal_id=proposal_id, lose_reply=lose_reply)


def propose(c, quote="Q-101", tonnes=100):
    return buyer(c, "propose_purchase_order", quote_id=quote, tonnes=tonnes, rationale="test")["proposal_id"]


@pytest.fixture
def planned(conn):
    """Goal confirmed, plan submitted by the CEO agent and approved by the CEO."""
    people.confirm_goal(conn, "D-001")
    ceo(conn, "submit_task_plan", tasks=PLAN)
    people.approve_plan(conn, "G-001")
    return conn
