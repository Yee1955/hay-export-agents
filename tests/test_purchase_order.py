import pytest

from hayagents import ops
from conftest import operator, proc


def propose(c, quote="Q-101", tonnes=100):
    return proc(c, "propose_purchase_order", quote_id=quote, tonnes=tonnes, rationale="test")


def po_count(c):
    return c.execute("SELECT COUNT(*) c FROM purchase_orders WHERE status='draft'").fetchone()["c"]


def test_server_prices_the_order_and_picks_the_tier(planned):
    r = propose(planned)
    assert (r["amount"], r["tier"], r["status"]) == (20500, "department_manager", "pending_approval")


def test_injected_300t_order_is_denied(planned):
    with pytest.raises(ops.Denied, match="authorised shortfall"):
        propose(planned, quote="Q-102", tonnes=300)


def test_quote_too_small_is_denied(planned):
    with pytest.raises(ops.Denied, match="only covers"):
        propose(planned, quote="Q-103")


def test_cannot_execute_before_approval(planned):
    p = propose(planned)["proposal_id"]
    with pytest.raises(ops.Denied, match="not approved"):
        operator(planned, "create_purchase_order", proposal_id=p)
    assert po_count(planned) == 0


def test_wrong_signer_is_denied(planned):
    p = propose(planned)["proposal_id"]
    with pytest.raises(ops.Denied, match="cannot approve"):
        ops.approve_proposal(planned, p, "production_manager")


def test_happy_path_and_verification(planned):
    p = propose(planned)["proposal_id"]
    ops.approve_proposal(planned, p, "procurement_manager")
    r = operator(planned, "create_purchase_order", proposal_id=p)
    assert r["created"] and r["purchase_order"]["tonnes"] == 100
    ok, why = ops.verify_task(planned, "G-001-T1")
    assert ok, why


def test_retry_returns_the_same_po(planned):
    p = propose(planned)["proposal_id"]
    ops.approve_proposal(planned, p, "procurement_manager")
    first = operator(planned, "create_purchase_order", proposal_id=p)["purchase_order"]["id"]
    again = operator(planned, "create_purchase_order", proposal_id=p)
    assert again["created"] is False and again["purchase_order"]["id"] == first
    assert po_count(planned) == 1


def test_lost_acknowledgement_after_commit(planned):
    p = propose(planned)["proposal_id"]
    ops.approve_proposal(planned, p, "procurement_manager")
    ops.FAULT_FLAG.write_text("armed")
    with pytest.raises(ops.FaultAfterCommit):
        operator(planned, "create_purchase_order", proposal_id=p)
    # restart: the caller retries the same approved proposal
    r = operator(planned, "create_purchase_order", proposal_id=p)
    assert r["created"] is False
    assert po_count(planned) == 1
    assert planned.execute("SELECT COUNT(*) c FROM events WHERE action='po_created'").fetchone()["c"] == 1
    assert ops.verify_task(planned, "G-001-T1")[0]


def test_stale_state_rejects_a_valid_approval(planned):
    p = propose(planned)["proposal_id"]
    ops.approve_proposal(planned, p, "procurement_manager")
    planned.execute("UPDATE items SET reserved_for=NULL WHERE lot_id='LOT-B2'")  # 30 t freed up
    with pytest.raises(ops.Denied, match="shortfall is now 70"):
        operator(planned, "create_purchase_order", proposal_id=p)
    assert po_count(planned) == 0


def test_changed_terms_after_approval_are_rejected(planned):
    p = propose(planned)["proposal_id"]
    ops.approve_proposal(planned, p, "procurement_manager")
    planned.execute("UPDATE proposals SET receipt_date='2026-12-04' WHERE id=?", (p,))
    with pytest.raises(ops.Denied, match="terms changed"):
        operator(planned, "create_purchase_order", proposal_id=p)


def test_one_po_per_demand(planned):
    p = propose(planned)["proposal_id"]
    ops.approve_proposal(planned, p, "procurement_manager")
    operator(planned, "create_purchase_order", proposal_id=p)
    planned.execute("UPDATE tasks SET status='open' WHERE id='G-001-T1'")
    with pytest.raises(ops.Denied, match="one PO per demand"):
        propose(planned)


def test_refused_approval_is_kept_in_the_audit_log(planned):
    p = propose(planned)["proposal_id"]
    with pytest.raises(ops.Denied):
        ops.approve_proposal(planned, p, "production_manager")
    row = planned.execute("SELECT * FROM events WHERE action='approve_proposal'").fetchone()
    assert row["decision"] == "denied" and row["goal_id"] == "G-001"
