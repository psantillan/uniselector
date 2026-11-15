"""The loop of one run, and the harness that holds the runs.

The harness enforces two things: the hard limits, and the action gate. Each other rule is
information in the state, and the decision model judges it.

    for intent in plan.intents:
        loop:
            choice, input = walk(state)
            a control option: handle it
            an action: request a confirmation, continue when it is given
            result = invoke(choice, input)
            add a summary of the result to the state
"""
import json
import secrets
import threading
import time
import traceback
from pathlib import Path

from datahandles import HandleError
from datahandles.sdk import Handle

from . import lineage
from .config import Config, clients as make_clients
from .decide import OllamaDecider
from .gate import ALL, Gate
from .log import RunLog
from .plan import check, limits, violations
from .pool import Pool
from .state import Entry, State, cut
from .subagent import ModelAsker, question_text
from .walk import Walk

ENDED = ("done", "woke", "aborted", "limit", "failed", "left")
NEXT = {
    "running": "The run works. Call plan_status with wait_seconds: the call returns when the run needs you or the time is over.",
    "waiting_answer": "A sub-agent could not find a value. Call plan_answer with the run, the id of the question, and a value that its schema allows.",
    "waiting_confirm": "An action needs your decision. Call plan_confirm with the run, the id of the request, and approve, deny, or human.",
    "waiting_human": "The human must decide. Tell the user to run the command in `pending.command`, or the same command with deny.",
    "done": "Each intent is met. Read the handles with the DataHandles tools.",
    "woke": "The decision model returned the work to you. Make a new plan from the handles and the results here, and call plan_submit.",
    "aborted": "The decision model stopped the run. Read the results here before you make a new plan.",
    "limit": "A hard limit stopped the run. The user decides the limits. The handles here stay usable.",
    "failed": "The harness failed. The reason is in `why`.",
    "left": "The process of the run ended before the run did. Make a new plan from the handles and the results here, and call plan_submit.",
}


class _Stop(Exception):
    def __init__(self, status: str, why: str):
        self.status, self.why = status, why


class Run:
    def __init__(self, run_id: str, plan: dict, cfg: Config, clients: dict, gate: Gate, decide, asker):
        self.id, self.plan, self.cfg, self.gate, self.asker = run_id, plan, cfg, gate, asker
        self.orchestrator = clients["orchestrator"]
        budget, wall = limits(cfg, plan)
        self.log = RunLog(Path(cfg.runs_dir) / run_id)
        self.state = State(plan, budget, wall, cfg.results_kept, cfg.handles_kept, cfg.value_chars)
        self.pool = Pool(self._probe, self._observe, cfg.hidden_capabilities)
        self.walk = Walk(self.state, decide, self.pool, cfg, self._ask, self._decision, self._check)
        self.status, self.why, self.pending, self.lineage = "running", "", None, None
        self.counts = {"decisions": 0, "operations": 0, "waits": 0, "probes": 0, "questions": 0, "planner_questions": 0,
                       "confirmations": 0, "human_requests": 0}
        self.jobs = []
        self._lock = threading.Condition()
        self._reply, self._ended = None, False
        self.thread = threading.Thread(target=self._main, name=f"uniselector-{run_id}", daemon=True)

    # ---- the loop -----------------------------------------------------------------------------

    def _main(self) -> None:
        try:
            for handle_id in self.plan["handles"]:
                self._adopt(handle_id, "start")
            self.state.start = len(self.state.entries)
            for index in range(len(self.plan["intents"])):
                self.state.intent = index
                self._intent()
            self.state.intent = len(self.plan["intents"])
            self._end("done", "each intent is met")
        except _Stop as stop:
            self._end(stop.status, stop.why)
        except Exception as e:   # a fault of the harness: the run ends, and the reason goes to the main agent
            self.log.write("events", {"event": "fault", "traceback": traceback.format_exc()})
            self._end("failed", f"{type(e).__name__}: {e}")

    def _intent(self) -> None:
        looked = False
        while True:
            self._check()
            if not looked:
                self._look()
            looked = False
            choice = self.walk.root()
            self.state.step += 1
            if choice[0] == "operation":
                _, entry, capability, input = choice
                bad = violations(capability["input_schema"], input)
                if bad:
                    self._result(entry, capability, input, {"status": "failed", "error": {
                        "code": "INVALID_INPUT", "message": f"the input does not match the schema: {bad[0]}", "retryable": False}})
                elif capability in entry.actions:
                    self._action(entry, capability, input)
                else:
                    self._invoke(entry, capability, input)
                continue
            control = choice[1]
            if control == "step_done":
                self._event(self.state.record({"control": "step_done", "note": "the intent is met"}))
                return
            if control == "wait":
                self.counts["waits"] += 1
                seconds = max(min(self.cfg.wait_seconds, self.state.wall_seconds - self.state.clock.elapsed()), 0)
                time.sleep(seconds)
                # The result of a wait is what the look after it shows: the state of each job and stream (D20).
                self._look()
                looked = True
                after = "".join(f". {e.alias} after the wait: {e.status}" for e in self.state.shown() if e.status)
                self._event(self.state.record({"control": "wait", "note": f"waited {round(seconds, 1)} s{after}"}))
                continue
            raise _Stop("woke" if control == "wake_agent" else "aborted", self.walk.reason(control))

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

    def _observe(self, entry: Entry) -> dict:
        """Observe a handle. The call waits while a job runs on the resource (GAPS.md, G11), so the wall time bounds it."""
        return self._bounded(entry.handle.observe)

    def _look(self) -> None:
        """Observe the current handles. A new version is a change. No new version makes the stall signal grow."""
        changed = False
        for entry in self.state.shown():
            try:
                seen = self._observe(entry)
                version = seen.get("version")
                continuous = entry.handle.info["consistency"] == "continuous"   # a resource that changes: a stream
                if continuous:   # a look at a stream shows its newest values (docs/DECISIONS.md, D20)
                    seen = {**seen, **self.pool.newest(entry)}
                status = cut(seen, self.cfg.value_chars) if continuous or "state" in seen else None   # "state": a job
            except HandleError as e:
                version, status = f"error {e.code}", f"error {e.code}"
            changed |= entry.version is not None and version != entry.version
            entry.version, entry.status = version, status
        if changed:
            self.state.changed()
        else:
            self.state.looks += 1

    # ---- operations ---------------------------------------------------------------------------

    def _adopt(self, handle_id: str, origin: str) -> Entry:
        """Take a handle into the state. The gate gives the orchestrator its capabilities that are not actions."""
        entry = self.state.entry(handle_id)
        if entry:
            return entry
        actions = self.gate.delegate(handle_id)
        handle = self.orchestrator.resolve(handle_id)
        entry = Entry(f"h{len(self.state.entries) + 1}", handle, origin, handle.describe()["capabilities"], actions)
        return self.state.add(entry)

    def _invoke(self, entry: Entry, capability: dict, input: dict) -> None:
        """Run one operation. Each operation runs as a job, so a slow one becomes a job handle in the state and not a blocked loop."""
        name = capability["name"]
        self.counts["operations"] += 1
        t0 = time.perf_counter()
        if capability["execution"]["mode"] == "stream":
            result = self._bounded(lambda: self._first(entry, name, input))
        elif entry.handle.info["kind"] == "job":
            result = entry.handle.call(name, input)
        else:
            result = entry.handle.call(name, input, background=True)
            if result["status"] == "running":
                job = self.orchestrator.resolve(result["handles"][0]["id"])
                job.grant(self.cfg.planner, [ALL])   # a job handle belongs to its submitter only (GAPS.md, G7)
                self.jobs.append(job)
                try:
                    out = job.wait(timeout=max(min(self.cfg.wait_seconds, self.state.wall_seconds - self.state.clock.elapsed()), 0))
                    result = {"status": "completed", **({"handles": [out.info]} if isinstance(out, Handle) else {"output": out})}
                except TimeoutError:
                    pass   # the job handle goes into the state: the decision model waits for it
                except HandleError as e:
                    result = {"status": "failed", "error": e.to_dict()}
        self._result(entry, capability, input, result, round((time.perf_counter() - t0) * 1000))

    def _first(self, entry: Entry, name: str, input: dict) -> dict:
        """A stream capability: take the first event as the result."""
        try:
            stream = entry.handle.subscribe(name, input)
            try:
                return {"status": "completed", "output": next(stream, {"events": 0})}
            finally:
                stream.close()
        except HandleError as e:
            return {"status": "failed", "error": e.to_dict()}

    def _result(self, entry: Entry, capability: dict, input: dict, result: dict, ms: int = 0) -> None:
        name = capability["name"]
        record, output = {"capability": name, "target": entry.alias, "input": input}, None
        if result["status"] == "failed":
            record["error"] = {k: result["error"].get(k) for k in ("code", "message", "retryable")}
        elif result["status"] == "waiting":
            record["error"] = {"code": "NOT_APPROVED", "message": "the action has no approval, so it did not run", "retryable": False}
        elif "handles" in result:
            running = result["status"] == "running"
            origin = f"a job: {name} on {entry.alias}" if running else f"from {entry.alias} by {name} {cut(input, 160)}"
            record["handle"] = self._adopt(result["handles"][0]["id"], origin).alias
            if running:
                record["handle"] += " (a job: it runs now. Use wait, then job.status and job.result)"
        else:
            output = result.get("output")
            record["output"] = cut(output, self.cfg.value_chars)
        self._event({**self.state.record(record, output), "ms": ms})

    def _probe(self, entry: Entry, name: str):
        """A bounded probe for the candidate pool: one capability that reads, gives a value, and needs no input."""
        self.counts["probes"] += 1
        t0 = time.perf_counter()
        result = self._bounded(lambda: entry.handle.call(name, {}))
        output = result.get("output") if result["status"] == "completed" else None
        self._event({"event": "probe", "target": entry.alias, "capability": name, "ok": output is not None,
                     "bytes": len(json.dumps(output, default=str)), "ms": round((time.perf_counter() - t0) * 1000)})
        return output

    # ---- questions and confirmations ----------------------------------------------------------

    def _ask(self, question: dict):
        """The escalation of one decision: a sub-agent first, then the main agent."""
        self.counts["questions"] += 1
        question = {"id": f"q_{self.counts['questions']}", **question}
        left = self.state.budget_tokens - self.state.tokens
        result = self.asker(question, min(self.cfg.question_tokens, left), max(self.state.wall_seconds - self.state.clock.elapsed(), 1))
        self.state.tokens += result["tokens"]
        answer = {k: result[k] for k in ("value", "reason", "needs_planner", "capped", "failed") if k in result}
        if "value" in result and violations(question["schema"], result["value"]):
            answer = {"needs_planner": "the sub-agent gave a value that the schema does not allow"}
        self.log.write("questions", {"run": self.id, "step": self.state.step, "question": question, "answer": answer,
                                     "calls": result["calls"], "tokens": result["tokens"]})
        self._check()
        if "value" in answer:
            return answer["value"]
        self.counts["planner_questions"] += 1
        # The text has the plan, the choices so far, the candidates and the handles: the summary does not repeat them.
        pending = {"id": question["id"], "kind": "question", "field": question["field"], "schema": question["schema"],
                   "text": question_text(question), "why": answer["needs_planner"]}
        value = self._park("waiting_answer", pending)["value"]
        self.log.write("questions", {"run": self.id, "step": self.state.step, "question": question["id"], "answer": {"planner_value": value}})
        return value

    def _action(self, entry: Entry, capability: dict, input: dict) -> None:
        """An action: the orchestrator cannot invoke it. It makes a request, and the gate runs the action after an approval."""
        self.counts["confirmations"] += 1
        name = capability["name"]
        request = {"id": f"req_{self.counts['confirmations']}", "kind": "confirmation", "capability": name,
                   "description": capability["description"], "input": input, "target": entry.summary(self.cfg.value_chars),
                   "observed": {label: cut(value, self.cfg.value_chars) for value, _, label in self.pool.observed(entry)[:self.cfg.results_kept]},
                   "human_only": name in self.cfg.human_only, "with": "main agent"}
        request["graph"] = self.gate.open(f"uniselector-{self.id}-{request['id']}", entry.id, name, input)
        reply = self._park("waiting_confirm", request)
        self._event({"event": "confirmation", "request": request["id"], "capability": name, **reply})
        if reply["decision"] == "deny":
            self._result(entry, capability, input, {"status": "failed", "error": {
                "code": "DENIED", "message": f"the {reply['by']} denied this action", "retryable": False}})
            return
        if reply["by"] == "main agent" or self.gate.human_client is not None:
            self.gate.approve(request["graph"], by_human=reply["by"] == "human")
        t0 = time.perf_counter()
        self.counts["operations"] += 1
        self._result(entry, capability, input, self._bounded(lambda: self.gate.run(request["graph"])), round((time.perf_counter() - t0) * 1000))

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

    def answer(self, question: str, value) -> None:
        with self._lock:
            if self.status != "waiting_answer" or self.pending["id"] != question:
                raise ValueError(f"run {self.id} has no pending question {question}")
            bad = violations(self.pending["schema"], value)
            if bad:
                raise ValueError(f"the value does not match the schema of the field: {bad[0]}")
            self._reply = {"value": value}
            self.status, self.pending = "running", None   # a status call right after this one must not show the old question
            self._lock.notify_all()

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

    def _decision(self, record: dict) -> None:
        self.counts["decisions"] += 1
        self.log.write("decisions", {"run": self.id, "step": self.state.step, "intent": self.state.intent, **record})

    def _event(self, record: dict) -> None:
        self.log.write("events", {"event": "result", "run": self.id, **record})

    def _end(self, status: str, why: str) -> None:
        with self._lock:
            if self._ended:   # the first end stands
                return
            self._ended = True
        self.state.clock.stop()
        if status != "done":
            for job in self.jobs:   # the run does not leave work that nothing waits for
                try:
                    job.invoke("job.cancel")
                except HandleError:
                    pass
        try:
            graph = lineage.export(self.id, [e.handle for e in self.state.entries])
            self.lineage = str(self.log.save("lineage.json", graph))
        except Exception as e:
            self.log.write("events", {"event": "fault", "traceback": f"lineage: {type(e).__name__}: {e}"})
        self.status, self.why = status, why
        outcome = {"run": self.id, "outcome": status, "why": why, "intents_done": self.state.intent, "intents": len(self.plan["intents"]),
                   "tokens": self.state.tokens, "seconds": round(self.state.clock.elapsed(), 1), **self.counts}
        self.log.write("decisions", outcome)
        self.log.write("events", {"event": "end", **outcome})
        self.log.save("summary.json", self.summary())
        self.log.close()

    def summary(self) -> dict:
        """A bounded summary. It is enough for an agent that starts from a fresh context."""
        with self._lock:
            status, pending = self.status, self.pending
        at = self.state.intent
        return {
            "run": self.id, "state": status, "why": why_text(status, self.why, pending),
            "plan": {"intents": [{"n": i + 1, "intent": text, "state": "done" if i < at else "current" if i == at else "later"}
                                 for i, text in enumerate(self.plan["intents"])],
                     **({"notes": self.plan["notes"]} if self.plan.get("notes") else {})},
            "pending": pending,
            "handles": [e.summary(self.cfg.value_chars) for e in self.state.shown()],
            "results": self.state.results[-self.cfg.results_kept:],
            "limits": self.state.limits(), "counts": self.counts, "lineage": self.lineage, "logs": str(self.log.dir),
            "next": NEXT[status],
        }


def why_text(status: str, why: str, pending: dict | None) -> str:
    if status in ENDED:
        return why
    if status == "waiting_answer":
        return f"a sub-agent returned a question to you: {pending['why']}"
    if status in ("waiting_confirm", "waiting_human"):
        return f"the action {pending['capability']} needs a confirmation from the {pending['with']}"
    return "the run works"


class Harness:
    """The runs of one process, and the four operations of the agent surface."""

    def __init__(self, cfg: Config, clients: dict | None = None, decide=None, asker=None):
        self.cfg = cfg
        self.clients = clients or make_clients(cfg)
        self.gate = Gate(self.clients["planner"], cfg.orchestrator, cfg.subagent, cfg.human, self.clients.get("human"))
        self.decide = decide or OllamaDecider(cfg.model_url, cfg.model, cfg.num_ctx)
        self.asker = asker or ModelAsker(cfg, self.clients["subagent"])
        self.runs = {}

    def submit(self, plan: dict) -> dict:
        check(plan)
        for handle_id in plan["handles"]:
            self.clients["planner"].resolve(handle_id)   # the main agent can hand off only a handle that it holds
        run_id = f"run_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"
        run = self.runs[run_id] = Run(run_id, plan, self.cfg, self.clients, self.gate, self.decide, self.asker)
        run.log.save("plan.json", plan)
        run.thread.start()
        return {"run": run_id, "state": "running", "next": NEXT["running"]}

    def status(self, run: str, wait_seconds: float = 0) -> dict:
        """The summary of a run. With `wait_seconds`, wait first until the run needs the main agent or the time is over."""
        if run in self.runs:
            end = time.time() + wait_seconds
            while self.runs[run].status == "running" and time.time() < end:
                time.sleep(0.1)
            return self.runs[run].summary()
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
