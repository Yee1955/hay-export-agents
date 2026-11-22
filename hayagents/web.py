"""The web interface.

    python -m hayagents.tools reset
    python -m hayagents.web           then open http://localhost:8000
"""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import uvicorn
from agent_framework import Agent, Message
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from . import tools
from .gate import active_turns
from .codex_client import CodexChatClient
from .team import agent, row, run_thread, task_efforts, task_models

state = {"history": [], "first": 1, "ceo_log": 0, "waiting": [], "runner": None,
         "instruction": None, "reply": None}   # where you are, so a page refresh picks up from here


def reply(response, approved=False):
    """What the CEO agent said, and the tasks it wants to create (its plan, waiting for your approval)."""
    state["waiting"] = response.user_input_requests
    plan = [r.function_call.parse_arguments() for r in state["waiting"]]
    for task in plan:
        task["efforts"], task["models"] = task_efforts(task), task_models(task)
    state["reply"] = {"text": response.text, "plan": plan, "approved": approved}
    return JSONResponse(state["reply"])


async def instruction(request):
    text = (await request.json())["text"]
    state["session"] = state["ceo"].create_session()          # one conversation per instruction
    state["first"] = tools.q("SELECT COALESCE(MAX(id), 0) + 1 n FROM tasks")[0]["n"]
    state["ceo_log"] = tools.q("SELECT COALESCE(MAX(rowid), 0) n FROM log")[0]["n"]   # the CEO agent's calls from here
    state["history"].append(text)
    state["instruction"], state["reply"] = text, None
    return reply(await state["ceo"].run(text, session=state["session"]))


async def answer(request):
    """Approve the plan, or type something: an answer to a question, or what to change."""
    body = await request.json()
    ceo, session, text = state["ceo"], state["session"], body.get("text")
    state["reply"] = None
    approved = bool(state["waiting"]) and (body["approve"] or await approves(text))
    if state["waiting"]:                          # your answer to the plan, and any feedback, in one message
        response = await ceo.run(Message("user", [*(r.to_function_approval_response(approved) for r in state["waiting"]),
                                                  *([text] if text and not approved else [])]), session=session)
    else:
        response = await ceo.run(text, session=session)
    if approved:
        state["runner"] = asyncio.create_task(run_tasks())   # every new task gets its own thread
    return reply(response, approved)


async def approves(text):
    """Does your typed reply approve the plan as it is? A small model reads it, so any wording works."""
    reader = row("reply_reader")
    judge = Agent(CodexChatClient(reader["model"], reader["effort"]), reader["instructions"])
    return (await judge.run(text)).text.strip().lower().startswith("yes")


async def next_instruction(request):
    state["instruction"] = None
    return JSONResponse({})


async def run_tasks():                            # all tasks at the same time, each in its own thread
    await asyncio.gather(*(run_thread(task) for task in
                           tools.q("SELECT * FROM tasks WHERE status = 'open' AND id >= ?", state["first"])))
    blocked = tools.q("SELECT id, goal, exceptions FROM tasks WHERE status = 'blocked' AND id >= ?", state["first"])
    if blocked:                                   # the CEO agent tells you what went wrong and asks what to do
        reply(await state["ceo"].run(f"These tasks are blocked: {blocked}. Tell the person why in a sentence each, "
                                     "and ask what to do.", session=state["session"]))


async def tasks(request):
    rows = tools.q("SELECT * FROM tasks WHERE id >= ?", state["first"])
    for row in rows:
        row["log"] = tools.q("SELECT rowid, * FROM log WHERE task_id = ? ORDER BY rowid", row["id"])
        row["efforts"], row["models"] = task_efforts(row), task_models(row)
        row["active_agents"] = sorted({name for task_id, name in active_turns.values() if task_id == row["id"]})
    ceo = tools.q("SELECT rowid, * FROM log WHERE task_id IS NULL AND rowid > ?", state["ceo_log"])
    working = bool(state["runner"]) and not state["runner"].done()
    return JSONResponse({"tasks": rows, "ceo": ceo, "working": working, "history": state["history"],
                         "default_efforts": {a["name"]: a["effort"] for a in tools.list_agents()},
                         "default_models": {a["name"]: a["model"] for a in tools.list_agents()},
                         "instruction": state["instruction"], "reply": state["reply"],
                         "stock": tools.read_stock(), "inbox": tools.read_inbox()})   # the company data, live


@asynccontextmanager
async def lifespan(app):
    async with AsyncExitStack() as stack:
        state["ceo"] = await agent("ceo_agent", None, stack)
        yield


app = Starlette(lifespan=lifespan, routes=[
    Route("/", lambda request: FileResponse(Path(__file__).with_name("web.html"))),
    Route("/instruction", instruction, methods=["POST"]),
    Route("/answer", answer, methods=["POST"]),
    Route("/next", next_instruction, methods=["POST"]),
    Route("/tasks", tasks)])

if __name__ == "__main__":
    uvicorn.run(app, port=8000)
