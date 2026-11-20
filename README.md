# hay-export-agents

A small, working version of a governed multi-agent system for an oaten hay exporter.
People own the goals and the decisions. AI agents do the legwork. Plain code holds the rules.

Built as an interview demo for a Graduate AI Engineer role. All data is synthetic.

```
hay demo            # live: real agents on the Codex SDK
hay demo --replay   # offline: recorded agent decisions, real MCP server, gate and database
```

## What it does

The CEO confirms one goal: *ship 500 t of Vietnam-spec oaten hay to Saigon Dairy by 15 Dec*.

1. **Plain code** works out the shortfall: `max(0, demand - eligible stock - firm inbound)`
   = 500 - 380 - 20 = **100 t**. Reserved and out-of-spec lots don't count.
2. The **CEO agent** drafts a task plan (procurement, production, sales, finance), each task with
   a deadline and a completion check. The server rejects vague or wrong plans. **The CEO approves it.**
3. Each department has a **human manager** with an **agent** under them. The **procurement agent**
   reads stock, quotes and the `pick-a-grower` skill, then proposes one order. It never places it.
4. The inbox holds a **trick email** asking to change bank details and order 300 t.
   The agent flags it. A scripted "fooled agent" then tries every request in it, and each one is refused.
5. The server prices the order ($20,500) and picks the **approval tier**. The wrong manager is refused;
   the procurement manager approves.
6. The system places the order, then the reply is **lost after the save**. A retry returns the
   **same PO**, with no duplicate.
7. **Plain code verifies** the task against the ERP state, and the CEO agent reports back:
   done / waiting / needs a decision.

## How it fits together

![Architecture](docs/architecture.png)

| File | What it does |
|---|---|
| `hayagents/server.py` | The MCP server and its safety gate. Every tool is written once; each agent sees only its own |
| `hayagents/people.py` | What people do: confirm the goal, approve the plan, sign orders. Plus the done-check and the report |
| `hayagents/rules.py` | Hard rules as plain Python: shortfall, spending tiers, market specs, plan checks, budget, models |
| `hayagents/agents.py` | Runs an agent turn on the Codex SDK (locked down), records it, replays it through MCP |
| `hayagents/db.py` | Mock Business Central in SQLite, plus the audit log |
| `hayagents/cli.py` | `hay demo`: the whole story, step by step |
| `hayagents/prompts/` | What each agent is told |
| `skills/pick-a-grower/SKILL.md` | Team know-how with a version and an owner |

The diagram source is `docs/architecture.svg`.

## Design choices

- **The AI suggests, code decides.** Agents can read and propose. Writes happen only after a person
  approves, through the operator path, and the server loads the approved terms itself.
- **Rules live in code, not prompts.** Spending tiers, specs, the shortfall maths and "no bank changes"
  are Python and YAML. A prompt can be talked out of a rule; the gate can't.
- **Two signatures for big orders.** Under $10k is automatic, $10k to $50k needs the procurement manager,
  over $50k needs the procurement manager **and** the CEO on the same terms. The tier is recomputed from the
  amount at approval and again at execution, never trusted from storage.
- **Approval is bound to exact terms.** A fingerprint of supplier, tonnes, price, amount, currency and
  delivery date is saved with the proposal. Any change after signing needs new signatures.
- **Rechecked at execution.** If stock changes between signing and placing the order, it's refused:
  the signatures were for 100 t, the gap is now 70 t. The grower's quote (vendor, price, date, capacity)
  is rechecked too. The CEO approves the one waiting plan draft; a draft can't be swapped.
- **Safe retries.** A purchase order's `proposal_id` is UNIQUE, so a retry returns the same PO. The PO and its
  audit row are saved in one transaction. Refused calls still count against the budget.
- **Least privilege for agents.** An agent only sees its own tools, only works on its own task, and can
  only message agents on a linked task. A message from another agent is information, never approval.
- **Skills vs rules.** Skills hold judgement ("prefer the more reliable grower if the price gap is under 8%"),
  with a version and an owner. Hard limits stay in `rules.py`, where the AI can't talk its way past them.

### Why the Codex SDK

The agents run on the [official Codex Python SDK](https://pypi.org/project/openai-codex/) with a ChatGPT
sign-in, so there's no API bill. Codex runs the tool loop; the MCP server is its only route to data:

- read-only sandbox, approvals denied, empty working folder
- shell, apps, plugins, browser, image and multi-agent features switched off (`LOCKED` in `agents.py`)
- MCP tool approval is left to the gate (`default_tools_approval_mode = "approve"`), so the
  real checks happen server-side where the agent can't skip them

Model routing is set per agent in `rules.py` and logged on every turn. Only one provider
has been tested, so this demo does **not** claim vendor portability yet.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
codex login            # once; the SDK reuses your Codex / ChatGPT sign-in
pytest -q              # 32 tests, no model needed
hay demo               # live, about 2 minutes, pauses between steps
hay demo --replay      # offline
hay demo --tonnes 450  # change the goal: the shortfall becomes 50 t, nothing else edited
```

## What's real and what's mocked

| Ad responsibility | In this demo | In production |
|---|---|---|
| CEO advisory agent | Built: plans tasks with checks and deadlines; reports done / waiting / decisions | Same pattern, more criteria |
| Department agents | Procurement built; production is a message recipient; sales and finance are people-owned tasks | One agent per department, each with its own tool scope |
| Skill library | One skill, with a version and an owner | Skills written with site managers; agent-suggested edits signed off by the owner |
| MCP to Business Central | Mock ERP behind a local MCP server | Microsoft's Business Central MCP server; validate tenant auth, company scope, exposed API objects and create permissions, then create and read back a draft PO in a sandbox |
| Email, banking, shipping, shift reports | Inbox table only (untrusted) | Proposed read-only scopes: approved mailbox, reconciliation data, booking status and ETA, site-scoped shift reports |
| Approval gates, authority limits | Built, tiered | Real identities (Entra ID) instead of a simulated approver name |
| Audit trail | Built, in SQLite | Protected, append-only store |
| Per-task cost caps | Token and tool-call budget per goal, checked before each model turn and each tool call | Dollar caps with reserve-then-reconcile |
| Model routing, no lock-in | Per-agent model and effort, logged; one provider tested | Test the same flow on a second provider before claiming portability |

**Trust boundary of the demo:** one trusted local operator, simulated roles, synthetic data.

See [WHAT_WENT_WRONG.md](WHAT_WENT_WRONG.md) for the real bugs found while building it.
