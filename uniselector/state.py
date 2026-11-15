"""The state of one run: the decision model's model of the run.

The state holds the plan, the current intent, brief summaries of the current handles, the last few
results, the errors, the time and the spend against the hard limits, and the stall signal.
`view` gives the bounded form that one decision reads.
"""
import json
import time
from dataclasses import dataclass, field


def cut(value, chars: int) -> str:
    """A value as JSON text of a bounded size."""
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= chars else f"{text[:chars]}... (cut: {len(text)} characters)"


class Clock:
    """The time that the run works. The clock stops while the run waits for the main agent or the human."""

    def __init__(self):
        self._sum, self._since = 0.0, time.monotonic()

    def stop(self) -> None:
        if self._since is not None:
            self._sum, self._since = self._sum + time.monotonic() - self._since, None

    def start(self) -> None:
        if self._since is None:
            self._since = time.monotonic()

    def elapsed(self) -> float:
        return self._sum + (time.monotonic() - self._since if self._since is not None else 0.0)


@dataclass
class Entry:
    """One handle of the run."""
    alias: str          # the short name that the decision model reads: h1, h2, ...
    handle: object      # the SDK handle of the orchestrator's principal
    origin: str         # "start", or the operation that made it
    capabilities: list  # the descriptors of the capabilities that the orchestrator can invoke
    actions: list       # the descriptors of the actions: the orchestrator can only request them
    status: str | None = None
    version: str | None = None

    @property
    def id(self) -> str:
        return self.handle.id

    def summary(self, chars: int) -> dict:
        info = self.handle.info
        out = {"alias": self.alias, "id": self.id, "type": info["type"], "origin": self.origin}
        for key in ("name", "description"):
            if info["descriptor"].get(key):
                out[key] = info["descriptor"][key]
        if info["descriptor"].get("schema") is not None:
            schema = info["descriptor"]["schema"]
            out["schema"] = schema if len(json.dumps(schema)) <= chars else cut(schema, chars)
        if self.status:
            out["status"] = self.status
        return out


@dataclass
class State:
    plan: dict
    budget_tokens: int
    wall_seconds: float
    results_kept: int
    handles_kept: int
    value_chars: int
    intent: int = 0
    step: int = 0
    tokens: int = 0
    entries: list = field(default_factory=list)
    start: int = 0                                 # the number of start handles
    results: list = field(default_factory=list)    # each result of the run, in the bounded form
    outputs: list = field(default_factory=list)    # the output values of the last few results, for the candidate pool
    clock: Clock = field(default_factory=Clock)
    looks: int = 0                                 # the looks since the last change
    seen: set = field(default_factory=set)

    def __post_init__(self):
        self.changed_at = self.clock.elapsed()

    def add(self, entry: Entry) -> Entry:
        self.entries.append(entry)
        return entry

    def entry(self, alias_or_id: str) -> Entry | None:
        return next((e for e in self.entries if alias_or_id in (e.alias, e.id)), None)

    def shown(self) -> list:
        """The handles that a decision reads: the start handles and the newest handles."""
        made = self.entries[self.start:]
        room = max(self.handles_kept - self.start, 1)
        return self.entries[:self.start] + made[-room:]

    def record(self, result: dict, output=None) -> dict:
        """Add one result. A result that the run did not see before is a change: it resets the stall signal."""
        result = {"step": self.step, "intent": self.intent, **result}
        key = json.dumps({k: result.get(k) for k in ("capability", "target", "input", "output", "handle", "error")}, sort_keys=True, default=str)
        if "control" not in result and "error" not in result and key not in self.seen:
            self.changed()
        self.seen.add(key)
        self.results.append(result)
        if output is not None:
            self.outputs = [*self.outputs, output][-self.results_kept:]
        return result

    def changed(self) -> None:
        self.changed_at, self.looks = self.clock.elapsed(), 0

    def limits(self) -> dict:
        n = len(self.plan["intents"])
        return {"elapsed_s": round(self.clock.elapsed(), 1), "wall_seconds": self.wall_seconds, "tokens": self.tokens,
                "budget_tokens": self.budget_tokens, "intents_done": self.intent, "intents": n,
                "spent_share": round(self.tokens / self.budget_tokens, 3)}

    def view(self, decision: str) -> dict:
        return {"plan": {k: self.plan[k] for k in ("intents", "notes") if k in self.plan}, "intent": self.intent,
                "handles": [e.summary(self.value_chars) for e in self.shown()],
                "results": self.results[-self.results_kept:], "limits": self.limits(),
                "stall": {"seconds": round(self.clock.elapsed() - self.changed_at, 1), "looks": self.looks},
                "decision": {"text": decision}}
