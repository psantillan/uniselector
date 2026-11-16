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
class Form:
    """One form of the output of an object, for example a summary or an embedding, and the runners that can make it."""
    runners: list               # who makes the form, in order. After the last one: the main agent gets a wake
    variables: list = field(default_factory=list)   # the check of the form: sentences about its value
    bound: int = 0              # the place of the current runner in `runners`
    value: str | None = None    # the form: a bounded value, or the id of a handle that holds it
    of: int | None = None       # the change of the output that the value is a form of
    tried: list = field(default_factory=list)   # what each runner gave that was not a good form, for the next runner

    @property
    def runner(self) -> str:
        return self.runners[self.bound] if self.bound < len(self.runners) else "main"


@dataclass
class Entry:
    """One object of the run: a handle that the main agent built, and the operation that reads it."""
    name: str
    handle: object            # the SDK handle of the orchestrator's principal
    operation: dict           # {"capability": ..., "input": {...}}
    written: str              # what wrote this version of the object: "plan", or the id of a wake
    action: dict | None       # the descriptor of the capability, when the operation is an action
    variables: list = field(default_factory=list)   # the check of this object: sentences about its output
    runners: list = field(default_factory=list)     # who answers for this object, in order. After the last one: the main agent
    holder: str = "main"         # the runner whose principal holds the action, and so runs it
    bound: int = 0               # the place of the current runner in `runners`
    trial: bool = False          # a runner changed the object, and no look judged the change yet
    repair: str | None = None    # the id of the wake that a model runner of the object works on now
    changes: int = 0             # the changes of the output after the first output
    first_at: float | None = None   # when this version showed its first output
    version: str | None = None   # the version of the handle when the operation last started
    job: object = None           # the job handle, while the operation runs
    started: float = 0.0
    runs: int = 0
    output: str | None = None
    error: dict | None = None
    changed_at: float = 0.0
    forms: dict = field(default_factory=dict)   # for the name of each form of the output: its `Form`

    @property
    def id(self) -> str:
        return self.handle.id

    @property
    def runner(self) -> str:
        """The current runner: the agent that repairs the object at a wake, or that confirms its action."""
        return self.runners[self.bound] if self.bound < len(self.runners) else "main"

    def settled(self) -> bool:
        """The operation ran one time or more, and it does not run now."""
        return self.job is None and (self.runs > 0 or self.error is not None)

    def summary(self, now: float) -> dict:
        out = {"name": self.name, "handle": self.id, "type": self.handle.info["type"], "operation": self.operation, "written": self.written}
        if self.variables:
            out["variables"] = self.variables
        if self.runners:
            out["runner"] = self.runner
        if self.repair is not None:
            out["in_repair"] = self.repair
        if self.error is not None:
            out["error"] = self.error
        elif self.output is not None:
            out["output"] = self.output
        if self.forms:   # a form can be behind the output: its runner works beside the loop
            out["forms"] = {name: None if form.value is None else {"value": form.value, "changes_behind": self.changes - form.of}
                            for name, form in self.forms.items()}
        if self.job is not None:
            out["running_s"] = round(now - self.started, 1)
        if self.runs and self.action is None:   # a quiet output with a new run shows that the source is live (docs/DECISIONS.md, D46)
            out["ran_s"] = round(now - self.started, 1)
        return {**out, "runs": self.runs, "changes": self.changes, "changed_s": round(now - self.changed_at, 1)}


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
        if entry.first_at is None:
            entry.first_at = now
        else:
            entry.changes += 1

    def limits(self) -> dict:
        return {"elapsed_s": round(self.clock.elapsed(), 1), "wall_seconds": self.wall_seconds, "tokens": self.tokens,
                "budget_tokens": self.budget_tokens}

    def objects(self) -> list:
        now = self.clock.elapsed()
        return [e.summary(now) for e in self.entries.values()]

    def view(self) -> dict:
        return {**({"notes": self.notes} if self.notes else {}), "objects": self.objects(), "limits": self.limits()}
