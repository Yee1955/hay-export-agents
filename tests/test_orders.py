"""Signing and placing orders: the only write, and everything that can go wrong with it."""
import pytest

from hayagents import people
from hayagents.server import ReplyLost
from hayagents.rules import Denied
from conftest import operator, propose


def po_count(c):
    return c.execute("SELECT COUNT(*) FROM purchase_orders WHERE proposal_id IS NOT NULL").fetchone()[0]




def test_wrong_signer_and_unsigned_order_refused(planned):
    p = propose(planned)
    with pytest.raises(Denied, match="can't sign"):
        people.sign(planned, p, "production_manager")
    with pytest.raises(Denied, match="still needs signatures"):
        operator(planned, p)
    assert po_count(planned) == 0


def test_big_order_needs_both_signatures(planned):
    planned.execute("UPDATE quotes SET price_per_t=600 WHERE id='Q-101'")   # 100 t = $60,000
    p = propose(planned)
    assert people.sign(planned, p, "ceo") == ["procurement_manager"]
    with pytest.raises(Denied, match="still needs signatures from: procurement_manager"):
        operator(planned, p)
    with pytest.raises(Denied, match="can't sign"):
        people.sign(planned, p, "ceo")                                      # no signing twice
    people.sign(planned, p, "procurement_manager")
    assert operator(planned, p)["created"]


def test_lost_reply_then_retry_returns_the_same_po(planned):
    p = propose(planned)
    assert people.sign(planned, p, "procurement_manager") == []
    with pytest.raises(ReplyLost):
        operator(planned, p, lose_reply=True)               # saved, but the caller never hears back
    again = operator(planned, p)
    assert (again["created"], again["purchase_order"]["id"], again["purchase_order"]["amount"]) == (False, "PO-0002", 20500)
    assert po_count(planned) == 1 and people.verify_task(planned, "G-001-T1")[0]


@pytest.mark.parametrize("change,why", [
    ("UPDATE items SET reserved_for=NULL WHERE lot_id='LOT-B2'", "shortfall is now 70"),   # stock changed
    ("UPDATE proposals SET receipt_date='2026-12-04'", "terms changed"),                    # terms edited
    ("UPDATE quotes SET capacity_t=60 WHERE id='Q-101'", "only supply 60"),                # grower cut back
    ("UPDATE quotes SET vendor_id='V-002' WHERE id='Q-101'", "quote changed"),             # different grower
    ("DELETE FROM quotes WHERE id='Q-101'", "no longer valid"),                             # quote withdrawn
])
def test_recheck_at_execution(planned, change, why):
    p = propose(planned)
    people.sign(planned, p, "procurement_manager")
    planned.execute(change)
    with pytest.raises(Denied, match=why):
        operator(planned, p)
    assert po_count(planned) == 0


def test_verify_needs_this_tasks_own_order(planned):
    ok, why = people.verify_task(planned, "G-001-T1")
    assert not ok and "for this task" in why
