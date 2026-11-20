"""Hard business rules, in plain Python. No AI decides or computes any of this."""
import hashlib
import json
from datetime import date

VERSION = "2026-11-20.4"          # written on every audit row
TODAY = "2026-11-20"              # fixed demo date, so every run gives the same result
CURRENCY = "AUD"
BUDGET = {"tokens": 400_000, "tool_calls": 40}   # per goal

# Spending tiers: the first limit the amount is below. Every listed signer must sign.
TIERS = [(10_000, []), (50_000, ["procurement_manager"]), (float("inf"), ["procurement_manager", "ceo"])]

# Market specs. SYNTHETIC values, to be checked with a site manager.
MARKETS = {"vietnam": {"max_moisture": 13.0, "min_protein": 8.0, "max_adf": 33.0},
           "korea": {"max_moisture": 14.0, "min_protein": 7.0, "max_adf": 35.0}}

# Model routing, logged on every agent turn.
MODELS = {"ceo_agent": ("gpt-6.2-sol", "medium", "planning across departments needs more reasoning"),
          "procurement_agent": ("gpt-6.2-sol", "low", "reading data and filling one proposal")}

OWNERS = {"procurement": "procurement_manager", "production": "production_manager",
          "sales_fulfilment": "sales_manager", "finance": "finance_manager"}
AGENTS = {"procurement": "procurement_agent", "production": "production_agent"}
CRITERIA = {"po_covers_shortfall": "a draft PO covers the shortfall and arrives before the site cutoff",
            "site_capacity_confirmed": "production manager confirms capacity and QC release",
            "booking_confirmed": "sales manager confirms the carrier booking and ETA",
            "commitment_checked": "finance manager checks the commitment against budget"}


class Denied(Exception):
    """The rules refuse this request. The reason goes back to the caller and into the audit log."""


def shortfall(conn, demand_id: str) -> dict:
    """max(0, demand - eligible stock - firm inbound). Eligible = meets the spec, not promised elsewhere."""
    d = conn.execute("SELECT * FROM demands WHERE id=?", (demand_id,)).fetchone()
    m = MARKETS[d["market"]]
    eligible = conn.execute("SELECT COALESCE(SUM(tonnes), 0) FROM items WHERE (reserved_for IS NULL OR reserved_for=?)"
                            " AND moisture_pct<=? AND crude_protein_pct>=? AND adf_pct<=?",
                            (demand_id, m["max_moisture"], m["min_protein"], m["max_adf"])).fetchone()[0]
    inbound = conn.execute("SELECT COALESCE(SUM(tonnes), 0) FROM purchase_orders WHERE demand_id=?"
                           " AND status='confirmed' AND receipt_date<=?", (demand_id, d["receipt_cutoff"])).fetchone()[0]
    return {"demand_t": d["tonnes"], "eligible_t": eligible, "inbound_t": inbound,
            "shortfall_t": max(0, d["tonnes"] - eligible - inbound)}


def missing_signers(amount: float, signed: list[str]) -> list[str]:
    """Signers still needed. The tier always comes from the amount, never from storage."""
    return [s for s in next(names for limit, names in TIERS if amount < limit) if s not in signed]


def terms_hash(p) -> str:
    """Fingerprint of the exact terms people sign. Any change means signing again."""
    keys = ("task_id", "vendor_id", "quote_id", "tonnes", "price_per_t", "amount", "currency", "receipt_date")
    values = [float(p[k]) if isinstance(p[k], (int, float)) else p[k] for k in keys]   # 100 and 100.0 match
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()[:16]


def validate_plan(conn, demand, tasks: list[dict]) -> list[dict]:
    """Check the CEO agent's draft plan. Reject anything vague or wrong; never trust its maths."""
    gap = shortfall(conn, demand["id"])["shortfall_t"]
    clean = []
    for i, t in enumerate(tasks, 1):
        dept, title, qty = t.get("department"), str(t.get("title", "")).strip(), t.get("quantity_t")
        try:
            deadline = date.fromisoformat(str(t.get("deadline"))).isoformat()
        except ValueError:
            raise Denied(f"plan rejected: task {i}: deadline must be a date like 2026-12-05")
        proc = dept == "procurement"
        checks = [
            (dept not in OWNERS, f"unknown department {dept!r}"),
            (t.get("criterion") not in CRITERIA, f"criterion must be one of {list(CRITERIA)}"),
            (not title or len(title) > 100, "title must be 1 to 100 characters"),
            (deadline > demand["arrival_date"], "deadline is after the customer arrival date"),
            (proc and qty != gap, f"procurement quantity must equal the computed shortfall ({gap:g} t), got {qty}"),
            (proc and deadline > demand["receipt_cutoff"], "procurement deadline is after the site receipt cutoff"),
        ]
        problem = next((why for bad, why in checks if bad), None)
        if problem:
            raise Denied(f"plan rejected: task {i}: {problem}")
        clean.append({"department": dept, "title": title, "quantity_t": qty, "deadline": deadline,
                      "criterion": t["criterion"], "depends_on": t.get("depends_on")})
    if gap > 0 and not any(c["department"] == "procurement" for c in clean):
        raise Denied(f"plan rejected: there is a {gap:g} t shortfall but no procurement task")
    return clean
