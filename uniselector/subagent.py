"""The sub-agent: it finds the value that the candidate pool did not have.

The interface is: a question in, a value or `needs_planner` out. `ModelAsker` is the first
implementation: a small agent loop on the local model, with a fresh context for each question. Its
tools are the DataHandles agent tools (`handle_find`, `handle_describe`, `handle_invoke`), the same
surface that the DataHandles MCP server gives. It runs as its own principal, which holds only
capabilities that do not write. A headless coding agent with the DataHandles MCP server can replace
it: it must be a callable with the same arguments and the same result.
"""
import json
import urllib.request

from datahandles.protocols.tools import FIND_TOOL, TOOLS, dispatch

from .plan import violations
from .state import cut

SYSTEM = (
    "You find one value for an operation on a data handle. Another program fills the input of the operation and could not "
    "choose the value of one field. You have tools to describe handles and to invoke read operations on them. A handle is a "
    "reference to a resource: an operation gives a small value or a new handle. Use the tools to look at the data until the "
    "data shows the value. For example, group by a column to see its values. Do not guess. You find facts. You do not decide "
    "what the user wants: if the value depends on a wish that the plan does not state, or if the data does not show the "
    "value, call needs_planner. When the data shows the value, call answer.")

_FINAL = [
    {"type": "function", "function": {
        "name": "answer", "description": "Give the value of the field. Use it when a tool result shows the value.",
        "parameters": {"type": "object", "required": ["value", "reason"], "properties": {
            "value": {"description": "The value. It must match the schema of the field."},
            "reason": {"type": "string", "description": "One sentence: which result shows the value."}}}}},
    {"type": "function", "function": {
        "name": "needs_planner", "description": "Return the question to the main agent. Use it when the value depends on what the user wants "
                                                "and the plan does not state it, or when the data does not show the value.",
        "parameters": {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string"}}}}},
]


def question_text(q: dict) -> str:
    """The question in the form that a sub-agent or the main agent reads."""
    lines = ["question:", "  plan:"]
    lines += [f"    {i + 1}. {text}" for i, text in enumerate(q["plan"]["intents"])]
    if q["plan"].get("notes"):
        lines.append(f"    notes: {q['plan']['notes']}")
    lines.append(f"  intent:     {q['intent']}")
    if q.get("capability"):
        lines.append(f"  capability: {q['capability']} on {q['target']['alias']} ({q['target']['id']})")
        lines.append(f"  field:      {q['field']}")
        lines.append(f"  chosen:     {json.dumps(q['chosen'])}")
    else:
        lines.append(f"  decision:   which {q['node']} is next for the intent")
    lines.append(f"  schema:     {json.dumps(q['schema'])}")
    lines.append("  candidates: " + "; ".join(json.dumps(c["value"]) for c in q["candidates"]))
    lines.append("  handles:    " + "; ".join(f"{h['alias']} = {h['id']} ({h['type']}, {h['origin']})" for h in q["handles"]))
    return "\n".join(lines)


class ModelAsker:
    def __init__(self, cfg, client):
        self.url, self.model, self.num_ctx = cfg.model_url.rstrip("/"), cfg.subagent_model or cfg.model, cfg.num_ctx
        self.client = client   # the DataHandles client of the sub-agent's principal
        # One tool result gets a quarter of the context at most. One token is about 3 characters of JSON.
        self.result_chars = self.num_ctx * 3 // 4

    def chat(self, messages: list, timeout: float) -> dict:
        body = {"model": self.model, "messages": messages, "tools": [FIND_TOOL, *TOOLS, *_FINAL], "stream": False, "think": False,
                "options": {"temperature": 0, "seed": 0, "num_ctx": self.num_ctx}}
        request = urllib.request.Request(self.url + "/api/chat", json.dumps(body).encode(),
                                         {"Content-Type": "application/json", "User-Agent": "uniselector"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)

    def __call__(self, question: dict, max_tokens: int, seconds: float) -> dict:
        """Return {"value", "reason"} or {"needs_planner": reason}, each with "calls" and "tokens"."""
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question_text(question)}]
        calls, tokens = [], 0

        def out(**result):
            return {**result, "calls": calls, "tokens": tokens}

        while tokens < max_tokens:
            reply = self.chat(messages, seconds)
            tokens += reply.get("prompt_eval_count", 0) + reply.get("eval_count", 0)
            message = reply["message"]
            messages.append(message)
            if not message.get("tool_calls"):
                messages.append({"role": "user", "content": "Use a tool. Call answer when the data shows the value. If it cannot, call needs_planner."})
                calls.append({"tool": None, "text": cut(message.get("content", ""), self.result_chars)})
                continue
            for call in message["tool_calls"]:
                name, arguments = call["function"]["name"], call["function"]["arguments"]
                if name == "needs_planner":
                    return out(needs_planner=str(arguments.get("reason", "")))
                if name == "answer":
                    bad = violations(question["schema"], arguments.get("value"))
                    result = {"error": f"the value does not match the schema: {bad[0]}"} if bad else {"accepted": True}
                else:
                    result = dispatch(self.client, name, arguments)
                calls.append({"tool": name, "arguments": arguments, "result": cut(result, self.result_chars)})
                if name == "answer" and not bad:
                    return out(value=arguments["value"], reason=str(arguments.get("reason", "")))
                messages.append({"role": "tool", "tool_name": name, "content": cut(result, self.result_chars)})
        return out(needs_planner="the sub-agent reached the token cap of the question and has no answer", capped=True)
