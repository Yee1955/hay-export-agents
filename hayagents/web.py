"""The web interface.

    python -m hayagents.tools reset
    python -m hayagents.web           then open http://localhost:8000
"""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import uvicorn
from agent_framework import Message
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from . import tools
from .team import agent, run_thread

state = {"history": [], "first": 1, "ceo_log": 0, "waiting": [], "runner": None,
         "instruction": None, "reply": None}   # where you are, so a page refresh picks up from here


def reply(response):
    """What the CEO agent said, and the tasks it wants to create (its plan, waiting for your approval)."""
    state["waiting"] = response.user_input_requests
    state["reply"] = {"text": response.text, "plan": [r.function_call.parse_arguments() for r in state["waiting"]]}
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
    ceo, session = state["ceo"], state["session"]
    state["reply"] = None
    if state["waiting"]:
        response = await ceo.run(Message("user", [r.to_function_approval_response(body["approve"])
                                                  for r in state["waiting"]]), session=session)
    if body.get("text"):
        response = await ceo.run(body["text"], session=session)
    if body["approve"]:
        state["runner"] = asyncio.create_task(run_tasks())   # every new task gets its own thread
    return reply(response)


async def next_instruction(request):
    state["instruction"] = None
    return JSONResponse({})


async def run_tasks():
    for task in tools.q("SELECT * FROM tasks WHERE status = 'open' AND id >= ?", state["first"]):
        await run_thread(task)


async def tasks(request):
    rows = tools.q("SELECT * FROM tasks WHERE id >= ?", state["first"])
    for row in rows:
        row["log"] = tools.q("SELECT rowid, * FROM log WHERE task_id = ?", row["id"])
    ceo = tools.q("SELECT rowid, * FROM log WHERE task_id IS NULL AND rowid > ?", state["ceo_log"])
    working = bool(state["runner"]) and not state["runner"].done()
    return JSONResponse({"tasks": rows, "ceo": ceo, "working": working, "history": state["history"],
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
