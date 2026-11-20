"""The system placing and checking orders, after people have approved them.

create_purchase_order is the only write to the business data. It is operator-only:
no agent has this tool. It loads the approved terms itself and rechecks everything
at the moment of execution, inside one transaction.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import rules
from .db import log_event, now, task_and_demand, transaction
from .errors import Denied, ReplyLost

FAULT_FLAG = Path(os.environ.get("HAY_FAULT_FLAG", ".fault_after_commit"))


def create_purchase_order(conn, *, proposal_id, **_):
    with transaction(conn):
        p = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if p is None:
            raise Denied("unknown proposal")
        done = conn.execute("SELECT * FROM purchase_orders WHERE idempotency_key=?", (p["idempotency_key"],)).fetchone()
        if done:      # a retry after a lost reply: return the same PO, create nothing
            return {"purchase_order": dict(done), "created": False, "note": "already created (safe retry)"}
        _check_still_valid(conn, p)
        t, d = task_and_demand(conn, p["task_id"])
        po_id = f"PO-{conn.execute('SELECT COUNT(*) c FROM purchase_orders').fetchone()['c'] + 1:04d}"
        conn.execute("INSERT INTO purchase_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (po_id, d["id"], p["vendor_id"], p["tonnes"], p["price_per_t"], p["amount"], p["currency"],
                      p["receipt_date"], "draft", p["id"], p["idempotency_key"], now()))
        conn.execute("UPDATE proposals SET status='executed' WHERE id=?", (proposal_id,))
        conn.execute("UPDATE tasks SET status='po_created' WHERE id=?", (p["task_id"],))
        log_event(conn, actor="operator", action="po_created", decision="ok", goal_id=t["goal_id"], task_id=t["id"],
                  detail={"po": po_id, "proposal": proposal_id, "tonnes": p["tonnes"], "amount": p["amount"],
                          "signed_by": p["approved_by"]})
    if FAULT_FLAG.exists():     # demo fault: the PO is committed, then the reply is lost
        FAULT_FLAG.unlink()
        raise ReplyLost(po_id)
    po = conn.execute("SELECT * FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
    return {"purchase_order": dict(po), "created": True}


def _check_still_valid(conn, p) -> None:
    """Everything that was true at approval must still be true now."""
    missing = rules.missing_signers(rules.tier_for(p["amount"]), json.loads(p["approved_by"] or "[]"))
    if p["approved_by"] and missing:                      # tier recomputed, never trusted from storage
        raise Denied(f"still needs approval from: {', '.join(missing)}")
    if p["status"] != "approved" or missing:
        raise Denied(f"proposal is {p['status']}, not approved")
    if p["approved_terms_hash"] != rules.terms_hash(dict(p)):
        raise Denied("terms changed after approval: a fresh approval is needed")
    _, d = task_and_demand(conn, p["task_id"])
    q = conn.execute("SELECT * FROM quotes WHERE id=?", (p["quote_id"],)).fetchone()
    if q["valid_until"] < rules.TODAY:
        raise Denied("the quote has expired")
    if (q["vendor_id"], q["price_per_t"], q["delivery_date"]) != (p["vendor_id"], p["price_per_t"], p["receipt_date"]):
        raise Denied("the grower's quote changed after approval")
    if q["capacity_t"] < p["tonnes"]:
        raise Denied(f"the grower can now only supply {q['capacity_t']:g} t")
    other = conn.execute("SELECT id FROM purchase_orders WHERE demand_id=? AND proposal_id IS NOT NULL",
                         (d["id"],)).fetchone()
    if other:
        raise Denied(f"{other['id']} already exists for this demand from a different proposal")
    gap = rules.shortfall(conn, d["id"]).gap_t
    if abs(gap - p["tonnes"]) > 1e-6:
        raise Denied(f"business state changed: the shortfall is now {gap:g} t, the approval was for {p['tonnes']:g} t")


def get_purchase_order(conn, *, proposal_id, **_):
    po = conn.execute("SELECT * FROM purchase_orders WHERE proposal_id=?", (proposal_id,)).fetchone()
    return {"purchase_order": dict(po) if po else None}


TOOLS = {"create_purchase_order": create_purchase_order, "get_purchase_order": get_purchase_order}


def verify_task(conn, task_id: str) -> tuple[bool, str]:
    """Close a task only when the business data proves it. The AI never marks a task done."""
    t, d = task_and_demand(conn, task_id)
    if t["criterion"] != "po_covers_shortfall":
        return False, f"waiting on {t['owner']} (human-owned task)"
    p = conn.execute("SELECT * FROM proposals WHERE task_id=? AND status='executed'", (task_id,)).fetchone()
    po = p and conn.execute("SELECT * FROM purchase_orders WHERE proposal_id=?", (p["id"],)).fetchone()
    if not po:
        return False, "no purchase order from an approved proposal for this task yet"
    terms = ("vendor_id", "tonnes", "amount", "currency", "receipt_date")
    checks = {
        "covers shortfall": po["tonnes"] >= rules.shortfall(conn, d["id"]).gap_t - 1e-6,
        "signed terms intact": p["approved_terms_hash"] == rules.terms_hash(dict(p)),
        "matches approved terms": all(po[k] == p[k] for k in terms),
        "arrives before site cutoff": po["receipt_date"] <= d["receipt_cutoff"],
        "amount = tonnes x price": abs(po["amount"] - po["tonnes"] * po["price_per_t"]) < 0.01,
    }
    ok = all(checks.values())
    with transaction(conn):
        if ok:
            conn.execute("UPDATE tasks SET status='verified' WHERE id=?", (task_id,))
        log_event(conn, actor="verifier", action="verify_task", decision="ok" if ok else "failed",
                  goal_id=t["goal_id"], task_id=task_id, detail=checks)
    return ok, ", ".join(f"{k}: {'yes' if v else 'NO'}" for k, v in checks.items())
