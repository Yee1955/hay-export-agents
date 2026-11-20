"""Plain-code rules: the shortfall, spending tiers, and plan checks."""
import pytest

from hayagents import people, rules
from hayagents.rules import Denied
from conftest import PLAN, ceo


def test_shortfall(conn):
    # 200 + 180 eligible; LOT-B2 is promised to Korea; LOT-B3 is too wet; 20 t firm inbound
    assert rules.shortfall(conn, "D-001") == {"demand_t": 500, "eligible_t": 380, "inbound_t": 20, "shortfall_t": 100}
    conn.execute("UPDATE items SET reserved_for=NULL WHERE lot_id='LOT-B2'")
    assert rules.shortfall(conn, "D-001")["shortfall_t"] == 70


def test_changing_the_goal_changes_the_shortfall(conn):
    people.confirm_goal(conn, "D-001", tonnes=450)
    assert rules.shortfall(conn, "D-001")["shortfall_t"] == 50


@pytest.mark.parametrize("amount,signers", [
    (9999.99, []), (10000, ["procurement_manager"]), (49999.99, ["procurement_manager"]),
    (50000, ["procurement_manager", "ceo"])])
def test_tier_boundaries(amount, signers):
    assert rules.missing_signers(amount, []) == signers


@pytest.fixture
def goal(conn):
    people.confirm_goal(conn, "D-001")
    return conn


@pytest.mark.parametrize("change,why", [
    ({"quantity_t": 300}, "computed shortfall"),          # a valid-looking 300 t task still fails
    ({"department": "marketing"}, "unknown department"),
    ({"criterion": "looks_done"}, "criterion"),
    ({"deadline": "soon"}, "deadline"),
    ({"deadline": "2026-12-07"}, "receipt cutoff"),
    ({"title": "x" * 101}, "title"),
])
def test_bad_plans_are_rejected(goal, change, why):
    with pytest.raises(Denied, match=why):
        ceo(goal, "submit_task_plan", tasks=[{**PLAN[0], **change}, *PLAN[1:]])


def test_shortfall_needs_a_procurement_task(goal):
    with pytest.raises(Denied, match="no procurement task"):
        ceo(goal, "submit_task_plan", tasks=PLAN[1:])


def test_one_draft_per_goal_and_ceo_approves_it(goal):
    ceo(goal, "submit_task_plan", tasks=PLAN)
    with pytest.raises(Denied, match="already waiting"):
        ceo(goal, "submit_task_plan", tasks=PLAN)           # a draft can't be swapped under the CEO
    people.approve_plan(goal, "G-001")
    assert goal.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 4
