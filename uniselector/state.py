"""The state of one run: the objects, what each one shows now, and the spend against the hard limits.

`view` gives the bounded form that the decision model reads at one look.
"""
import json
import time
from dataclasses import dataclass, field


def cut(value, chars: int) -> str:
    """A value as JSON text of a bounded size. A long value keeps its first and its last part (docs/DECISIONS.md, D38)."""
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) <= chars:
        return text
    half = chars // 2
    return f"{text[:half]} ... (cut: {len(text) - 2 * half} of {len(text)} characters) ... {text[-half:]}"


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
    """One object of the run: a handle that the main agent built, and the operation that reads it."""
    name: str
    handle: object            # the SDK handle of the orchestrator's principal
    operation: dict           # {"capability": ..., "input": {...}}
    written: str              # what wrote this version of the object: "plan", or the id of a wake
    action: dict | None       # the descriptor of the capability, when the operation is an action
    version: str | None = None   # the version of the handle when the operation last started
    job: object = None           # the job handle, while the operation runs
    started: float = 0.0
    runs: int = 0
    output: str | None = None
    error: dict | None = None
    changed_at: float = 0.0

    @property
    def id(self) -> str:
        return self.handle.id

    def settled(self) -> bool:
        """The operation ran one time or more, and it does not run now."""
        return self.job is None and (self.runs > 0 or self.error is not None)

    def summary(self, now: float) -> dict:
        out = {"name": self.name, "handle": self.id, "type": self.handle.info["type"], "operation": self.operation, "written": self.written}
        if self.error is not None:
            out["error"] = self.error
        elif self.output is not None:
            out["output"] = self.output
        if self.job is not None:
            out["running_s"] = round(now - self.started, 1)
        return {**out, "runs": self.runs, "changed_s": round(now - self.changed_at, 1)}


@dataclass
class State:
    variables: list
    notes: str | None
    budget_tokens: int
    wall_seconds: float
    tokens: int = 0
    entries: dict = field(default_factory=dict)    # the current object of each name
    clock: Clock = field(default_factory=Clock)
    changed_at: float = 0.0    # when an output last changed
    gap: float = 0.0           # the longest time between two changes so far
    fresh: bool = False        # an output changed after the last look
    quiet: float = 0.0         # the silent time at which the next look with no change is due

    def changed(self, entry: Entry) -> None:
        now = self.clock.elapsed()
        self.gap, self.changed_at, entry.changed_at = max(self.gap, now - self.changed_at), now, now
        self.fresh, self.quiet = True, 0.0

    def limits(self) -> dict:
        return {"elapsed_s": round(self.clock.elapsed(), 1), "wall_seconds": self.wall_seconds, "tokens": self.tokens,
                "budget_tokens": self.budget_tokens}

    def objects(self) -> list:
        now = self.clock.elapsed()
        return [e.summary(now) for e in self.entries.values()]

    def view(self) -> dict:
        return {**({"notes": self.notes} if self.notes else {}), "objects": self.objects(), "limits": self.limits()}
