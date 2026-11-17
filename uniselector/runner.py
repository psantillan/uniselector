"""The runners that work beside the loop: a model runner, and a callable of other code.

A runner is what an object is bound to. The main agent is a runner, and the human is a runner (for
an action only). Each other runner is a callable:

    runner(question, submit, spend, seconds)

`question` has the object and the reason of the wake. `submit(value)` gives the result to the run:
it returns None, or the fault of the value. `spend(tokens, cost)` reports the spend of one request
to a model: it returns the tokens that the run has left. `seconds` is the time that the run has
left. The core knows no more about a runner (docs/DECISIONS.md, D53).

A runner comes from a function that makes it: `make(client)`. `client` is the DataHandles client of
the orchestrator's principal, so a runner holds no action. `ModelRunner` is one such function. `load`
gets another one from code that the user or the main agent wrote: `FILE.py:NAME` or `MODULE:NAME`.

A runner does one of two things. At a wake about an object, it gives one replacement of that one
object. For a form of an output (a summary, an embedding), it gives the form: a small value, or
the id of a handle that holds it. The question has `form` then.

A model runner is a chat model from the configuration. One invocation is one request to the model
(docs/DECISIONS.md, D57). The model has no tools to read more, so no context grows. For a
replacement, the request has the graph that made the handle of the object, and the fault of each
node that fails now. The model names the nodes that must change. The runner runs the changed graph
one time, and the object gets the new handle. For a form, the request has the whole output.
"""
import importlib
import importlib.util
import json
import os
import urllib.request
from dataclasses import dataclass

from datahandles import HandleError

from .lineage import made
from .plan import OBJECT_SCHEMA

SYSTEM = (
    "You replace one broken object of a run that nobody watches. An object is a DataHandles handle and one operation that reads a small "
    "value from it. The message has the object, what it shows now, the sentences that are not true now, the graph that made its handle "
    "(`graph`), and the fault of each node of the graph that fails now (`faults_now`). You have this one request, and you cannot read "
    "more. Call `repair` with each operation node that must change: its id and its new operation. Change only what is broken. Each "
    "other node stays as it is, so the new version shows the same thing as the old one. Give `operation` only when the operation of "
    "the object itself must change. An object shows when its operation last ran, so a quiet object still shows that its data is live. "
    "If the object works and shows a right value, it has no break: the sentence is then about the state of the work, or about what "
    "the user wants. Call `to_main` with what you found. Do not change an object that works. If the message does not show the cause "
    "of a break, call `give_up` with the reason. Do not guess.")
FORM_SYSTEM = (
    "You make one form of the output of an object, for a reader that cannot read the whole output. An object is a DataHandles handle "
    "and one operation. The message has the object, the name of the form, the sentences that are true of a good form, and the whole "
    "output (`whole_output`). You have this one request. Call `form` with the value of the form. Keep it small. State only what the "
    "output shows. If you cannot make the form, call `give_up` with the reason. Do not guess.")
_FORM = {"type": "function", "function": {
    "name": "form", "description": "Give the form of the output.",
    "parameters": {"type": "object", "required": ["value"], "properties": {"value": {"description": "The form: a text or a small JSON value."}}}}}
_REPAIR = {"type": "function", "function": {
    "name": "repair", "description": "Give the replacement of the object. The name of the object stays.",
    "parameters": {"type": "object", "properties": {
        "nodes": {"type": "array", "description": "Each operation node of the graph that must change.", "items": {
            "type": "object", "required": ["id", "operation"], "properties": {
                "id": {"type": "string", "description": "The id of the node in the graph."},
                "operation": OBJECT_SCHEMA["properties"]["operation"]}}},
        "operation": OBJECT_SCHEMA["properties"]["operation"]}}}}
_GIVE_UP = {"type": "function", "function": {
    "name": "give_up", "description": "You cannot do it. The next runner of the object gets the wake.",
    "parameters": {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string"}}}}}
_TO_MAIN = {"type": "function", "function": {
    "name": "to_main", "description": "Give the wake to the main agent: the object works, or the repair needs a decision about what the user wants.",
    "parameters": {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string"}}}}}


@dataclass
class Outcome:
    calls: list     # the one call of the model, and each run of a graph, for the log
    reason: str     # how the wake ended for this runner
    main: bool = False   # the wake is for the main agent: the runner did not fail


def load(spec: str, client, **arguments):
    """The runner from code outside this package. `spec` names the function that makes it: `FILE.py:NAME` or `MODULE:NAME`."""
    where, _, name = spec.rpartition(":")
    try:
        if where.endswith(".py"):   # a file gets a new module at each load, so the newest text of the file runs
            found = importlib.util.spec_from_file_location("uniselector_loaded", where)
            module = importlib.util.module_from_spec(found)
            found.loader.exec_module(module)
        else:
            module = importlib.import_module(where)
        runner = getattr(module, name)(client, **arguments)
    except Exception as e:   # the code of another author: each fault of it goes back as one message
        raise ValueError(f"the runner {spec!r} did not load: {type(e).__name__}: {e}") from e
    if not callable(runner):
        raise ValueError(f"the runner {spec!r} did not load: {name}(client) must return a function runner(question, submit, spend, seconds)")
    return runner


def make(table: dict, client):
    """The runner of one `[runners.NAME]` table of the configuration. With `call`, the table names a function that makes the runner,
    and each other key is an argument of that function. With no `call`, the table is a model."""
    if "call" in table:
        return load(table["call"], client, **{k: v for k, v in table.items() if k != "call"})
    return ModelRunner(table["url"], table["model"], os.environ[table["key_env"]] if "key_env" in table else None, client, table.get("body"))


class ModelRunner:
    def __init__(self, url: str, model: str, key: str | None, client, body: dict | None = None):
        self.url, self.model, self.key = url, model, key
        self.client = client     # the DataHandles client of the orchestrator's principal
        self.body = body or {}   # more keys for each request, when the server of the model needs them

    def chat(self, system: str, question: dict, tools: list, timeout: float) -> dict:
        """One request to a chat completions API in the OpenAI form."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(question, indent=1, default=str)}]
        body = {"model": self.model, "messages": messages, "tools": tools, "temperature": 0, **self.body}
        headers = {"Content-Type": "application/json", "User-Agent": "uniselector", **({"Authorization": f"Bearer {self.key}"} if self.key else {})}
        with urllib.request.urlopen(urllib.request.Request(self.url, json.dumps(body).encode(), headers), timeout=timeout) as response:
            return json.load(response)

    def run(self, graph: dict, out: Outcome) -> dict:
        """Run a graph one time. Return the handle of each node, and the fault of each node that failed."""
        result = self.client.submit(graph)
        faults = {node: r["error"] for node, r in result["results"].items() if r["status"] == "failed"}
        out.calls.append({"graph": [n for n in graph["nodes"] if n["kind"] == "operation"], "faults": faults})
        return {"handles": result["handles"], "faults": faults}

    def __call__(self, question: dict, submit, spend, seconds: float) -> Outcome:
        """One replacement of an object, or one form of an output: one request to the model."""
        out, what, target = Outcome([], ""), "form" if "form" in question else "repair", question["object"]
        try:
            if what == "form":   # the state keeps a part of a long output. DataHandles bounds the size of the whole one
                more = {"whole_output": self.client.resolve(target["handle"]).invoke(target["operation"]["capability"], target["operation"].get("input", {}))}
            else:
                # A describe of a source can be old (docs/GAPS.md, G18). One run of the graph gives the fault of each node as it is now.
                more = {"faults_now": self.run(question["graph"], out)["faults"] if made(question["graph"]) else {}}
            if spend(0) <= 0:
                out.reason = f"the run has no tokens left for a {what}"
                return out
            reply = self.chat(FORM_SYSTEM if what == "form" else SYSTEM, {**question, **more},
                              [_FORM, _GIVE_UP] if what == "form" else [_REPAIR, _GIVE_UP, _TO_MAIN], seconds)
            spend(reply["usage"]["total_tokens"], reply["usage"].get("cost") or 0.0)
            message = reply["choices"][0]["message"]
            if not message.get("tool_calls"):
                out.calls.append({"tool": None, "text": message.get("content") or ""})
                out.reason = f"the runner gave no {what}: {message.get('content') or 'it gave no text'}"
                return out
            name, arguments = message["tool_calls"][0]["function"]["name"], json.loads(message["tool_calls"][0]["function"]["arguments"] or "{}")
            out.calls.append({"tool": name, "arguments": arguments})
            if name in ("give_up", "to_main"):
                out.main = name == "to_main"
                out.reason = f"the runner gave {'the wake to the main agent' if out.main else 'up'}: {arguments.get('reason', '')}"
                return out
            if what == "form":
                fault = submit(arguments.get("value"))
            else:
                fault = self.replace(question, arguments, submit, out)
        except (OSError, ValueError, HandleError) as e:   # the model did not answer, or its answer was not usable: this binding is not adequate
            out.reason = f"the runner failed: {type(e).__name__}: {e}"
            return out
        out.reason = f"the run did not take the {what}: {fault}" if fault else what
        return out

    def replace(self, question: dict, arguments: dict, submit, out: Outcome) -> str | None:
        """Change the nodes that the model named, run the graph one time, and give the new handle to the run. Return a fault, or None."""
        graph, target = json.loads(json.dumps(question["graph"])), question["object"]
        nodes = {n["id"]: n for n in graph["nodes"] if n["kind"] == "operation"}
        for change in arguments.get("nodes", []):
            if change["id"] not in nodes:
                return f"the graph has no operation node {change['id']!r}"
            nodes[change["id"]]["operation"] = change["operation"]
        handle = target["handle"]
        if arguments.get("nodes"):
            result = self.run(graph, out)
            if handle not in result["handles"]:
                return f"the changed graph failed: {json.dumps(result['faults'], default=str)}"
            handle = result["handles"][handle]
        elif "operation" not in arguments:
            return "the answer changes nothing"
        return submit({"handle": handle, "operation": arguments.get("operation", target["operation"])})
