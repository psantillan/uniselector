"""The one primitive: decide(state, options) -> a distribution over the options.

One call is one pass of a model over the state and a set of options. The set is different at each
call. `OllamaDecider` is the first implementation: a local model reads the state, the options get
one letter each, and the probabilities of the first output token give the distribution. A trained
decision model can replace it: it must be a callable with the same arguments and the same result.
"""
import json
import math
import urllib.request
from dataclasses import dataclass

# One letter for each option. 20 is the largest `top_logprobs` that the Ollama server accepts, so
# with 20 options or fewer the call can see the probability of each option (docs/DECISIONS.md, D4).
LABELS = "ABCDEFGHIJKLMNOPQRST"

SYSTEM = (
    "You are the regulator of a long task. A main agent gave you a plan: a list of intents. You carry out the current "
    "intent with operations on handles. A handle is a reference to a resource. The data stays at the resource: an "
    "operation gives a new handle or a small value. You get the state of the run, one decision, and the options. "
    "Choose the option that carries out the current intent. Do not repeat an operation that has a result already. An error in the results means that the input was wrong: do not choose the same input again. "
    "A wait result shows each job and stream after the wait: compare its values with the intent. "
    "When the results show that the current intent is met, choose step_done. Reply with the letter of one option and nothing else.")


@dataclass
class Distribution:
    probs: list     # one probability for each option. The sum is 1, or 0 when no label got probability.
    mass: float     # the share of the probability that was on a label before the normalisation
    tokens: int     # the prompt and output tokens of the call


def judge(d: Distribution, top: float, ratio: float, mass: float) -> int | None:
    """Return the index of the chosen option, or None: the decision abstains."""
    order = sorted(range(len(d.probs)), key=lambda i: -d.probs[i])
    first = d.probs[order[0]]
    second = d.probs[order[1]] if len(order) > 1 else 0.0
    if d.mass < mass or first < top or second > ratio * first:
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


def render(state: dict, options: list) -> str:
    """The state and the options as the text that the model reads. The decision comes last: the text
    before it stays the same through one walk, so the server can reuse its work on that part."""
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
    lines += ["", "DECISION", state["decision"]["text"], "", "OPTIONS"]
    lines += [f"{LABELS[i]}. {text}" for i, text in enumerate(options)]
    lines += ["", "Reply with one letter."]
    return "\n".join(lines)


class OllamaDecider:
    def __init__(self, url: str, model: str, num_ctx: int):
        self.url, self.model, self.num_ctx = url.rstrip("/"), model, num_ctx

    def __call__(self, state: dict, options: list) -> Distribution:
        assert 1 <= len(options) <= len(LABELS), f"a decision takes 1 to {len(LABELS)} options, not {len(options)}"
        body = {"model": self.model, "system": SYSTEM, "prompt": render(state, options), "stream": False, "think": False,
                "logprobs": True, "top_logprobs": len(LABELS),
                "options": {"temperature": 0, "seed": 0, "num_predict": 1, "num_ctx": self.num_ctx}}
        request = urllib.request.Request(self.url + "/api/generate", json.dumps(body).encode(),
                                         {"Content-Type": "application/json", "User-Agent": "uniselector"})
        left = state["limits"]["wall_seconds"] - state["limits"]["elapsed_s"]   # a call cannot take longer than the run has
        with urllib.request.urlopen(request, timeout=max(left, 1)) as response:
            reply = json.load(response)
        raw = [0.0] * len(options)
        for top in (reply.get("logprobs") or [{}])[0].get("top_logprobs", []):
            token = top["token"].strip()   # "B" and " B" are the same answer
            if len(token) == 1 and token in LABELS[:len(options)]:
                raw[LABELS.index(token)] += math.exp(top["logprob"])
        mass = sum(raw)
        return Distribution([p / mass for p in raw] if mass else raw, mass,
                            reply.get("prompt_eval_count", 0) + reply.get("eval_count", 0))
