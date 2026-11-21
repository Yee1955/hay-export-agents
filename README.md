# hay-export-agents

A small proof of concept: AI agents for an oaten hay exporter, where **agents ask, tools check, then act**.
All data is synthetic.

![Architecture](docs/architecture.png)

## The story

The goal: ship **500 t** of Vietnam-spec oaten hay to Saigon Dairy by 15 Dec.

1. **The CEO agent** reads the goal and splits it into one task per department. The shortfall is worked out by
   plain code, never by the AI: `max(0, demand - eligible stock - firm inbound)` = 500 - 380 - 20 = **100 t**.
   Reserved and out-of-spec lots don't count.
2. **The procurement agent** reads its task and the grower quotes, follows the `pick-a-grower` skill, and orders
   100 t from the more reliable grower ($20,500).
3. **A trick email** in the inbox asks for new bank details and a 300 t order. The agent flags it, and the gate
   would refuse it anyway: the order must equal the shortfall, and there is no tool for bank details.

## Run it

```bash
pip install -e ".[dev]"
codex login                                    # once; uses your ChatGPT sign-in, no API key
pytest -q                                      # 20 tests of the gate and rules, no model needed

python -m hayagents.data                       # fresh data (or: python -m hayagents.data 450)
python -m hayagents.agents ceo_agent           # plans the tasks
python -m hayagents.agents procurement_agent   # places the order
sqlite3 hay.db "select actor, action, decision, reason from events"   # the audit log
```

## The three files

| File | Job |
|---|---|
| `hayagents/agents.py` | Runs one agent on the Codex SDK. Locked down: read-only, no shell, apps or browser. MCP tools are its only way in |
| `hayagents/tools.py` | The MCP server. Each tool goes through the **gate** first: only your own tools and task, budget left. Then the tool's own rules: exactly the shortfall, a valid quote, under the $50k authority limit |
| `hayagents/data.py` | Mock Business Central (SQLite, names after BC API v2.0) and the audit log |

Plus `hayagents/prompts/` (what each agent is told) and `skills/` (team know-how with a version and an owner).

## How it covers the agent layers

| Layer | Where |
|---|---|
| System prompt | `prompts/*.md` |
| Read | Tools: task, quotes, inbox (labelled untrusted), skill |
| MCP tools | `tools.py`; each agent only sees its own |
| Permission | Tool allow-list per agent, own task only, $50k authority limit |
| Gate | Every call checked in `gate()`, plus each tool's rules; the trick email can't get past them |
| Route and cost cap | Model and reasoning effort per agent (`MODELS`); token and tool-call budget per goal |
| Observability | Audit log of every call, allowed or refused, with the reason and tokens used |

Not built, on purpose: people in the loop (orders over the limit are simply refused), document search, caching.
In production, the mock would be Microsoft's Business Central MCP server, and approvals would come back as a
step that waits for a manager.

See [WHAT_WENT_WRONG.md](WHAT_WENT_WRONG.md) for the real bugs found while building it.
