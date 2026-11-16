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
            a variable outside: wake the runner of the object
        when a runner answered: take its new version of the object

A model runner works beside the loop: each other object runs, and the looks go on. The run waits
only for the main agent and the human, and its clock stops then.

An object carries its runners: who answers for it, in order. The first one is bound. When the
repair of the bound runner does not hold at the next look, the same wake path binds the next one.
After the last one, the runner is the main agent. An object with no runners is bound to the main
agent. The confirmation of an action is the same binding: the main agent, then the human.

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
from .decide import NO_ERROR, Busy, Decider, judge
from .gate import Gate
from .log import RunLog
from .plan import ANSWER_SCHEMA, check, limits, violations
from .runner import ModelRunner, Outcome
from .state import Entry, State, cut

ENDED = ("done", "limit", "failed", "left")
NEXT = {
    "running": "The objects run, and the decision model watches the variables. Call plan_status with wait_seconds: the call returns when the "
               "run needs you or the time is over. Do not read the data yourself while you wait.",
    "woke": "An essential variable is outside its limits. `pending.outside` has the reason, and `objects` has what each object shows now. Find "
            "the cause with the DataHandles tools. Build a new handle for only the object that is broken, and write it back with plan_answer "
            "(question: the id in `pending`). Keep the name of the object. Leave each other object as it is. If a variable is not correct now, "
            "give a new list of variables. An object shows when its operation last ran (`ran_s`), so a "
            "variable can state that a quiet object still gets new data. `pending.tried` has each runner of the object that gave no "
            "repair. An object with `in_repair` is with its runner now: leave it. If only that repair can make the variable true, answer with objects: [] and the wake waits for the runner. If the work of the plan is complete, answer with end: true. Then call plan_status with wait_seconds again.",
    "waiting_confirm": "The operation of an object is an action. Call plan_confirm with the run, the id of the request, and approve, deny, or human.",
    "waiting_human": "The human must decide. Tell the user to run the command in `pending.command`, or the same command with deny. Then "
                     "call plan_status with wait_seconds again.",
    "done": "The run ended at your word. Read the handles of the objects with the DataHandles tools.",
    "limit": "A hard limit stopped the run. The user decides the limits. The handles of the objects stay usable.",
    "failed": "The harness failed. The reason is in `why`.",
    "left": "The process of the run ended before the run did. The handles of the objects stay usable. Call plan_submit to watch them again.",
}
WITH = {"main": "main agent", "human": "human"}
# The main agent's principal cannot see a capability that it does not hold (docs/GAPS.md, G16).
HELD_BY_HUMAN = "Only the human's principal holds this capability. The main agent's principal cannot read its description."
# The time between two requests for the result of an operation that started in this tick. Most operations end in less than one tick.
POLL_SECONDS = 0.05


class _Stop(Exception):
    def __init__(self, status: str, why: str):
        self.status, self.why = status, why


class Run:
    def __init__(self, run_id: str, plan: dict, entries: list, cfg: Config, orchestrator, gate: Gate, decide, prepare, runners: dict):
        self.id, self.plan, self.cfg, self.gate, self.decide, self.prepare = run_id, plan, cfg, gate, decide, prepare
        self.orchestrator, self.runners = orchestrator, runners
        budget, wall = limits(cfg, plan)
        self.log = RunLog(Path(cfg.runs_dir) / run_id)
        self.state = State(list(plan["variables"]), plan.get("notes"), budget, wall)
        self.state.entries = {e.name: e for e in entries}
        self.status, self.why, self.pending, self.lineage = "running", "", None, None
        self.counts = {"looks": 0, "wakes": 0, "runner_wakes": 0, "rebinds": 0, "operations": 0, "confirmations": 0, "human_requests": 0}
        self.cost, self.last_wake, self.ending = 0.0, None, False
        self.told = set()   # the statements of the wake that the main agent answered while a runner worked (docs/DECISIONS.md, D52)
        self.handles = [e.handle for e in entries]   # each handle that was an object of the run, for the lineage
        self.repairs = {}   # for the name of each object that a model runner works on now: the wake, the thread, and what the runner gave
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
                self._collect()
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

    def _spend(self, tokens: int, cost: float = 0.0) -> int:
        """Add a spend to the run. Return the tokens that the run has left: 0 after its end. A runner reports each request here, so
        the budget holds while a runner works beside the loop."""
        with self._lock:
            self.state.tokens += tokens
            self.cost += cost
            return 0 if self._ended else max(self.state.budget_tokens - self.state.tokens, 0)

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
        view = self.state.view()
        asked = [(NO_ERROR, None)] + [(v, None) for v in self.state.variables]   # a statement, and the object that it is about
        # The check of an object waits for the first output of its version: a trial must last long enough to show its result. It
        # waits also while the runner of the object works.
        asked += [(f"About the object `{e.name}`: {v}", e) for e in self.state.entries.values()
                  if e.first_at is not None and e.repair is None for v in e.variables]
        statements = [text for text, _ in asked]
        t0 = time.perf_counter()
        try:
            d = self._bounded(lambda: self.decide(view, statements))
        except Busy as e:   # no look was made. The look stays due, and the next tick asks again. The objects go on.
            if reason == "silence":
                self.state.quiet /= 2
            self._event({"event": "no_look", "why": str(e)})
            return
        self.state.fresh = False
        self.counts["looks"] += 1
        self._spend(d.tokens, d.cost)
        outside, owners, news = [], [], []
        for i in judge(d, self.cfg.inside):
            text, entry = asked[i]
            own = [entry.name] if entry is not None else self._owners(text)
            owners += own
            outside.append({"variable": statements[i], "probability": round(d.probs[i], 3)})
            if not own and text not in self.told:   # a statement with no object is the main agent's, and the main agent does not know it
                news.append(text)
        self.log.write("decisions", {"run": self.id, "look": self.counts["looks"], "reason": reason, "state": view, "statements": statements,
                                     "probs": d.probs, "outside": [o["variable"] for o in outside], "tokens": d.tokens, "cost": d.cost,
                                     "ms": round((time.perf_counter() - t0) * 1000)})
        for entry in self.state.entries.values():   # the look judges each repair that it can see
            if entry.trial and entry.first_at is not None:
                entry.trial = False
                if entry.name in owners:
                    self._rebind(entry, entry.bound + 1, "the repair of the runner did not hold")
                else:
                    self._rebind(entry, 0, "the repair held")
        if not outside:
            return
        target = next((e for e in self.state.entries.values() if e.name in owners and e.name not in self.repairs), None)
        beside = target is not None and target.runner in self.runners
        if beside:   # a model runner repairs its object beside the loop
            self._wake(outside, target)
            target = None
        if target is not None or news:   # the main agent does not wait for a runner (docs/DECISIONS.md, D52)
            self._wake(outside, target)
        elif not beside:   # each statement is with a runner, or the main agent answered a wake that had it: the wake waits for the repair
            self._event({"event": "held", "look": self.counts["looks"], "outside": [o["variable"] for o in outside]})

    def _owners(self, statement: str) -> list:
        """The objects that a statement of the run is about. A variable that the main agent wrote for the plan has no object."""
        return [e.name for e in self.state.entries.values() if e.error is not None] if statement == NO_ERROR else []

    def _rebind(self, entry: Entry, bound: int, why: str) -> None:
        """The step inside an object: bind it to another runner of its list. After the last one, the runner is the main agent."""
        was, entry.bound = entry.runner, min(bound, len(entry.runners))
        if entry.runner != was:   # a step that keeps the runner changes nothing
            self.counts["rebinds"] += 1
            self._event({"event": "rebind", "object": entry.name, "from": was, "to": entry.runner, "why": why})

    def _wake(self, outside: list, target: Entry | None) -> None:
        """The step-function: the runner of the object replaces what is broken. The run takes the new version and goes on."""
        self.counts["wakes"] += 1
        self._send({"id": f"wake_{self.counts['wakes']}", "kind": "wake", "outside": outside,
                    **({"object": target.name} if target is not None else {})}, target)

    def _send(self, wake: dict, target: Entry | None) -> None:
        """Give a wake to the current runner of its object. A model runner works beside the loop. The main agent gets the wake when
        the object has no other runner. The human gets only actions, through the gate."""
        if target is not None and target.runner in self.runners:
            self._start(target, wake)
        else:
            self._apply(wake, target, "main", self._park("woke", wake))

    def _start(self, entry: Entry, wake: dict) -> None:
        """Start one wake of a model runner. `_collect` takes its answer."""
        name, now = entry.runner, self.state.clock.elapsed()
        self.counts["runner_wakes"] += 1
        sources, parents = [], list(entry.handle.info.get("parents", []))
        while parents:   # each handle that the handle of the object was made from
            sources.append(parents.pop())
            parents += self.orchestrator.resolve(sources[-1]).info.get("parents", [])
        question = {"object": entry.summary(now), "sources": sources, "not_true_now": wake["outside"], "tried": list(wake.get("tried", [])),
                    "other_objects": [e.summary(now) for e in self.state.entries.values() if e is not entry],
                    "limits": self.state.limits(), **({"notes": self.state.notes} if self.state.notes else {})}
        work = {"wake": wake, "entry": entry, "runner": name, "question": question, "tokens": 0, "cost": 0.0}

        def submit(value) -> str | None:
            if self._ended:
                return "the run ended"
            value = {"objects": [{"name": entry.name, **{k: value[k] for k in ("handle", "operation") if k in value}}]}
            try:
                work["reply"] = self._accept(value, wake["id"], by_model=True)
            except (ValueError, HandleError) as e:   # the fault goes back to the runner
                return str(e)

        def spend(tokens: int, cost: float = 0.0) -> int:
            work["tokens"], work["cost"] = work["tokens"] + tokens, work["cost"] + cost
            return self._spend(tokens, cost)

        def run() -> None:
            t0 = time.perf_counter()
            try:
                work["out"] = self.runners[name](question, submit, spend, max(self.state.wall_seconds - now, 0))
            except Exception as e:   # a runner that fails gave no repair: this binding is not adequate
                work["out"] = Outcome([], f"the runner failed: {type(e).__name__}: {e}")
            work["ms"] = round((time.perf_counter() - t0) * 1000)

        entry.repair = wake["id"]
        self._event({"event": "repair", "wake": wake["id"], "object": entry.name, "runner": name})
        work["thread"] = threading.Thread(target=run, name=f"uniselector-{self.id}-{name}", daemon=True)
        self.repairs[entry.name] = work
        work["thread"].start()

    def _collect(self) -> None:
        """Take the answer of each model runner that ended. With no repair, the same wake goes to the next runner of the object."""
        for name, work in list(self.repairs.items()):
            if work["thread"].is_alive():
                continue
            del self.repairs[name]
            self.told = set()   # the result of a repair is new: a statement that waited for it wakes the main agent again
            wake, entry, out = work["wake"], work["entry"], work["out"]
            entry.repair = None
            record = {"run": self.id, "wake": wake["id"], "object": name, "runner": work["runner"], "reason": out.reason,
                      "tokens": work["tokens"], "cost": work["cost"], "ms": work["ms"]}
            self.log.write("runners", {**record, "question": work["question"], "calls": out.calls, "repair": work.get("reply", {}).get("value")})
            self._event({"event": "runner", **record})
            if self.state.entries.get(name) is not entry:   # the main agent wrote the object while the runner worked: its version stays
                self._event({"event": "dropped", "wake": wake["id"], "object": name, "runner": work["runner"]})
            elif "reply" in work:
                self._apply(wake, entry, work["runner"], work["reply"])
            else:   # this binding is not adequate: the next runner of the object reads what this one found
                wake.setdefault("tried", []).append({"runner": work["runner"], "result": cut(out.reason, self.cfg.value_chars)})
                if out.main:   # the next runner of the list cannot do more: the wake is not about a break of the object
                    self._rebind(entry, len(entry.runners), "the runner gave the wake to the main agent")
                else:
                    self._rebind(entry, entry.bound + 1, "the runner gave no repair")
                self._send(wake, entry)

    def _apply(self, wake: dict, target: Entry | None, by: str, reply: dict) -> None:
        """Take the answer to a wake: the new versions of the objects."""
        for name in reply["remove"]:
            del self.state.entries[name]
        for entry in reply["entries"]:
            old = self.state.entries.get(entry.name)
            if old is not None:   # a new version keeps the binding of its name
                entry.bound = min(old.bound, len(entry.runners))
            self.state.entries[entry.name] = entry
            self.handles.append(entry.handle)
            self._event({"event": "object", **entry.summary(self.state.clock.elapsed())})
        if reply["variables"]:
            self.state.variables = reply["variables"]
        self.ending = reply["end"]
        self.state.fresh = True   # the answer changed the run: the next look does not wait for a new output
        now = self.state.entries.get(target.name) if target is not None else None
        if now is not None and now.action is None:
            now.trial = True   # the next look that sees an output of the object judges the repair
        if by == "main":   # the main agent knows these statements now. They wait while a runner works.
            self.told = {o["variable"] for o in wake["outside"]} if self.repairs else set()
        self.last_wake = {**wake, "by": by, "answer": reply["value"]}
        self._event({"event": "answer", "wake": wake["id"], "by": by, "value": reply["value"]})

    def _accept(self, value, written: str, by_model: bool = False) -> dict:
        """Check an answer to a wake, and take its objects. A fault goes back to the one that answered."""
        check(value, ANSWER_SCHEMA, "answer")
        unknown = [n for n in value.get("remove", []) if n not in self.state.entries]
        if unknown:
            raise ValueError(f"the run has no object {unknown[0]!r}")
        entries = self.prepare(value.get("objects", []), written, self.state.entries)
        if by_model and entries[0].action is not None:
            raise ValueError("a model runner cannot write an action")
        return {"value": value, "entries": entries, "remove": value.get("remove", []), "variables": value.get("variables"),
                "end": value.get("end", False)}

    def answer(self, question: str, value) -> None:
        """The main agent's answer to a wake. A fault in it goes back to the main agent, and the run stays awake."""
        with self._lock:
            if self.status != "woke" or self.pending["id"] != question:
                raise ValueError(f"run {self.id} has no pending wake {question}")
        reply = self._accept(value, question)
        with self._lock:
            self._reply = reply
            self.status, self.pending = "running", None   # a status call right after this one must not show the old wake
            self._lock.notify_all()

    # ---- actions ------------------------------------------------------------------------------

    def _action(self, entry: Entry) -> None:
        """An action: the orchestrator cannot invoke it. It makes a request for the runner of the object, and the gate runs the action
        after an approval."""
        self.counts["confirmations"] += 1
        name, input, info = entry.operation["capability"], entry.operation.get("input", {}), entry.handle.info
        target = {"id": entry.id, "type": info["type"], **{k: info["descriptor"][k] for k in ("name", "description") if info["descriptor"].get(k)}}
        try:   # the main agent decides on real information: what the target shows now
            target["now"] = cut(self._bounded(entry.handle.observe), self.cfg.value_chars)
        except HandleError as e:
            target["now"] = f"error {e.code}"
        request = {"id": f"req_{self.counts['confirmations']}", "kind": "confirmation", "object": entry.name, "capability": name,
                   "description": entry.action["description"], "input": input, "target": target,
                   "human_only": "main" not in entry.runners, "holder": entry.holder, "with": WITH[entry.runner]}
        request["graph"] = self.gate.open(f"uniselector-{self.id}-{request['id']}", entry.id, name, input)
        if entry.runner == "human":
            self._to_human(request)
        reply = self._park("waiting_human" if entry.runner == "human" else "waiting_confirm", request)
        self._rebind(entry, 0, "one decision is made")
        self._event({"event": "confirmation", "request": request["id"], "capability": name, **reply})
        if reply["decision"] == "deny":
            self._show(entry, error={"code": "DENIED", "message": f"the {reply['by']} denied this action"})
        elif "result" in reply:   # the human's command ran the graph, with a token that this process does not have
            self._take(entry, reply["result"])
        elif entry.holder == "human" and self.gate.human_client is None:   # and the command did not run it
            self._take(entry, {"status": "waiting"})
        else:
            by_human = reply["by"] == "human"
            if not by_human or self.gate.human_client is not None:
                self.gate.approve(request["graph"], by_human=by_human)
            # The principal that holds the action runs the graph.
            self._take(entry, self._bounded(lambda: self.gate.run(request["graph"], by_human=entry.holder == "human")))

    def _to_human(self, request: dict) -> None:
        self.counts["human_requests"] += 1
        # With a runtime behind a URL, the human approves in DataHandles with a token of their own.
        url = f" --url {self.cfg.target}" if self.cfg.target.startswith(("http://", "https://")) else ""
        request["with"], request["command"] = "human", f"uniselector-confirm {self.log.dir} {request['id']} approve{url}"

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
                    self._reply = {**json.loads(decision.read_text()), "by": "human"}
            reply, self._reply = self._reply, None
            self.status, self.pending = "running", None
            self.state.clock.start()
        return reply

    def confirm(self, request: str, decision: str) -> str:
        """The main agent's decision on a request. Return where the request is now."""
        with self._lock:
            if self.status not in ("waiting_confirm", "waiting_human") or self.pending["id"] != request:
                raise ValueError(f"run {self.id} has no pending request {request}")
            if decision == "deny" or (decision == "approve" and self.status == "waiting_confirm"):
                self._reply = {"decision": decision, "by": "main agent"}
                self.status, self.pending = "running", None
                self._lock.notify_all()
                return "running"
            if self.status == "waiting_confirm":   # `human`: the object binds its next runner for this one decision
                entry = self.state.entries[self.pending["object"]]
                self._rebind(entry, entry.bound + 1, "the main agent passed the decision on")
                self.status = "waiting_human"
                self._to_human(self.pending)
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
        return {**out, "limits": self.state.limits(), "counts": dict(self.counts), "next": NEXT[status]}


def why_text(status: str, why: str, pending: dict | None) -> str:
    if status in ENDED:
        return why
    if status == "woke":
        return (f"the object {pending['object']}: " if "object" in pending else "") + "outside its limits: " + "; ".join(f"{o['variable']} (probability that it is true: {o['probability']})" for o in pending["outside"])
    if status in ("waiting_confirm", "waiting_human"):
        return f"the action {pending['capability']} of the object {pending['object']} needs a confirmation from the {pending['with']}"
    return "the objects run, and no look found a variable outside its limits"


class Harness:
    """The runs of one process, and the four operations of the agent surface."""

    def __init__(self, cfg: Config, clients: dict | None = None, decide=None, runners: dict | None = None):
        self.cfg = cfg
        self.clients = clients or make_clients(cfg)
        self.gate = Gate(self.clients["planner"], cfg.orchestrator, cfg.human, self.clients.get("human"))
        self.decide = decide or Decider(cfg.decision_url, cfg.decision_model, os.environ[cfg.decision_key_env])
        # A model runner works as the orchestrator's principal, so the runtime gives it no action.
        self.runners = runners if runners is not None else {
            name: ModelRunner(r["url"], r["model"], os.environ[r["key_env"]] if "key_env" in r else None, r["wake_tokens"],
                              self.clients["orchestrator"], r.get("body")) for name, r in cfg.runners.items()}
        self.runs = {}

    def prepare(self, objects: list, written: str, current: dict | None = None) -> list:
        """Take objects into a run. The main agent can hand off only a handle that it holds, and only an operation that the handle has.
        `current` has the objects of the run: a new version keeps the variables and the runners of its name."""
        entries = []
        for o in objects:
            old = (current or {}).get(o["name"])
            self.clients["planner"].resolve(o["handle"])
            actions = {c["name"]: c for c in self.gate.delegate(o["handle"])}
            handle = self.clients["orchestrator"].resolve(o["handle"])
            capabilities = {c["name"]: c for c in handle.describe()["capabilities"]} | actions
            operation, name = o["operation"], o["operation"]["capability"]
            variables = o.get("variables", old.variables if old is not None else [])
            if name not in capabilities and o.get("runners") == ["human"]:
                # An action that only the human's principal holds. The runtime checks the input when the human runs it (docs/GAPS.md, G16).
                entries.append(Entry(o["name"], handle, operation, written, {"description": HELD_BY_HUMAN}, variables, ["human"], "human"))
                continue
            if name not in capabilities:
                raise ValueError(f"the handle of the object {o['name']!r} has no capability {name!r} that you hold. It has: "
                                 f"{', '.join(sorted(capabilities))}. For an action that only the human holds, state runners: [\"human\"]")
            bad = violations(capabilities[name]["input_schema"], operation.get("input", {}))
            if bad:
                raise ValueError(f"the input of the operation of the object {o['name']!r} does not match the schema of {name}: {bad[0]}")
            if capabilities[name]["execution"]["mode"] == "stream":
                raise ValueError(f"{name} is a stream. The operation of an object gives one value: read the newest value or a recorded window")
            if name in actions:   # the binding of a confirmation: the main agent, then the human
                runners = ["human"] if name in self.cfg.human_only or o.get("runners") == ["human"] else ["main", "human"]
                if o.get("runners", runners) != runners:
                    raise ValueError(f"the operation of the object {o['name']!r} is an action. Its runners are {json.dumps(runners)}")
            else:
                runners = o.get("runners", old.runners if old is not None and old.action is None else [])
                unknown = [r for r in runners if r != "main" and r not in self.runners]
                if unknown:
                    raise ValueError(f"the object {o['name']!r} has no runner {unknown[0]!r}. A runner of an object that is not an action is "
                                     f"`main` or a model runner of the configuration: {', '.join(sorted(self.runners)) or 'it has none'}")
                if any(r in self.runners for r in runners):
                    self.gate.delegate(o["handle"], sources=True)
            entries.append(Entry(o["name"], handle, operation, written, actions.get(name), variables, runners))
        return entries

    def submit(self, plan: dict) -> dict:
        check(plan)
        entries = self.prepare(plan["objects"], "plan")
        run_id = f"run_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"
        run = self.runs[run_id] = Run(run_id, plan, entries, self.cfg, self.clients["orchestrator"], self.gate, self.decide, self.prepare, self.runners)
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
            out["note"] = "Only the human can approve this request. It is with the human."
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
