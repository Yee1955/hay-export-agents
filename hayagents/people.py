"""What people do: the CEO confirms the goal and approves the plan, managers sign orders.
Plus the plain-code check they rely on (is the task really done?) and the report back."""
import json

from .db import log, one, task_and_demand, transaction
from .rules import AGENTS, OWNERS, Denied, missing_signers, shortfall, terms_hash


def confirm_goal(conn, demand_id: str, tonnes: float | None = None) -> str:
    """The CEO confirms the goal once. Changing the tonnes here changes every number downstream."""
    with transaction(conn):
        if one(conn, "SELECT 1 FROM goals WHERE demand_id=?", demand_id):
            raise Denied(f"demand {demand_id} already has a goal")
        if tonnes is not None:
            conn.execute("UPDATE demands SET tonnes=? WHERE id=?", (tonnes, demand_id))
        goal_id = f"G-{one(conn, 'SELECT COUNT(*) + 1 FROM goals')[0]:03d}"
        conn.execute("INSERT INTO goals (id, demand_id) VALUES (?,?)", (goal_id, demand_id))
        log(conn, "ceo", "confirm_goal", "ok", detail={"demand": demand_id, "tonnes": tonnes}, goal_id=goal_id)
    return goal_id


def approve_plan(conn, goal_id: str) -> None:
    """The CEO approves the one waiting draft (drafts can't be replaced). Each task gets its owner."""
    with transaction(conn):
        plan = one(conn, "SELECT * FROM plans WHERE goal_id=? AND status='proposed'", goal_id)
        if plan is None:
            raise Denied("no plan is waiting for approval")
        for i, t in enumerate(json.loads(plan["tasks"]), 1):
            dept = t["department"]
            conn.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,'open')",
                         (f"{goal_id}-T{i}", goal_id, dept, OWNERS[dept], AGENTS.get(dept), t["title"],
                          t["quantity_t"], t["deadline"], t["criterion"], t["depends_on"]))
        conn.execute("UPDATE plans SET status='approved' WHERE goal_id=?", (goal_id,))
        log(conn, "ceo", "approve_plan", "ok", goal_id=goal_id)


def sign(conn, proposal_id: str, signer: str) -> list[str]:
    """One signature on a proposal. Returns the signers still needed (none = approved)."""
    try:
        with transaction(conn):                     # read, check and sign in one go: no lost signatures
            p = one(conn, "SELECT * FROM proposals WHERE id=?", proposal_id)
            signed = json.loads(p["signed_by"]) if p else []
            needed = missing_signers(p["amount"], signed) if p else []
            if p is None or p["status"] != "pending":
                raise Denied(f"{proposal_id} is not waiting for signatures")
            if p["terms_hash"] != terms_hash(p):
                raise Denied("terms were edited: propose again")
            if signer not in needed:
                raise Denied(f"{signer} can't sign this order (needs {', '.join(needed)})")
            left = missing_signers(p["amount"], signed + [signer])
            conn.execute("UPDATE proposals SET signed_by=?, status=? WHERE id=?",
                         (json.dumps(signed + [signer]), "pending" if left else "approved", proposal_id))
            log(conn, signer, "sign", "ok", detail={"proposal": proposal_id, "still_needs": left}, task_id=p["task_id"])
    except Denied as e:
        log(conn, signer, "sign", "denied", str(e), {"proposal": proposal_id})
        raise
    return left


def verify_task(conn, task_id: str) -> tuple[bool, str]:
    """Close the task only when the business data proves it. The AI never marks a task done."""
    t, d = task_and_demand(conn, task_id)
    p = one(conn, "SELECT * FROM proposals WHERE task_id=? AND status='executed'", task_id)
    po = p and one(conn, "SELECT * FROM purchase_orders WHERE proposal_id=?", p["id"])
    if not po:
        return False, "no purchase order from a signed proposal for this task yet"
    checks = {
        "covers the shortfall": po["tonnes"] >= shortfall(conn, d["id"])["shortfall_t"],
        "matches the signed terms": p["terms_hash"] == terms_hash(p)
            and all(po[k] == p[k] for k in ("vendor_id", "tonnes", "amount", "receipt_date")),
        "arrives before the site cutoff": po["receipt_date"] <= d["receipt_cutoff"],
    }
    ok = all(checks.values())
    with transaction(conn):
        if ok:
            conn.execute("UPDATE tasks SET status='verified' WHERE id=?", (task_id,))
        log(conn, "verifier", "verify_task", "ok" if ok else "failed", detail=checks, task_id=task_id)
    return ok, ", ".join(f"{k}: {'yes' if v else 'NO'}" for k, v in checks.items())


def report(conn, goal_id: str) -> str:
    """What the CEO agent reports back: done, waiting on whom, refusals and budget."""
    tasks = conn.execute("SELECT * FROM tasks WHERE goal_id=? ORDER BY id", (goal_id,)).fetchall()
    goal = one(conn, "SELECT * FROM goals WHERE id=?", goal_id)
    refused = one(conn, "SELECT COUNT(*) FROM events WHERE decision='denied'")[0]
    return (f"Done: {', '.join(t['title'] for t in tasks if t['status'] == 'verified') or 'nothing yet'}."
            f" Waiting on: {', '.join(t['owner'] for t in tasks if t['status'] != 'verified')}."
            f" Refusals in the audit log: {refused}. Used {goal['tokens_used']:,} tokens, {goal['tool_calls']} tool calls.")
