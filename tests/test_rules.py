import pytest

from hayagents import people, rules


def test_shortfall_seed(conn):
    s = rules.shortfall(conn, "D-001")
    # 200 + 180 eligible; LOT-B2 reserved for Korea; LOT-B3 too wet; 20 t firm inbound
    assert (s.eligible_t, s.inbound_t, s.gap_t) == (380, 20, 100)


def test_shortfall_follows_goal_change(conn):
    people.confirm_goal(conn, "D-001", tonnes=450)
    assert rules.shortfall(conn, "D-001").gap_t == 50


def test_shortfall_never_negative(conn):
    assert rules.shortfall(conn, "D-001", demand_t=300).gap_t == 0


def test_releasing_a_reserved_lot_shrinks_the_gap(conn):
    conn.execute("UPDATE items SET reserved_for=NULL WHERE lot_id='LOT-B2'")
    assert rules.shortfall(conn, "D-001").gap_t == 70


@pytest.mark.parametrize("amount,tier", [
    (9999.99, "auto"), (10000, "department_manager"), (49999.99, "department_manager"),
    (50000, "ceo"), (50000.01, "ceo")])
def test_tier_boundaries(amount, tier):
    assert rules.tier_for(amount)["name"] == tier


def test_who_can_approve():
    mgr, top = rules.tier_for(20000), rules.tier_for(60000)
    assert rules.can_approve(mgr, "procurement_manager")
    assert not rules.can_approve(mgr, "production_manager")
    assert rules.can_approve(top, "ceo")
    assert rules.missing_signers(top, ["ceo"]) == ["procurement_manager"]
