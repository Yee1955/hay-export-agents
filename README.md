# Hay Export Agents

A proof of concept for an oaten hay exporter, built with **Microsoft Agent Framework**, **Codex**, and **MCP**.
Give the CEO agent an instruction, review its plan, and watch the approved tasks run in separate threads. All company data is synthetic.

## Watch the demo

### Live app demo

https://github.com/user-attachments/assets/ca469711-55ae-4c59-a98a-21965375f203

The live run shows plan approval, agent handoffs, live tool calls, and the safety gate flagging a suspicious grower email. The agents reserve **380 t**, record an order for **120 t**, and flag **LOT-3** for re-drying. [Live demo MP4 (1:25, silent)](docs/hay-export-live-demo.mp4)

### Animated overview

https://github.com/user-attachments/assets/3e0f04d0-8807-4ec4-add8-d5aaf1d43e16

See how an instruction moves through the system, from approval and agent assignment to safety checks and the final report. [Animated overview MP4 (1:33, silent)](docs/hay-export-demo.mp4)

## Quick start

Requires Python 3.11 or later and the Codex CLI. Sign in with your ChatGPT account. No API key is required.

```bash
python -m pip install -e ".[dev]"
codex login
python -m hayagents.tools reset
python -m hayagents.web
```

Open [localhost:8000](http://localhost:8000). The reset command replaces the local database with fresh demo data.

Try the instruction from the recording:

> Saigon Dairy Co. needs 500 t of Vietnam-spec oaten hay by 15 Dec 2026. Reserve eligible stock first, then order only the shortfall. Separately, flag LOT-3 for re-drying in its stock note.

For the terminal interface, run `python -m hayagents.team`. Run `pytest -q` to test locally without a model connection.

## How a run works

1. **Give an instruction.** The CEO agent checks stock and grower offers and asks for clarification if needed. Its proposed tasks include completion criteria, deadlines, agent assignments, and model settings.
2. **Approve the plan.** Independent tasks run in parallel. Dependent steps, such as reserving stock before buying the shortfall, stay together in one task.
3. **Follow the work.** Each task has a Coordinator. The live cards show which agents are on turn and let you open tool calls to inspect their results.
4. **Review the outcome.** Check task results, exceptions, and total token use. Open **Workspace details** to see the agents, shed stock, and grower inbox. For a blocked task, **Ask the CEO agent** starts a follow-up instruction.

## Architecture

![Architecture diagram for Hay Export Agents](docs/architecture.png)

| Layer | How it works |
| --- | --- |
| Agent instructions | One factory builds every agent from its configuration row, including its role, instructions, tool permissions, and defaults. [Agent setup](hayagents/team.py) |
| Model routing | The CEO agent selects the model and effort for each agent on a task. The Coordinator uses its configured small model. [Routing](hayagents/team.py) |
| Skills | Agents load versioned skills for planning orders, checking export specs, reserving stock, choosing growers, and spotting suspicious messages. [Skills](skills/) |
| MCP and permissions | The mock company system exposes stock, inbox, and task tools over MCP. Each agent connects with a tool allowlist. At Balco, the proposed equivalent is a Business Central MCP server. [MCP server](hayagents/tools.py) |
| Approval and spending | Task creation requires approval. Procurement has a $50,000 default ordering limit, and a plan can carry a higher approved amount. [Order controls](hayagents/tools.py) |
| Safety and token limits | Tool results are checked for known prompt-injection patterns and redacted for bank details. A task stops before its next model call once its cumulative usage reaches 1 million tokens. [Gate](hayagents/gate.py) |
| Observability | Each tool call records the agent, tool, arguments, and a result excerpt. The web UI displays the trace and task progress. [Web UI](hayagents/web.html) |
