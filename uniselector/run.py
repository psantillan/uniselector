"""The loop of one run, and the harness that holds the runs.

The run is the second loop of an ultrastable system. The main agent builds objects and leaves.
DataHandles runs the objects: no model is in that loop. The decision model looks at what the
objects show, and it judges one thing: is each essential variable inside its limits? When one is
not, the run wakes the main agent with the reason and the objects. The main agent writes back a new
version of only what is broken, and the run goes on.

    loop:
        for each object: when its handle has a new version, do its operation again
        when an output changed, or nothing changed for a long time: look
            each variable inside its limits: do nothing
            a variable outside: wake the main agent, take its answer, go on

The harness enforces two things: the hard limits, and the action gate. Each other rule is
information in the state, and the decision model judges it.
"""
import json
import os
import secrets
import threading
import time
import traceback
from pathlib import Path

from datahandles import HandleError
from datahandles.sdk import Handle

from . import lineage
from .config import Config, clients as make_clients
from .decide import NO_ERROR, Decider, judge
from .gate import Gate
from .log import RunLog
from .plan import ANSWER_SCHEMA, check, limits, violations
from .state import Entry, State, cut

ENDED = ("done", "limit", "failed", "left")
NEXT = {
    "running": "The objects run, and the decision model watches the variables. Call plan_status with wait_seconds: the call returns when the "
               "run needs you or the time is over. Do not read the data yourself while you wait.",
    "woke": "An essential variable is outside its limits. `pending.outside` has the reason, and `objects` has what each object shows now. Find "
            "the cause with the DataHandles tools. Build a new handle for only the object that is broken, and write it back with plan_answer "
            "(question: the id in `pending`). Keep the name of the object. Leave each other object as it is. If a variable is not correct now, "
            "give a new list of variables. If the work of the plan is complete, answer with end: true. Then call plan_status with wait_seconds again.",
    "waiting_confirm": "The operation of an object is an action. Call plan_confirm with the run, the id of the request, and approve, deny, or human.",
    "waiting_human": "The human must decide. Tell the user to run the command in `pending.command`, or the same command with deny.",
    "done": "The run ended at your word. Read the handles of the objects with the DataHandles tools.",
    "limit": "A hard limit stopped the run. The user decides the limits. The handles of the objects stay usable.",
    "failed": "The harness failed. The reason is in `why`.",
    "left": "The process of the run ended before the run did. The handles of the objects stay usable. Call plan_submit to watch them again.",
}
# The time between two requests for the result of an operation that started in this tick. Most operations end in less than one tick.
POLL_SECONDS = 0.05


class _Stop(Exception):
    def __init__(self, status: str, why: str):
        self.status, self.why = status, why


class Run:
    def __init__(self, run_id: str, plan: dict, entries: list, cfg: Config, orchestrator, gate: Gate, decide, prepare):
        self.id, self.plan, self.cfg, self.gate, self.decide, self.prepare = run_id, plan, cfg, gate, decide, prepare
        self.orchestrator = orchestrator
        budget, wall = limits(cfg, plan)
        self.log = RunLog(Path(cfg.runs_dir) / run_id)
        self.state = State(list(plan["variables"]), plan.get("notes"), budget, wall)
        self.state.entries = {e.name: e for e in entries}
        self.status, self.why, self.pending, self.lineage = "running", "", None, None
        self.counts = {"looks": 0, "wakes": 0, "operations": 0, "confirmations": 0, "human_requests": 0}
        self.cost, self.last_wake, self.ending = 0.0, None, False
        self.handles = [e.handle for e in entries]   # each handle that was an object of the run, for the lineage
        self._lock = threading.Condition()
        self._reply, self._ended = None, False
        self.thread = threading.Thread(target=self._main, name=f"uniselector-{run_id}", daemon=True)

    # ---- the loop -----------------------------------------------------------------------------

    def _main(self) -> None:
        try:
            for entry in self.state.entries.values():
                self._event({"event": "object", **entry.summary(0.0)})
            while True:
                self._check()
                end = self.state.clock.elapsed() + self.cfg.wait_seconds
                self._settle(self._tick(), end)
                if self.ending:
                    if all(e.settled() for e in self.state.entries.values()):
                        raise _Stop("done", "the main agent ended the run")
                else:
                    reason = self._due()
                    if reason:
                        self._look(reason)
                time.sleep(max(end - self.state.clock.elapsed(), 0))
        except _Stop as stop:
            self._end(stop.status, stop.why)
        except Exception as e:   # a fault of the harness: the run ends, and the reason goes to the main agent
            self.log.write("events", {"event": "fault", "traceback": traceback.format_exc()})
            self._end("failed", f"{type(e).__name__}: {e}")

    def _check(self) -> None:
        if self.state.tokens >= self.state.budget_tokens:
            raise _Stop("limit", f"the token budget is spent: {self.state.tokens} of {self.state.budget_tokens}")
        if self.state.clock.elapsed() >= self.state.wall_seconds:
            raise _Stop("limit", f"the wall time is spent: {round(self.state.clock.elapsed(), 1)} s of {self.state.wall_seconds} s")

    def _bounded(self, fn):
        """Run a call that can block. Stop the run if the wall time ends first: a caller cannot cancel a synchronous operation (GAPS.md, G5)."""
        box = {}

        def work():
            try:
                box["value"] = fn()
            except BaseException as e:
                box["error"] = e

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(max(self.state.wall_seconds - self.state.clock.elapsed(), 0))
        if thread.is_alive():
            raise _Stop("limit", f"the wall time is spent: an operation did not return in {self.state.wall_seconds} s")
        if "error" in box:
            raise box["error"]
        return box["value"]

    def _tick(self) -> list:
        """Start the operation of each object whose handle has a new version. Return the objects that started. No model is here."""
        new = []
        for entry in list(self.state.entries.values()):
            if entry.job is not None:
                continue
            if entry.action is not None:   # an action runs one time for one approval, not at each change
                if not entry.settled():
                    self._action(entry)
                continue
            try:
                # The call waits while a job runs on the resource (GAPS.md, G11), so the wall time bounds it.
                version = self._bounded(entry.handle.observe).get("version")
            except HandleError as e:
                entry.version = None
                self._show(entry, error=e.to_dict())
                continue
            if entry.runs and version == entry.version:
                continue
            entry.version, entry.started = version, self.state.clock.elapsed()
            # Each operation runs as a job, so a slow one does not block the loop (docs/DECISIONS.md, D35).
            result = entry.handle.call(entry.operation["capability"], entry.operation.get("input", {}), background=True)
            if result["status"] == "running":
                entry.job = self.orchestrator.resolve(result["handles"][0]["id"])
                new.append(entry)
            else:
                self._take(entry, result)
        return new

    def _settle(self, new: list, end: float) -> None:
        """Take the result of each operation that ended. Wait a short time for the ones that started in this tick."""
        while True:
            for entry in self.state.entries.values():
                if entry.job is None:
                    continue
                try:
                    out = entry.job.wait(timeout=0)
                    result = {"status": "completed", **({"handles": [out.info]} if isinstance(out, Handle) else {"output": out})}
                except TimeoutError:
                    continue
                except HandleError as e:
                    result = {"status": "failed", "error": e.to_dict()}
                entry.job = None
                self._take(entry, result)
            if all(e.job is None for e in new) or self.state.clock.elapsed() >= end:
                return
            time.sleep(POLL_SECONDS)

    def _take(self, entry: Entry, result: dict) -> None:
        """The result of one run of an operation, as what the object shows now."""
        entry.runs += 1
        self.counts["operations"] += 1
        if result["status"] == "failed":
            self._show(entry, error=result["error"])
        elif result["status"] == "waiting":
            self._show(entry, error={"code": "NOT_APPROVED", "message": "the action has no approval, so it did not run"})
        elif "handles" in result:
            self._show(entry, output=cut({"handle": result["handles"][0]["id"], "type": result["handles"][0]["type"]}, self.cfg.value_chars))
        else:
            self._show(entry, output=cut(result.get("output"), self.cfg.value_chars))

    def _show(self, entry: Entry, output: str | None = None, error: dict | None = None) -> None:
        if error is not None:
            error = {"code": error["code"], "message": cut(error["message"], self.cfg.value_chars)}
        if (output, error) == (entry.output, entry.error):
            return
        entry.output, entry.error = output, error
        self.state.changed(entry)
        self._event({"event": "observation", **entry.summary(self.state.clock.elapsed())})

    # ---- the detector -------------------------------------------------------------------------

    def _due(self) -> str | None:
        """Is a look due? After a change of an output, or after a silent time that doubles at each look (docs/DECISIONS.md, D32)."""
        now, wait = self.state.clock.elapsed(), self.cfg.wait_seconds
        if any(e.job is not None and e.runs == 0 and now - e.started < wait for e in self.state.entries.values()):
            return None   # a new object gets one tick to show its first output
        if self.state.fresh:
            return "change"
        silent = now - self.state.changed_at
        due = self.state.quiet or 2 * max(self.state.gap, wait)
        if silent >= due:
            self.state.quiet = 2 * due
            return "silence"
        return None

    def _look(self, reason: str) -> None:
        """One pass of the decision model over the state: one probability for each essential variable."""
        self.state.fresh = False
        self.counts["looks"] += 1
        view, statements = self.state.view(), [NO_ERROR, *self.state.variables]
        t0 = time.perf_counter()
        d = self._bounded(lambda: self.decide(view, statements))
        self.state.tokens += d.tokens
        self.cost += d.cost
        outside = [{"variable": statements[i], "probability": round(d.probs[i], 3)} for i in judge(d, self.cfg.inside)]
        self.log.write("decisions", {"run": self.id, "look": self.counts["looks"], "reason": reason, "state": view, "statements": statements,
                                     "probs": d.probs, "outside": [o["variable"] for o in outside], "tokens": d.tokens, "cost": d.cost,
                                     "ms": round((time.perf_counter() - t0) * 1000)})
        if outside:
            self._wake(outside)

    def _wake(self, outside: list) -> None:
        """The step-function: the main agent replaces what is broken. The run takes the new versions and goes on."""
        self.counts["wakes"] += 1
        wake = {"id": f"wake_{self.counts['wakes']}", "kind": "wake", "outside": outside}
        reply = self._park("woke", wake)
        for name in reply["remove"]:
            del self.state.entries[name]
        for entry in reply["entries"]:
            self.state.entries[entry.name] = entry
            self.handles.append(entry.handle)
            self._event({"event": "object", **entry.summary(self.state.clock.elapsed())})
        if reply["variables"]:
            self.state.variables = reply["variables"]
        self.ending = reply["end"]
        self.state.fresh = True   # the answer changed the run: the next look does not wait for a new output
        self.last_wake = {**wake, "answer": reply["value"]}
        self._event({"event": "answer", "wake": wake["id"], "value": reply["value"]})

    def answer(self, question: str, value) -> None:
        """The main agent's answer to a wake. A fault in it goes back to the main agent, and the run stays awake."""
        with self._lock:
            if self.status != "woke" or self.pending["id"] != question:
                raise ValueError(f"run {self.id} has no pending wake {question}")
        check(value, ANSWER_SCHEMA, "answer")
        unknown = [n for n in value.get("remove", []) if n not in self.state.entries]
        if unknown:
            raise ValueError(f"the run has no object {unknown[0]!r}")
        entries = self.prepare(value.get("objects", []), question)
        with self._lock:
            self._reply = {"value": value, "entries": entries, "remove": value.get("remove", []),
                           "variables": value.get("variables"), "end": value.get("end", False)}
            self.status, self.pending = "running", None   # a status call right after this one must not show the old wake
            self._lock.notify_all()

    # ---- actions ------------------------------------------------------------------------------

    def _action(self, entry: Entry) -> None:
        """An action: the orchestrator cannot invoke it. It makes a request, and the gate runs the action after an approval."""
        self.counts["confirmations"] += 1
        name, input, info = entry.operation["capability"], entry.operation.get("input", {}), entry.handle.info
        target = {"id": entry.id, "type": info["type"], **{k: info["descriptor"][k] for k in ("name", "description") if info["descriptor"].get(k)}}
        try:   # the main agent decides on real information: what the target shows now
            target["now"] = cut(self._bounded(entry.handle.observe), self.cfg.value_chars)
        except HandleError as e:
            target["now"] = f"error {e.code}"
        request = {"id": f"req_{self.counts['confirmations']}", "kind": "confirmation", "object": entry.name, "capability": name,
                   "description": entry.action["description"], "input": input, "target": target,
                   "human_only": name in self.cfg.human_only, "with": "main agent"}
        request["graph"] = self.gate.open(f"uniselector-{self.id}-{request['id']}", entry.id, name, input)
        reply = self._park("waiting_confirm", request)
        self._event({"event": "confirmation", "request": request["id"], "capability": name, **reply})
        if reply["decision"] == "deny":
            self._show(entry, error={"code": "DENIED", "message": f"the {reply['by']} denied this action"})
            return
        if reply["by"] == "main agent" or self.gate.human_client is not None:
            self.gate.approve(request["graph"], by_human=reply["by"] == "human")
        self._take(entry, self._bounded(lambda: self.gate.run(request["graph"])))

    def _park(self, status: str, pending: dict):
        """Wait for the main agent or the human. The clock of the run stops."""
        with self._lock:
            self.state.clock.stop()
            self.status, self.pending, self._reply = status, pending, None
            self.log.write("events", {"event": status, "run": self.id, "pending": pending})
            while self._reply is None:
                self._lock.wait(0.5)
                decision = self.log.dir / f"human-{pending['id']}.json"   # written by `uniselector-confirm`
                if self.status == "waiting_human" and decision.exists():
                    self._reply = {"decision": json.loads(decision.read_text())["decision"], "by": "human"}
            reply, self._reply = self._reply, None
            self.status, self.pending = "running", None
            self.state.clock.start()
        return reply

    def confirm(self, request: str, decision: str) -> str:
        """The main agent's decision on a request. Return where the request is now."""
        with self._lock:
            if self.status not in ("waiting_confirm", "waiting_human") or self.pending["id"] != request:
                raise ValueError(f"run {self.id} has no pending request {request}")
            with_human = self.status == "waiting_human"
            if decision == "deny" or (decision == "approve" and not with_human and not self.pending["human_only"]):
                self._reply = {"decision": decision, "by": "main agent"}
                self.status, self.pending = "running", None
                self._lock.notify_all()
                return "running"
            if not with_human:   # `human`, or an approval of a capability that only the human can confirm
                self.counts["human_requests"] += 1
                self.status, self.pending["with"] = "waiting_human", "human"
                # With a runtime behind a URL, the human approves in DataHandles with a token of their own.
                url = f" --url {self.cfg.target}" if self.cfg.target.startswith(("http://", "https://")) else ""
                self.pending["command"] = f"uniselector-confirm {self.log.dir} {request} approve{url}"
                self.log.write("events", {"event": "waiting_human", "run": self.id, "pending": self.pending})
            return "waiting_human"

    # ---- the logs and the summary -------------------------------------------------------------

    def _event(self, record: dict) -> None:
        self.log.write("events", {"run": self.id, **record})

    def _end(self, status: str, why: str) -> None:
        with self._lock:
            if self._ended:   # the first end stands
                return
            self._ended = True
        self.state.clock.stop()
        for entry in self.state.entries.values():   # the run does not leave work that nothing waits for
            if entry.job is not None:
                try:
                    entry.job.invoke("job.cancel")
                except HandleError:
                    pass
        try:
            self.lineage = str(self.log.save("lineage.json", lineage.export(self.id, self.handles)))
        except Exception as e:
            self.log.write("events", {"event": "fault", "traceback": f"lineage: {type(e).__name__}: {e}"})
        self.status, self.why = status, why
        outcome = {"run": self.id, "outcome": status, "why": why, "tokens": self.state.tokens, "cost": round(self.cost, 6),
                   "seconds": round(self.state.clock.elapsed(), 1), **self.counts}
        self.log.write("decisions", outcome)
        self.log.write("events", {"event": "end", **outcome})
        self.log.save("summary.json", self.summary())
        self.log.close()

    def summary(self, brief: bool = False) -> dict:
        """A bounded summary. The full form is enough for an agent that starts from a fresh context."""
        with self._lock:
            status, pending = self.status, self.pending
        out = {"run": self.id, "state": status, "why": why_text(status, self.why, pending)}
        if not brief:
            out |= {"plan": {"variables": self.state.variables, **({"notes": self.state.notes} if self.state.notes else {})},
                    "objects": self.state.objects(), "pending": pending, "last_wake": self.last_wake,
                    "lineage": self.lineage, "logs": str(self.log.dir)}
        return {**out, "limits": self.state.limits(), "counts": self.counts, "next": NEXT[status]}


def why_text(status: str, why: str, pending: dict | None) -> str:
    if status in ENDED:
        return why
    if status == "woke":
        return "outside its limits: " + "; ".join(f"{o['variable']} (probability that it is true: {o['probability']})" for o in pending["outside"])
    if status in ("waiting_confirm", "waiting_human"):
        return f"the action {pending['capability']} of the object {pending['object']} needs a confirmation from the {pending['with']}"
    return "the objects run, and no look found a variable outside its limits"


class Harness:
    """The runs of one process, and the four operations of the agent surface."""

    def __init__(self, cfg: Config, clients: dict | None = None, decide=None):
        self.cfg = cfg
        self.clients = clients or make_clients(cfg)
        self.gate = Gate(self.clients["planner"], cfg.orchestrator, cfg.human, self.clients.get("human"))
        self.decide = decide or Decider(cfg.decision_url, cfg.decision_model, os.environ[cfg.decision_key_env])
        self.runs = {}

    def prepare(self, objects: list, written: str) -> list:
        """Take objects into a run. The main agent can hand off only a handle that it holds, and only an operation that the handle has."""
        entries = []
        for o in objects:
            self.clients["planner"].resolve(o["handle"])
            actions = {c["name"]: c for c in self.gate.delegate(o["handle"])}
            handle = self.clients["orchestrator"].resolve(o["handle"])
            capabilities = {c["name"]: c for c in handle.describe()["capabilities"]} | actions
            operation, name = o["operation"], o["operation"]["capability"]
            if name not in capabilities:
                raise ValueError(f"the handle of the object {o['name']!r} has no capability {name!r}. It has: {', '.join(sorted(capabilities))}")
            bad = violations(capabilities[name]["input_schema"], operation.get("input", {}))
            if bad:
                raise ValueError(f"the input of the operation of the object {o['name']!r} does not match the schema of {name}: {bad[0]}")
            if capabilities[name]["execution"]["mode"] == "stream":
                raise ValueError(f"{name} is a stream. The operation of an object gives one value: read the newest value or a recorded window")
            entries.append(Entry(o["name"], handle, operation, written, actions.get(name)))
        return entries

    def submit(self, plan: dict) -> dict:
        check(plan)
        entries = self.prepare(plan["objects"], "plan")
        run_id = f"run_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"
        run = self.runs[run_id] = Run(run_id, plan, entries, self.cfg, self.clients["orchestrator"], self.gate, self.decide, self.prepare)
        run.log.save("plan.json", plan)
        run.thread.start()
        return {"run": run_id, "state": "running", "next": NEXT["running"]}

    def status(self, run: str, wait_seconds: float = 0) -> dict:
        """The summary of a run. With `wait_seconds`, wait first until the run needs the main agent or the time is over."""
        if run in self.runs:
            end = time.time() + wait_seconds
            while self.runs[run].status == "running" and time.time() < end:
                time.sleep(0.1)
            # A wait that ends with nothing to do gives the short form: the main agent sleeps, and each call adds to its context.
            return self.runs[run].summary(brief=wait_seconds > 0 and self.runs[run].status == "running")
        saved = Path(self.cfg.runs_dir) / run / "summary.json"   # a run of a process that ended
        if not saved.exists():
            raise ValueError(f"no run {run}")
        return json.loads(saved.read_text())

    def answer(self, run: str, question: str, value) -> dict:
        self._run(run).answer(question, value)
        return {"run": run, "accepted": question, "next": NEXT["running"]}

    def confirm(self, run: str, request: str, decision: str) -> dict:
        if decision not in ("approve", "deny", "human"):
            raise ValueError("the decision is approve, deny, or human")
        now = self._run(run).confirm(request, decision)
        out = {"run": run, "request": request, "decision": decision, "state": now,
               "next": NEXT[now]}
        if decision == "approve" and now == "waiting_human":
            out["note"] = "Only the human can approve this request. It is with the human now."
        return out

    def close(self) -> None:
        """The process ends. A run is a thread of this process, so each run that works ends here, and its logs say so."""
        for run in self.runs.values():
            if run.status not in ENDED:
                run._end("left", "the MCP server of the main agent ended before the run did")

    def _run(self, run: str) -> Run:
        if run not in self.runs:
            raise ValueError(f"no live run {run}")
        return self.runs[run]
