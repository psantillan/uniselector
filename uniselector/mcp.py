"""The agent surface: four tools on a Model Context Protocol server on stdio.

  uniselector-mcp --config uniselector.toml

The main agent hands off a plan with `plan_submit` and reads a bounded summary with `plan_status`.
It gets no raw data and no step history. `plan_answer` and `plan_confirm` are the two things that a
run can need from it.
"""
import argparse
import json
import os
import signal

import anyio
from datahandles import HandleError
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .config import load
from .plan import PLAN_SCHEMA
from .run import Harness

INSTRUCTIONS = (
    "Uniselector carries out a long task on DataHandles handles while your context stays small. State your intent as a short "
    "plan: a list of intents (sentences), and the handle ids to start from. Call plan_submit, then plan_status with wait_seconds. "
    "A decision model chooses the operations. plan_status gives a bounded summary. You must act only when a run has a pending question "
    "(plan_answer) or a pending confirmation of an action (plan_confirm), or when it returned the work to you.")

_RUN = {"type": "string", "description": "The run id from plan_submit."}
TOOLS = [
    {"name": "plan_submit", "description": "Start a run of a plan. Return the run id. The plan is a list of intents, not a list of operations.",
     "parameters": {"type": "object", "required": ["plan"], "properties": {"plan": PLAN_SCHEMA}}},
    {"name": "plan_status", "description": "Return a bounded summary of a run: its state, the plan and the current intent, a pending question or "
                                           "confirmation, the handles, the last results, and what you can do next. With wait_seconds, the call "
                                           "waits until the run needs you, ends, or the time is over. Use it: each call adds to your context.",
     "parameters": {"type": "object", "required": ["run"], "properties": {
         "run": _RUN, "wait_seconds": {"type": "number", "minimum": 0, "default": 0,
                                       "description": "Wait this long for the run. Stay below the tool-call timeout of your client."}}}},
    {"name": "plan_answer", "description": "Answer a pending question of a run: a value that a sub-agent could not find. The value must match the schema in the question.",
     "parameters": {"type": "object", "required": ["run", "question", "value"], "properties": {
         "run": _RUN, "question": {"type": "string", "description": "The id of the pending question."},
         "value": {"description": "The value of the field."}}}},
    {"name": "plan_confirm", "description": "Decide on a pending action of a run: approve it, deny it, or pass it to the human. "
                                            "Pass it to the human when you cannot judge it from the request.",
     "parameters": {"type": "object", "required": ["run", "request", "decision"], "properties": {
         "run": _RUN, "request": {"type": "string", "description": "The id of the pending request."},
         "decision": {"enum": ["approve", "deny", "human"]}}}},
]


def dispatch(harness: Harness, name: str, arguments: dict) -> dict:
    """Run one tool call. A failure is a value: {"error": {...}}."""
    try:
        if name == "plan_submit":
            return harness.submit(arguments["plan"])
        if name == "plan_status":
            return harness.status(arguments["run"], arguments.get("wait_seconds", 0))
        if name == "plan_answer":
            return harness.answer(arguments["run"], arguments["question"], arguments.get("value"))
        if name == "plan_confirm":
            return harness.confirm(arguments["run"], arguments["request"], arguments["decision"])
        return {"error": {"code": "INVALID_INPUT", "message": f"no tool {name!r}"}}
    except HandleError as e:
        return {"error": e.to_dict()}
    except (ValueError, KeyError, TypeError) as e:
        return {"error": {"code": "INVALID_INPUT", "message": str(e)}}


def server(harness: Harness) -> Server:
    tools = [types.Tool(name=t["name"], description=t["description"], input_schema=t["parameters"]) for t in TOOLS]

    async def list_tools(context, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def call_tool(context, params) -> types.CallToolResult:
        out = await anyio.to_thread.run_sync(dispatch, harness, params.name, params.arguments or {})
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(out, default=str))],
                                    structured_content=json.loads(json.dumps(out, default=str)), is_error="error" in out)

    return Server("uniselector", instructions=INSTRUCTIONS, on_list_tools=list_tools, on_call_tool=call_tool)


def main() -> None:
    p = argparse.ArgumentParser(description="Serve Uniselector to an MCP client on stdio.")
    p.add_argument("--config", default="uniselector.toml", help="the configuration file; it must state the hard limits")
    harness = Harness(load(p.parse_args().config))

    async def run() -> None:
        mcp_server = server(harness)
        async with stdio_server() as (read, write):
            await mcp_server.run(read, write, mcp_server.create_initialization_options())

    def leave(number, _frame):
        # The event loop cannot stop while a thread reads the input, so the handler ends the runs, then lets the signal end the process.
        harness.close()
        signal.signal(number, signal.SIG_DFL)
        os.kill(os.getpid(), number)

    # A client can end its servers with a signal. The runs must then end in the logs too (docs/DECISIONS.md, D25).
    for number in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, leave)
    try:
        anyio.run(run)
    finally:
        harness.close()


if __name__ == "__main__":
    main()
