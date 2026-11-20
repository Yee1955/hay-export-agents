"""What people do: the CEO sets and approves, managers sign, skill owners decide."""
from __future__ import annotations

import json

from . import rules
from .db import log_event, now, transaction
from .errors import Denied
from .tools import read_skill


def confirm_goal(conn, demand_id: str, tonnes: float | None = None, confirmed_by: str = "ceo") -> str:
    """The CEO confirms the goal once. Changing the tonnes here changes every number downstream."""
    with transaction(conn):
        if conn.execute("SELECT 1 FROM goals WHERE demand_id=? AND status='open'", (demand_id,)).fetchone():
            raise Denied(f"demand {demand_id} already has an open goal")
        if tonnes is not None:
            conn.execute("UPDATE demands SET tonnes=? WHERE id=?", (tonnes, demand_id))
        goal_id = f"G-{conn.execute('SELECT COUNT(*) c FROM goals').fetchone()['c'] + 1:03d}"
        conn.execute("INSERT INTO goals VALUES (?,?,?,?,?)", (goal_id, demand_id, confirmed_by, now(), "open"))
        conn.execute("INSERT INTO budgets (goal_id) VALUES (?)", (goal_id,))
        log_event(conn, actor=confirmed_by, action="confirm_goal", decision="ok", goal_id=goal_id,
                  detail={"demand": demand_id, "tonnes": tonnes}, policy_version=rules.policy()["version"])
    return goal_id


def approve_plan(conn, goal_id: str, approver: str, version: int | None) -> list[str]:
    """The CEO approves the exact plan version they reviewed. Each task gets its owner."""
    if approver != "ceo":
        raise Denied("only the CEO approves the task plan")
    with transaction(conn):
        plan = conn.execute("SELECT * FROM plans WHERE goal_id=? AND status='proposed'", (goal_id,)).fetchone()
        if plan is None:
            raise Denied("no proposed plan to approve")
        if plan["version"] != version:
            raise Denied(f"you reviewed plan v{version}, but the plan waiting is v{plan['version']}: review it again")
        ids = []
        for i, t in enumerate(json.loads(plan["tasks_json"]), 1):
            dept, tid = t["department"], f"{goal_id}-T{i}"
            conn.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (tid, goal_id, dept, rules.DEPARTMENT_OWNER[dept], rules.DEPARTMENT_AGENT.get(dept),
                          t["title"], t["quantity_t"], t["deadline"], t["criterion"], t.get("depends_on"),
                          "open" if dept == "procurement" else "waiting_on_human"))
            ids.append(tid)
        conn.execute("UPDATE plans SET status='approved', approved_by=? WHERE goal_id=? AND version=?",
                     (approver, goal_id, version))
        log_event(conn, actor=approver, action="approve_plan", decision="ok", goal_id=goal_id,
                  detail={"version": version, "tasks": ids})
    return ids


def approve_proposal(conn, proposal_id: str, approver: str) -> dict:
    """One signature on a proposal. Big orders need every signer in the tier, on the same terms."""
    p = conn.execute("SELECT p.*, t.goal_id FROM proposals p JOIN tasks t ON t.id = p.task_id WHERE p.id=?",
                     (proposal_id,)).fetchone()
    if p is None:
        raise Denied("unknown proposal")
    try:
        with transaction(conn):
            signed, missing = _sign(conn, proposal_id, approver)
            log_event(conn, actor=approver, action="approve_proposal", decision="ok", goal_id=p["goal_id"],
                      task_id=p["task_id"], detail={"proposal": proposal_id, "signed": signed, "still_needs": missing})
    except Denied as e:     # the transaction rolled back; the refusal is still logged
        log_event(conn, actor=approver, action="approve_proposal", decision="denied", reason=str(e),
                  goal_id=p["goal_id"], task_id=p["task_id"], detail={"proposal": proposal_id})
        raise
    return dict(conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone())


def _sign(conn, proposal_id, approver):
    p = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    tier = rules.tier_for(p["amount"])                    # recomputed, never trusted from storage
    signed = json.loads(p["approved_by"] or "[]")
    if p["status"] != "pending_approval":
        raise Denied(f"proposal is {p['status']}, not waiting for approval")
    if p["terms_hash"] != rules.terms_hash(dict(p)):
        raise Denied("proposal terms were edited: it needs to be proposed again")
    if tier["name"] != p["tier"]:
        raise Denied(f"spending limits changed: this is now a {tier['name']} order")
    if not rules.can_approve(tier, approver):
        raise Denied(f"{approver} cannot approve a {tier['name']} order (needs {', '.join(tier['approvers'])})")
    if approver in signed:
        raise Denied(f"{approver} has already signed")
    signed.append(approver)
    missing = rules.missing_signers(tier, signed)
    conn.execute("UPDATE proposals SET status=?, approved_by=?, approved_terms_hash=?, approved_at=? WHERE id=?",
                 ("pending_approval" if missing else "approved", json.dumps(signed), p["terms_hash"], now(),
                  proposal_id))
    return signed, missing


def decide_skill_suggestion(conn, suggestion_id: int, approver: str, accept: bool) -> None:
    """Only the skill's owner can accept an agent's suggested edit."""
    s = conn.execute("SELECT * FROM skill_suggestions WHERE id=?", (suggestion_id,)).fetchone()
    owner = read_skill(s["skill"])["owner"]
    if approver != owner:
        raise Denied(f"only the skill owner ({owner}) can decide")
    conn.execute("UPDATE skill_suggestions SET status=?, decided_by=? WHERE id=?",
                 ("accepted" if accept else "rejected", approver, suggestion_id))
    log_event(conn, actor=approver, action="decide_skill_suggestion", decision="ok",
              detail={"id": suggestion_id, "accepted": accept})


def report(conn, goal_id: str) -> dict:
    """What the CEO agent reports back: done, waiting, needs a decision."""
    tasks = [dict(r) for r in conn.execute("SELECT * FROM tasks WHERE goal_id=? ORDER BY id", (goal_id,))]
    return {
        "done": [t for t in tasks if t["status"] == "verified"],
        "waiting": [t for t in tasks if t["status"] != "verified"],
        "needs_decision": [dict(r) for r in conn.execute(
            "SELECT p.* FROM proposals p JOIN tasks t ON t.id = p.task_id "
            "WHERE t.goal_id=? AND p.status='pending_approval'", (goal_id,))],
        "blocked": conn.execute("SELECT COUNT(*) c FROM events WHERE goal_id=? AND decision='denied'",
                                (goal_id,)).fetchone()["c"],
        "budget": dict(conn.execute("SELECT * FROM budgets WHERE goal_id=?", (goal_id,)).fetchone()),
    }
