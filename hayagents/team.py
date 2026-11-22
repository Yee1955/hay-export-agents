"""The team. Every agent is the same Agent, built from its row in the agents table (see tools.py).

    python -m hayagents.tools reset    fresh data
    python -m hayagents.team           give the CEO agent an instruction
"""
import asyncio
import json
import sys
from contextlib import AsyncExitStack

from agent_framework import Agent, FileSkillsSource, MCPStdioTool, Message, SkillsProvider
from agent_framework_orchestrations import MagenticBuilder

from . import tools
from .codex_client import CodexChatClient
from .gate import Gate


def row(name):
    """An agent's setup: its row in the agents table."""
    return tools.q("SELECT * FROM agents WHERE name = ?", name)[0]


def choice(table, requested, default):
    """The CEO agent's choice for this task if it's in the models or efforts table, otherwise the agent's default."""
    return requested if requested and tools.q(f"SELECT 1 FROM {table} WHERE name = ?", requested) else default


def agent_effort(name, requested=None):
    return choice("efforts", requested, row(name)["effort"])


def agent_model(name, requested=None):
    return choice("models", requested, row(name)["model"])


def task_efforts(task):
    return task_choices(task, "efforts", agent_effort)


def task_models(task):
    return task_choices(task, "models", agent_model)


def task_choices(task, column, pick):
    """What each agent on the task uses: the CEO agent's choices, or the agents' defaults."""
    requested = task.get(column) or {}
    if isinstance(requested, str):
        try:
            requested = json.loads(requested)
        except json.JSONDecodeError:
            requested = {}
    if not isinstance(requested, dict):
        requested = {}
    names = task.get("agents") or []
    if isinstance(names, str):
        names = names.split(",")
    known = {a["name"] for a in tools.list_agents()}
    chosen = {name: pick(name, requested.get(name)) for name in names if name in known}
    chosen["coordinator"] = pick("coordinator")
    return chosen


async def agent(name, task_id, stack, effort=None, model=None) -> Agent:
    """Build an agent from its row. The stack closes it, and its connection to the company system, when done."""
    a = row(name)
    company_system = MCPStdioTool(name="company_system", command=sys.executable, args=["-m", "hayagents.tools", name],
                                  allowed_tools=a["tools"].split(","),         # its permissions
                                  approval_mode={"always_require_approval": ["create_task"]})  # you approve tasks
    return await stack.enter_async_context(Agent(
        CodexChatClient(agent_model(name, model), agent_effort(name, effort)), a["instructions"], name=name, description=a["job"],
        tools=[company_system],
        context_providers=[SkillsProvider(FileSkillsSource("skills"), disable_load_skill_approval=True)],
        middleware=Gate(name, task_id).middleware))


async def run_thread(task):
    """A task's thread: its agents work together, with a small coordinator, until the task is done."""
    print(f"\nTask {task['id']}: {task['goal']}")
    async with AsyncExitStack() as stack:
        efforts, models = task_efforts(task), task_models(task)      # chosen by the CEO agent for this task
        team = [await agent(name, task["id"], stack, efforts.get(name), models.get(name))
                for name in task["agents"].split(",") if name != "coordinator"]
        workflow = MagenticBuilder(participants=team, manager_agent=await agent("coordinator", task["id"], stack),
                                   max_round_count=10).build()
        try:
            await workflow.run(f"Task {task['id']}: {task['goal']}\nDone when: {task['completion_criteria']}\n"
                               f"Deadline: {task['deadline']}\n"
                               "It's finished only when one of you sets it done, or blocked, with update_task.")
        except Exception as e:                    # e.g. the cost cap
            tools.update_task(task["id"], "blocked", exception=str(e))
    if tools.read_task(task["id"])["status"] == "open":   # the thread ended, but nobody finished the task
        tools.update_task(task["id"], "blocked", exception="The thread ended before the task was done.")
    print(f"Task {task['id']}: {tools.read_task(task['id'])['status']}")


async def main():
    """Wait for your input, one instruction at a time. Ctrl+C to quit."""
    async with AsyncExitStack() as stack:
        ceo = await agent("ceo_agent", None, stack)
        session = ceo.create_session()
        while True:
            response = await ceo.run(input("\nYou: "), session=session)
            while response.user_input_requests:   # a task waiting for your approval
                answers = [request.to_function_approval_response(
                               input(f"\nApprove {request.function_call.arguments}? [y/n] ") == "y")
                           for request in response.user_input_requests]
                response = await ceo.run(Message("user", answers), session=session)
            print(f"\nceo_agent: {response.text}")
            await asyncio.gather(*(run_thread(task) for task in   # every new task gets its own thread,
                                   tools.q("SELECT * FROM tasks WHERE status = 'open'")))  # all at once


if __name__ == "__main__":
    asyncio.run(main())
