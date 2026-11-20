"""Hard business rules in plain Python. The AI never computes these numbers."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

DATA = Path(__file__).parent / "data"

DEPARTMENTS = {"procurement", "production", "sales_fulfilment", "finance"}
DEPARTMENT_OWNER = {
    "procurement": "procurement_manager",
    "production": "production_manager",
    "sales_fulfilment": "sales_manager",
    "finance": "finance_manager",
}
DEPARTMENT_AGENT = {"procurement": "procurement_agent", "production": "production_agent"}

# Completion checks the planner may attach to a task. Each one is a function below.
CRITERIA = {
    "po_covers_shortfall": "A draft PO exists for this demand, covers the shortfall, meets the market spec and arrives before the site cutoff",
    "site_capacity_confirmed": "Production manager confirms site capacity and QC release before the cutoff",
    "booking_confirmed": "Sales manager confirms carrier booking and ETA before the arrival date",
    "commitment_checked": "Finance manager checks the purchase commitment against budget",
}


@lru_cache
def policy() -> dict:
    return yaml.safe_load((DATA / "policy.yaml").read_text())


@lru_cache
def specs() -> dict:
    return yaml.safe_load((DATA / "specs.yaml").read_text())


def meets_spec(lot, market: str) -> bool:
    s = specs()["markets"][market]
    return (lot["moisture_pct"] <= s["max_moisture_pct"]
            and lot["crude_protein_pct"] >= s["min_crude_protein_pct"]
            and lot["adf_pct"] <= s["max_adf_pct"])


def eligible_lots(conn, demand_id: str) -> list[dict]:
    """Lots that meet the market spec and are not promised to another order."""
    d = conn.execute("SELECT * FROM demands WHERE id=?", (demand_id,)).fetchone()
    lots = conn.execute("SELECT * FROM items ORDER BY lot_id").fetchall()
    return [dict(l) for l in lots
            if (l["reserved_for"] in (None, demand_id)) and meets_spec(l, d["market"])]


def firm_inbound(conn, demand_id: str) -> float:
    """Confirmed POs for this demand that arrive at the site by the receipt cutoff."""
    d = conn.execute("SELECT receipt_cutoff FROM demands WHERE id=?", (demand_id,)).fetchone()
    row = conn.execute(
        "SELECT COALESCE(SUM(tonnes),0) t FROM purchase_orders "
        "WHERE demand_id=? AND status='confirmed' AND receipt_date<=?",
        (demand_id, d["receipt_cutoff"])).fetchone()
    return float(row["t"])


@dataclass(frozen=True)
class Shortfall:
    demand_t: float
    eligible_t: float
    inbound_t: float
    gap_t: float


def shortfall(conn, demand_id: str, demand_t: float | None = None) -> Shortfall:
    """gap = max(0, demand - eligible available stock - firm inbound for this demand)."""
    d = conn.execute("SELECT tonnes FROM demands WHERE id=?", (demand_id,)).fetchone()
    demand = float(d["tonnes"] if demand_t is None else demand_t)
    eligible = sum(l["tonnes"] for l in eligible_lots(conn, demand_id))
    inbound = firm_inbound(conn, demand_id)
    return Shortfall(demand, eligible, inbound, max(0.0, demand - eligible - inbound))


def tier_for(amount: float) -> dict:
    for t in policy()["approval_tiers"]:
        hi = t["max_exclusive"]
        if amount >= t["min"] and (hi is None or amount < hi):
            return t
    raise ValueError(f"no tier for {amount}")


def can_approve(tier: dict, approver: str) -> bool:
    """Is this person one of the signers this tier needs?"""
    return approver in tier["approvers"]


def missing_signers(tier: dict, signed: list[str]) -> list[str]:
    return [a for a in tier["approvers"] if a not in signed]


def terms_hash(p: dict) -> str:
    """Fingerprint of the exact terms a human approves. Any change = new approval needed."""
    keys = ("task_id", "vendor_id", "quote_id", "tonnes", "price_per_t", "amount",
            "currency", "receipt_date", "spec_version")
    blob = json.dumps({k: p[k] for k in keys}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class PlanError(ValueError):
    pass


def validate_plan(conn, goal_id: str, tasks: list[dict]) -> list[dict]:
    """Check the CEO agent's draft plan. Reject anything vague or wrong; never trust its maths."""
    g = conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
    if g is None:
        raise PlanError("unknown goal")
    d = conn.execute("SELECT * FROM demands WHERE id=?", (g["demand_id"],)).fetchone()
    gap = shortfall(conn, d["id"]).gap_t
    if not tasks:
        raise PlanError("plan has no tasks")
    clean = []
    for i, t in enumerate(tasks, 1):
        dept = t.get("department")
        if dept not in DEPARTMENTS:
            raise PlanError(f"task {i}: unknown department {dept!r}")
        if t.get("criterion") not in CRITERIA:
            raise PlanError(f"task {i}: criterion must be one of {sorted(CRITERIA)}")
        try:
            deadline = date.fromisoformat(str(t.get("deadline")))
        except ValueError:
            raise PlanError(f"task {i}: deadline must be a date like 2026-12-05")
        if deadline > date.fromisoformat(d["arrival_date"]):
            raise PlanError(f"task {i}: deadline is after the customer arrival date")
        title = str(t.get("title", "")).strip()
        if not title:
            raise PlanError(f"task {i}: missing title")
        if len(title) > 100:
            raise PlanError(f"task {i}: title is {len(title)} characters; keep it under 100")
        qty = t.get("quantity_t")
        if dept == "procurement":
            if qty is None or abs(float(qty) - gap) > 1e-6:
                raise PlanError(f"task {i}: procurement quantity must equal the computed shortfall ({gap:g} t), got {qty}")
            if deadline > date.fromisoformat(d["receipt_cutoff"]):
                raise PlanError(f"task {i}: procurement deadline is after the site receipt cutoff")
        clean.append({
            "department": dept, "title": title, "quantity_t": qty,
            "deadline": deadline.isoformat(), "criterion": t["criterion"],
            "depends_on": t.get("depends_on"),
        })
    if gap > 0 and not any(c["department"] == "procurement" for c in clean):
        raise PlanError(f"there is a {gap:g} t shortfall but no procurement task")
    return clean
