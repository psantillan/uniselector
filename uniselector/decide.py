"""The one primitive: decide(state, statements) -> one probability for each statement.

A decision model reads a state and typed questions, and it gives probabilities. It writes no text.
Here it is the detector of the outer loop: it judges if each essential variable is inside its
limits. `Decider` sends one look as one call to a Decisions API, with one `noul` question for each
statement. Another decision model can replace it: it must be a callable with the same arguments
and the same result.
"""
import json
import urllib.request
from dataclasses import dataclass

RULES = (
    "The state shows objects that run with no supervision. Each object is a handle to data and one operation that reads a small value "
    "from it. The state shows the newest output of each operation, or its error. Judge the statement from the state only. "
    "Is the statement true now?")
# The essential variable of each run. The harness does not judge an error: it can be a fault of one read or a break of the structure.
NO_ERROR = "No object shows an error."


@dataclass
class Distribution:
    probs: list     # for each statement, the probability that it is true. 0 when the model refused the question.
    tokens: int     # the input tokens of the call. A decision model has no output tokens.
    cost: float     # the price of the call in USD, as the API states it


def judge(d: Distribution, inside: float) -> list[int]:
    """Return the indexes of the statements that are outside their limits: the model does not hold them to be true."""
    return [i for i, p in enumerate(d.probs) if p < inside]


def _object_lines(o: dict) -> list[str]:
    lines = [f"- {o['name']} ({o['type']}): {o['operation']['capability']} {json.dumps(o['operation'].get('input', {}))}"]
    if "error" in o:
        lines.append(f"  ERROR {o['error']['code']}: {o['error']['message']}")
    elif "output" in o:
        lines.append(f"  output: {o['output']}")
    else:
        lines.append("  no output yet")
    if "running_s" in o:
        lines.append(f"  the operation runs now, for {o['running_s']} s")
    lines.append(f"  runs: {o['runs']}. last change of the output: {o['changed_s']} s ago.")
    return lines


def render(state: dict) -> str:
    """The state as the text that the decision model reads. The statements are not in it: they are the questions."""
    lines = [f"NOTES\n{state['notes']}\n"] if state.get("notes") else []
    lines.append("OBJECTS")
    for o in state["objects"]:
        lines += _object_lines(o)
    lim = state["limits"]
    lines += ["", "LIMITS", f"time: {lim['elapsed_s']} s of {lim['wall_seconds']} s. tokens: {lim['tokens']} of {lim['budget_tokens']}."]
    return "\n".join(lines)


class Decider:
    def __init__(self, url: str, model: str, key: str):
        self.url, self.model, self.key = url, model, key

    def __call__(self, state: dict, statements: list) -> Distribution:
        names = [f"v{i + 1}" for i in range(len(statements))]
        body = {"model": self.model, "state": render(state), "questions": {
            name: {"type": "noul", "instructions": f"{RULES}\n\nStatement: {text}"} for name, text in zip(names, statements)}}
        request = urllib.request.Request(self.url, json.dumps(body).encode(), {
            "Content-Type": "application/json", "Authorization": f"Bearer {self.key}", "User-Agent": "uniselector"})
        left = state["limits"]["wall_seconds"] - state["limits"]["elapsed_s"]   # a call cannot take longer than the run has
        with urllib.request.urlopen(request, timeout=max(left, 1)) as response:
            reply = json.load(response)
        # A refusal has no probability. The statement then gets 0: the model does not hold it to be true.
        return Distribution([reply["answers"][name].get("noul", 0.0) for name in names], reply["usage"]["input_tokens"], reply["usage"]["cost"])
