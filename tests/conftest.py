import pytest

from hayagents import db, ops


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = db.reset(tmp_path / "test.db")
    monkeypatch.setattr(ops, "FAULT_FLAG", tmp_path / ".fault")
    c = db.connect(path)
    yield c
    c.close()


GOOD_PLAN = [
    {"department": "procurement", "title": "Buy the shortfall", "quantity_t": 100,
     "deadline": "2026-12-05", "criterion": "po_covers_shortfall"},
    {"department": "production", "title": "Confirm Bowmans capacity and QC", "quantity_t": None,
     "deadline": "2026-12-06", "criterion": "site_capacity_confirmed", "depends_on": "G-001-T1"},
    {"department": "sales_fulfilment", "title": "Book containers", "quantity_t": None,
     "deadline": "2026-12-08", "criterion": "booking_confirmed"},
    {"department": "finance", "title": "Check commitment", "quantity_t": None,
     "deadline": "2026-12-04", "criterion": "commitment_checked"},
]


def ceo(conn, tool, **args):
    return ops.gate(conn, agent="ceo_agent", goal_id="G-001", task_id=None, tool=tool, args=args)


def proc(conn, tool, task="G-001-T1", **args):
    return ops.gate(conn, agent="procurement_agent", goal_id=None, task_id=task, tool=tool, args=args)


def operator(conn, tool, **args):
    return ops.gate(conn, agent="operator", goal_id=None, task_id=None, tool=tool, args=args)


@pytest.fixture
def planned(conn):
    """Goal confirmed, plan submitted by the CEO agent and approved by the CEO."""
    ops.confirm_goal(conn, "D-001")
    ceo(conn, "submit_task_plan", tasks=GOOD_PLAN)
    ops.approve_plan(conn, "G-001", "ceo")
    return conn
