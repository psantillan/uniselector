"""The one primitive: decide(state, options) -> a distribution over the options.

One call is one pass of a decision model over the state and a set of options. The set is different
at each call. A decision model reads a state and typed questions, and it gives probabilities. It
writes no text. `Decider` sends one decision as one `choice` question to a Decisions API. Another
decision model can replace it: it must be a callable with the same arguments and the same result.
"""
import json
import urllib.request
from dataclasses import dataclass

# The largest number of options of one `choice` question in the Decisions API (docs/DECISIONS.md, D4).
MAX_OPTIONS = 255

RULES = (
    "You are the regulator of a long task. A main agent gave you a plan: a list of intents. You carry out the current "
    "intent with operations on handles. A handle is a reference to a resource. The data stays at the resource: an "
    "operation gives a new handle or a small value. The state shows the run. Choose the option that carries out the "
    "current intent. Do not repeat an operation that has a result already. An error in the results means that the input was wrong: do not choose the same input again. "
    "A wait result shows each job and stream after the wait: compare its values with the intent. "
    "When the results show that the current intent is met, choose step_done.")


@dataclass
class Distribution:
    probs: list     # one probability for each option. The sum is 1, or 0 when the model refused the question.
    tokens: int     # the input tokens of the call. A decision model has no output tokens.
    cost: float     # the price of the call in USD, as the API states it


def judge(d: Distribution, top: float, ratio: float) -> int | None:
    """Return the index of the chosen option, or None: the decision abstains."""
    order = sorted(range(len(d.probs)), key=lambda i: -d.probs[i])
    first = d.probs[order[0]]
    second = d.probs[order[1]] if len(order) > 1 else 0.0
    if first < top or second > ratio * first:
        return None
    return order[0]


def _handle_line(h: dict) -> str:
    line = f"- {h['alias']}: {h['type']} {h.get('name') or ''}".rstrip() + f" ({h['origin']})"
    if h.get("description"):
        line += f". {h['description']}"
    if h.get("schema") is not None:
        line += f" Schema: {json.dumps(h['schema'])}"
    if h.get("status"):
        line += f" Status now: {h['status']}"
    return line


def _result_line(r: dict) -> str:
    what = r.get("capability", r.get("control", ""))
    line = f"- step {r['step']}: {what}"
    if "target" in r:
        line += f" on {r['target']} with input {json.dumps(r.get('input', {}))}"
    if "error" in r:
        e = r["error"]
        return line + f" -> ERROR {e['code']} (retryable: {str(e.get('retryable', False)).lower()}): {e['message']}"
    if "handle" in r:
        return line + f" -> new handle {r['handle']}"
    if "output" in r:
        return line + f" -> {r['output']}"
    return line + f" -> {r.get('note', 'done')}"


def render(state: dict) -> str:
    """The state as the text that the decision model reads. The decision and its options are not in it: they are the question."""
    plan, at = state["plan"], state["intent"]
    lines = ["PLAN"]
    for i, text in enumerate(plan["intents"]):
        lines.append(f"{i + 1}. [{'done' if i < at else 'CURRENT' if i == at else 'later'}] {text}")
    if plan.get("notes"):
        lines.append(f"Notes: {plan['notes']}")
    lines += ["", "HANDLES", *map(_handle_line, state["handles"])]
    # The results of the current intent are apart: they are the evidence for `step_done`.
    earlier = [r for r in state["results"] if r["intent"] != at]
    current = [r for r in state["results"] if r["intent"] == at]
    if earlier:
        lines += ["", "RESULTS OF EARLIER INTENTS (oldest first)", *map(_result_line, earlier)]
    lines += ["", f"RESULTS OF THE CURRENT INTENT ({at + 1}. {plan['intents'][at]})",
              *(map(_result_line, current) if current else ["- none yet: no operation ran for this intent"])]
    lim, stall = state["limits"], state["stall"]
    lines += ["", "LIMITS", f"time: {lim['elapsed_s']} s of {lim['wall_seconds']} s. tokens: {lim['tokens']} of {lim['budget_tokens']}. "
              f"intents done: {lim['intents_done']} of {lim['intents']}. share of the budget spent: {lim['spent_share']}."]
    lines += ["", "STALL", f"nothing changed for {stall['seconds']} s, in {stall['looks']} looks."]
    return "\n".join(lines)


class Decider:
    def __init__(self, url: str, model: str, key: str):
        self.url, self.model, self.key = url, model, key

    def __call__(self, state: dict, options: list) -> Distribution:
        assert 1 <= len(options) <= MAX_OPTIONS, f"a decision takes 1 to {MAX_OPTIONS} options, not {len(options)}"
        names = [str(i + 1) for i in range(len(options))]   # the name of an option is its number, and the description is its text
        body = {"model": self.model, "state": render(state), "questions": {"decision": {
            "type": "choice", "instructions": f"{RULES}\n\n{state['decision']['text']}", "criteria": dict(zip(names, options))}}}
        request = urllib.request.Request(self.url, json.dumps(body).encode(), {
            "Content-Type": "application/json", "Authorization": f"Bearer {self.key}", "User-Agent": "uniselector"})
        left = state["limits"]["wall_seconds"] - state["limits"]["elapsed_s"]   # a call cannot take longer than the run has
        with urllib.request.urlopen(request, timeout=max(left, 1)) as response:
            reply = json.load(response)
        # A refusal has no probabilities. Each option then gets 0, and the decision abstains.
        probs = reply["answers"]["decision"].get("probabilities", {})
        return Distribution([probs.get(name, 0.0) for name in names], reply["usage"]["input_tokens"], reply["usage"]["cost"])
