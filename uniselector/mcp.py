"""The agent surface: four tools on a Model Context Protocol server on stdio.

  uniselector-mcp --config uniselector.toml

The main agent hands off its objects and the essential variables with `plan_submit`, and it sleeps
in `plan_status`. It gets no raw data and no history of the operations. `plan_answer` and
`plan_confirm` are the two things that a run can need from it.
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
from .plan import ANSWER_SCHEMA, PLAN_SCHEMA
from .run import Harness

INSTRUCTIONS = (
    "Uniselector watches objects that you built, so that you do not monitor. Do not read data again and again to see if something "
    "changed. Build the thing that monitors, then leave. First build each object with the DataHandles tools: a handle (for example a "
    "filter and an aggregate on a dataset) and one operation on it that gives a small value: the run keeps only the first and the last "
    "part of a long output. Each look of the decision model costs tokens, and a look comes at each change of an output: prefer an "
    "object whose output changes only when the work needs attention. An object shows when its operation last ran (`ran_s`), so a "
    "variable can state that a quiet object still gets new data. A handle that you derive reads the newest data of its source at each "
    "read. Then state the essential variables: sentences that are true while you can sleep. For a goal, state that the goal is not "
    "met yet (\"The object `rows` shows fewer than 600 rows.\"): the wake comes when the sentence is not true. Call "
    "plan_submit, then plan_status with wait_seconds. The objects run without you, and a decision model judges the variables. It "
    "wakes you when one is not true. The summary then has the reason and the objects. Build a new version of only the object that is "
    "broken, write it back with plan_answer, and sleep again. An object can carry a check of its own (`variables`) and the runners "
    "that answer for it, in order (`runners`). A runner costs nothing while each variable is true. At a wake about the object, the "
    "first runner gives one replacement of it, and the next look judges the replacement with the check of the object. A runner whose "
    "replacement does not pass leaves the list. After the last one, the wake comes to you. "
    "A runner is a model of the configuration, or a function of your code (`FILE.py:NAME`) when the configuration lets a plan name "
    "code. A check rejects only what it states: for an object with runners, state each condition that a replacement must keep. "
    "State its meaning (\"It sums only the paid orders.\"), and do not name a column whose name can change: a sentence with the old "
    "name rejects the right replacement. An object can also carry forms of its output (`forms`), for example a summary: a runner "
    "makes each form one time before a wake, and the wake has it. A form does not help the decision model to find a failure: when "
    "an output is longer than a summary shows, build an object with a small output. "
    "The check of an object says only that the object works. A sentence that tells you that the work is done, or that needs your "
    "decision, is a variable of the plan: it has no object, and its wake comes to you at once, also while a runner repairs an object. "
    "So do not state the check of an object again as a variable of the plan. A model runner can change operations in the graph that "
    "made the handle of an object, and the operation of the object. It cannot add or remove a step, and it cannot change the data. A "
    "check that only a change of the data can make true again is a variable of the plan.")

# One more part of the instructions, for a configuration that reports the context of the main agent to the run (docs/DECISIONS.md, D66).
CONTEXT = (
    "This configuration reports your context to the run. An object can carry the form `compaction`. Its check is a sentence about "
    "your context (\"The context of the main agent is below 85% of the tokens at which it compacts by itself.\"), and its runner "
    "compacts your context when the sentence is not true. The runner costs nothing while the sentence is true.")

_RUN = {"type": "string", "description": "The run id from plan_submit."}
TOOLS = [
    {"name": "plan_submit", "description": "Start a run of a plan. Return the run id. The plan is the objects that you built and the essential variables.",
     "parameters": {"type": "object", "required": ["plan"], "properties": {"plan": PLAN_SCHEMA}}},
    {"name": "plan_status", "description": "Return a bounded summary of a run: its state, the variables, what each object shows now, a pending wake or "
                                           "confirmation, and what you can do next. With wait_seconds, the call waits until the run needs you, "
                                           "ends, or the time is over. Use it: each call adds to your context.",
     "parameters": {"type": "object", "required": ["run"], "properties": {
         "run": _RUN, "wait_seconds": {"type": "number", "minimum": 0, "default": 0,
                                       "description": "Wait this long for the run. Stay below the tool-call timeout of your client."}}}},
    {"name": "plan_answer", "description": "Answer a wake of a run: write back the new version of what is broken. Only what you name changes.",
     "parameters": {"type": "object", "required": ["run", "question", "value"], "properties": {
         "run": _RUN, "question": {"type": "string", "description": "The id of the pending wake."},
         "value": ANSWER_SCHEMA}}},
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


def instructions(harness: Harness) -> str:
    """The instructions, with the names of the runners that an object can name."""
    names = ", ".join(f"`{name}`" for name in sorted(harness.runners))
    context = f" {CONTEXT}" if harness.cfg.main_context else ""
    return f"{INSTRUCTIONS}{context} The runners of this configuration: `main` (you){', ' + names if names else ''}, and `human` (for an action only)."


def server(harness: Harness) -> Server:
    tools = [types.Tool(name=t["name"], description=t["description"], input_schema=t["parameters"]) for t in TOOLS]

    async def list_tools(context, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def call_tool(context, params) -> types.CallToolResult:
        out = await anyio.to_thread.run_sync(dispatch, harness, params.name, params.arguments or {})
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(out, default=str))],
                                    structured_content=json.loads(json.dumps(out, default=str)), is_error="error" in out)

    return Server("uniselector", instructions=instructions(harness), on_list_tools=list_tools, on_call_tool=call_tool)


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
