import copy

import pytest

from hayagents import people
from hayagents.errors import Denied
from conftest import GOOD_PLAN, ceo


@pytest.fixture
def goal(conn):
    people.confirm_goal(conn, "D-001")
    return conn


def plan_with(**change):
    p = copy.deepcopy(GOOD_PLAN)
    p[0].update(change)
    return p


def test_good_plan_waits_for_ceo(goal):
    r = ceo(goal, "submit_task_plan", tasks=GOOD_PLAN)
    assert r["status"] == "waiting for CEO approval"
    assert goal.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"] == 0


def test_oversized_task_draft_is_rejected(goal):
    # a schema-valid task for 300 t must still fail: Python owns the number
    with pytest.raises(Denied, match="computed shortfall"):
        ceo(goal, "submit_task_plan", tasks=plan_with(quantity_t=300))


@pytest.mark.parametrize("change,msg", [
    ({"department": "marketing"}, "unknown department"),
    ({"criterion": "looks_done"}, "criterion"),
    ({"deadline": "soon"}, "deadline"),
    ({"deadline": "2026-12-07"}, "receipt cutoff"),
])
def test_vague_or_wrong_tasks_rejected(goal, change, msg):
    with pytest.raises(Denied, match=msg):
        ceo(goal, "submit_task_plan", tasks=plan_with(**change))


def test_shortfall_needs_a_procurement_task(goal):
    with pytest.raises(Denied, match="no procurement task"):
        ceo(goal, "submit_task_plan", tasks=GOOD_PLAN[1:])


def test_only_ceo_approves_plan(goal):
    ceo(goal, "submit_task_plan", tasks=GOOD_PLAN)
    with pytest.raises(Denied):
        people.approve_plan(goal, "G-001", "procurement_manager", version=1)
    ids = people.approve_plan(goal, "G-001", "ceo", version=1)
    assert len(ids) == 4
    owners = {r["department"]: r["owner"] for r in goal.execute("SELECT * FROM tasks")}
    assert owners["procurement"] == "procurement_manager"


def test_approved_plan_cannot_be_replaced(planned):
    with pytest.raises(Denied, match="already approved"):
        ceo(planned, "submit_task_plan", tasks=GOOD_PLAN)


def test_long_title_is_rejected_not_cut(goal):
    with pytest.raises(Denied, match="under 100"):
        ceo(goal, "submit_task_plan", tasks=plan_with(title="x" * 101))
