"""A model runner: a model that answers for an object at a wake.

A runner is the agent that an object is bound to. The main agent is a runner, the human is a runner
(for an action only), and a model runner is a chat model from the configuration. At a wake, a model
runner gets the object and the reason. It finds the cause with the DataHandles tools, as the
orchestrator's principal, so it holds no action. It writes back a new version of that one object.

A wake of a model runner is one turn of an agent. It ends when the model calls `repair` with a
version that the run takes, when the model calls `give_up` or `to_main`, or when the model answers
with no call of a tool. It ends also at a limit: the tokens of one wake, the tokens of the run, or the time of
the run.

Another runner can replace `ModelRunner`: it must be a callable with the same arguments and the
same result.
"""
import json
import time
import urllib.request
from dataclasses import dataclass

from datahandles.protocols.tools import FIND_TOOL, TOOLS, dispatch

from .plan import OBJECT_SCHEMA

SYSTEM = (
    "You repair one object of a run that nobody watches. An object is a DataHandles handle and one operation that reads a small value "
    "from it. The message has the object, what it shows now, and the sentences that are not true now. Find the cause with the tools: "
    "describe the handle of the object, and read small values. `sources` names each handle that the handle of the object was made "
    "from: describe them too. Build a new handle when the object needs one, for example a new filter on a source. Read the new handle first, so that you know that it works. Then call `repair` "
    "with the handle and the operation of the new version. Change only what is broken: the new version shows the same thing as the old one. "
    "An object shows when its operation last ran, so a quiet object still shows that its data is live. If the object works and shows a right value, it has no break: the sentence is then about the "
    "state of the work, or about what the user wants. Call `to_main` with what you found. Do not change an object that works. If you "
    "cannot find the cause of a break, call `give_up` with the reason. Do not guess.")
_FINAL = [
    {"type": "function", "function": {
        "name": "repair", "description": "Write back the new version of the object. The name of the object stays.",
        "parameters": {"type": "object", "required": ["handle", "operation"], "properties": {
            "handle": {"type": "string", "description": "The id of the handle of the new version."},
            "operation": OBJECT_SCHEMA["properties"]["operation"]}}}},
    {"type": "function", "function": {
        "name": "give_up", "description": "Give the wake to the next runner of the object.",
        "parameters": {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "to_main", "description": "Give the wake to the main agent: the object works, or the repair needs a decision about what the user wants.",
        "parameters": {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string"}}}}},
]


@dataclass
class Outcome:
    calls: list     # each call of a tool, with its result, for the log
    reason: str     # how the wake ended for this runner
    main: bool = False   # the wake is for the main agent: no other runner of the object gets it


class ModelRunner:
    def __init__(self, url: str, model: str, key: str | None, wake_tokens: int, client, body: dict | None = None):
        self.url, self.model, self.key, self.wake_tokens = url, model, key, wake_tokens
        self.client = client     # the DataHandles client of the orchestrator's principal
        self.body = body or {}   # more keys for each request, when the server of the model needs them

    def chat(self, messages: list, timeout: float) -> dict:
        """One request to a chat completions API in the OpenAI form."""
        body = {"model": self.model, "messages": messages, "tools": [FIND_TOOL, *TOOLS, *_FINAL], "temperature": 0, **self.body}
        headers = {"Content-Type": "application/json", "User-Agent": "uniselector", **({"Authorization": f"Bearer {self.key}"} if self.key else {})}
        with urllib.request.urlopen(urllib.request.Request(self.url, json.dumps(body).encode(), headers), timeout=timeout) as response:
            return json.load(response)

    def __call__(self, question: dict, submit, spend, seconds: float) -> Outcome:
        """One wake. `submit(value)` gives the repair to the run: it returns None, or the fault of the value. `spend(tokens, cost)`
        reports the spend of one request: it returns the tokens that the run has left. `seconds` is the time that the run has left."""
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(question, indent=1)}]
        out, used, left, end = Outcome([], ""), 0, spend(0), time.monotonic() + seconds
        while used < self.wake_tokens and left > 0 and time.monotonic() < end:
            try:
                reply = self.chat(messages, end - time.monotonic())
            except OSError as e:   # the model did not answer: this binding is not adequate now
                out.reason = f"the model of the runner failed: {type(e).__name__}: {e}"
                return out
            used += reply["usage"]["total_tokens"]
            left = spend(reply["usage"]["total_tokens"], reply["usage"].get("cost") or 0.0)
            message = reply["choices"][0]["message"]
            messages.append(message)
            if not message.get("tool_calls"):   # the turn of the model ended, as the turn of an agent ends
                out.calls.append({"tool": None, "text": message.get("content") or ""})
                out.reason = f"the runner stopped with no repair: {message.get('content') or 'it gave no text'}"
                return out
            for call in message["tool_calls"]:
                name = call["function"]["name"]
                try:
                    arguments = json.loads(call["function"]["arguments"] or "{}")
                except ValueError:
                    arguments, result = None, {"error": "the arguments are not JSON"}
                else:
                    if name in ("give_up", "to_main"):
                        out.calls.append({"tool": name, "arguments": arguments})
                        out.main = name == "to_main"
                        out.reason = f"the runner gave {'the wake to the main agent' if out.main else 'up'}: {arguments.get('reason', '')}"
                        return out
                    if name == "repair":
                        fault = submit(arguments)
                        result = {"error": fault} if fault else {"accepted": True}
                    else:
                        result = dispatch(self.client, name, arguments)   # DataHandles bounds the size of each value
                out.calls.append({"tool": name, "arguments": arguments, "result": result})
                if result == {"accepted": True}:
                    out.reason = "repair"
                    return out
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, default=str)})
        out.reason = (f"the runner reached a limit with no repair: it spent {used} tokens, one wake can spend {self.wake_tokens}, "
                      f"and the run has {left} tokens and {max(round(end - time.monotonic()), 0)} s left")
        return out
