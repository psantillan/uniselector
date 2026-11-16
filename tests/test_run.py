"""The loop, with a detector that follows a rule and a real DataHandles runtime."""
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import Scripted, finish, harness, shows, spent
from uniselector import confirm
from datahandles.sdk import connect

from uniselector.decide import NO_ERROR, Busy
from uniselector.run import ENDED, Harness

SLEEP = "The count is less than 1000."


def table(h, events):
    return h.clients["planner"].register("events", adapter="duckdb", source={"path": str(events)})


def purchases(source, column="event_type"):
    """What the main agent builds: the cleaning rule, as a handle."""
    return source.invoke("dataset.filter", {"where": [{"column": column, "op": "eq", "value": "purchase"}]})


def thing(name, handle, capability="dataset.count", **input):
    return {"name": name, "handle": handle.id, "operation": {"capability": capability, **({"input": input} if input else {})}}


def lines(summary: dict, name: str) -> list:
    return [json.loads(line) for line in (Path(summary["logs"]) / f"{name}.jsonl").read_text().splitlines()]


def rows_of(events, kind="purchase") -> int:
    return sum(line.split(",")[2] == kind for line in events.read_text().splitlines())


def append(events, n, kind="purchase"):
    with open(events, "a") as f:
        f.write("".join(f"{900 + i},1,{kind},10\n" for i in range(n)))


def test_an_object_follows_its_source_with_no_model_and_no_main_agent(cfg, events):
    h = harness(cfg)
    run = h.submit({"objects": [thing("purchases", purchases(table(h, events)))], "variables": [SLEEP]})["run"]
    first = rows_of(events)
    assert first > 5 and shows(h, run, "purchases", lambda o: o.get("output") == json.dumps({"rows": first}))
    append(events, 3)
    shown = shows(h, run, "purchases", lambda o: o.get("output") == json.dumps({"rows": first + 3}))
    assert shown["runs"] == 2 and shown["written"] == "plan"   # the handle of the plan gave the new count: nothing built it again
    append(events, 2, kind="view")   # the source changes, and the output of the object does not
    assert shows(h, run, "purchases", lambda o: o["runs"] == 3)["output"] == json.dumps({"rows": first + 3})
    summary = finish(h, run, until=("running",))
    assert summary["counts"]["wakes"] == 0 and summary["pending"] is None
    time.sleep(0.5)
    looks = lines(summary, "decisions")
    # The decision model looked at each change of an output. It did not look at each tick, and it did not look at each operation.
    assert [look["reason"] for look in looks].count("change") == 2 and h.status(run)["counts"]["operations"] == 3
    assert looks[0]["statements"] == [NO_ERROR, SLEEP] and looks[0]["outside"] == []
    # A wait that ends with nothing to do gives the short form.
    assert set(h.status(run, wait_seconds=0.2)) == {"run", "state", "why", "limits", "counts", "next"}


def test_a_schema_break_wakes_the_main_agent_and_it_replaces_only_the_broken_object(cfg, events):
    h = harness(cfg, lambda state, statement: '"rows": 6' in next(o for o in state["objects"] if o["name"] == "all").get("output", '"rows": 6'))
    source = table(h, events)
    run = h.submit({"objects": [thing("purchases", purchases(source)), thing("all", source)],
                    "variables": ["The count of the object `all` is less than 70."], "notes": "The feed gets new rows."})["run"]
    shows(h, run, "purchases", lambda o: o["runs"] == 1)
    kept = h.runs[run].state.entries["all"]
    events.write_text(events.read_text().replace("event_type", "kind", 1))   # the schema of the feed breaks
    summary = finish(h, run, until=("woke",))
    # The wake has the reason and the current objects. It is enough for an agent with a fresh context.
    wake = summary["pending"]
    assert (wake["id"], wake["kind"], [o["variable"] for o in wake["outside"]]) == ("wake_1", "wake", [NO_ERROR])
    broken, other = summary["objects"]
    assert broken["name"] == "purchases" and "event_type" in broken["error"]["message"] and "error" not in other
    assert summary["plan"] == {"variables": ["The count of the object `all` is less than 70."], "notes": "The feed gets new rows."}
    assert "plan_answer" in summary["next"] and NO_ERROR in summary["why"]
    # The main agent builds a new version of the broken object only, and writes it back under the same name.
    out = h.answer(run, "wake_1", {"objects": [thing("purchases", purchases(source, "kind"))]})
    assert out["accepted"] == "wake_1"
    expected = rows_of(events)
    shown = shows(h, run, "purchases", lambda o: o.get("output") == json.dumps({"rows": expected}))
    assert shown["written"] == "wake_1" and h.runs[run].state.entries["all"] is kept and kept.written == "plan"
    assert finish(h, run, until=("running",))["last_wake"]["answer"]["objects"][0]["name"] == "purchases"
    # The goal of the plan is a variable too: the feed grows, the variable leaves its limits, and the main agent ends the run.
    append(events, 12, kind="view")
    assert [o["variable"] for o in finish(h, run, until=("woke",))["pending"]["outside"]] == ["The count of the object `all` is less than 70."]
    h.answer(run, "wake_2", {"end": True})
    summary = finish(h, run)
    assert (summary["state"], summary["counts"]["wakes"]) == ("done", 2)
    decisions = lines(summary, "decisions")   # each look is one log line, and the last line is the outcome
    assert {"state", "statements", "probs", "outside", "tokens"} <= set(decisions[0]) and decisions[-1]["outcome"] == "done"
    # The lineage has each version of each object, as a graph that DataHandles runs again.
    graph = json.loads(Path(summary["lineage"]).read_text())
    filters = [n["operation"]["input"]["where"][0]["column"] for n in graph["nodes"] if n["kind"] == "operation"]
    assert sorted(filters) == ["event_type", "kind"]


def test_a_wrong_answer_goes_back_to_the_main_agent_and_the_run_stays_awake(cfg, events):
    h = harness(cfg, lambda state, statement: False)
    source = table(h, events)
    run = h.submit({"objects": [thing("all", source)], "variables": ["never true"]})["run"]
    finish(h, run, until=("woke",))
    for value, fault in (({}, "not valid"), ({"objects": [thing("all", source, "dataset.nothing")]}, "no capability"),
                         ({"objects": [thing("all", source, "dataset.head", limit="many")]}, "does not match the schema"),
                         ({"remove": ["none"]}, "no object"), ({"end": False}, "not valid")):
        with pytest.raises(ValueError, match=fault):
            h.answer(run, "wake_1", value)
    with pytest.raises(ValueError, match="no pending wake"):
        h.answer(run, "wake_7", {"end": True})
    assert h.status(run)["state"] == "woke"
    # A new list of variables is a valid answer. The next look uses it, and it does not wait for a new output.
    h.answer(run, "wake_1", {"variables": ["still never true"]})
    summary = finish(h, run, until=("woke",))
    assert summary["pending"]["outside"][0]["variable"] == "still never true" and lines(summary, "decisions")[-1]["reason"] == "change"


def test_the_token_budget_stops_the_run(cfg, events):
    cfg.budget_tokens = 150   # each look of the test costs 100 tokens
    h = harness(cfg)
    run = h.submit({"objects": [thing("all", table(h, events))], "variables": [SLEEP]})["run"]
    assert spent(h, run, 100)["state"] == "running"   # the first look, and the run goes on
    append(events, 1)   # a change: the second look
    summary = finish(h, run)
    assert summary["state"] == "limit" and "token budget" in summary["why"] and summary["limits"]["tokens"] == 200


def test_a_plan_can_lower_a_limit_and_cannot_raise_it(cfg, events):
    h = harness(cfg)
    source = table(h, events)
    low = finish(h, h.submit({"objects": [thing("all", source)], "variables": [SLEEP], "limits": {"budget_tokens": 50}})["run"])
    assert low["state"] == "limit" and low["limits"]["budget_tokens"] == 50
    high = h.status(h.submit({"objects": [thing("all", source)], "variables": [SLEEP], "limits": {"budget_tokens": 10**9, "wall_seconds": 10**6}})["run"])
    assert (high["limits"]["budget_tokens"], high["limits"]["wall_seconds"]) == (cfg.budget_tokens, cfg.wall_seconds)


def test_a_slow_operation_does_not_stop_the_other_objects_and_the_wall_time_stops_it(cfg, events, tmp_path):
    script = tmp_path / "slow.py"
    script.write_text('"""Sum the numbers."""\nimport time\ntime.sleep(30)\nprint("total", 42)\n')
    cfg.wall_seconds = 1.5
    h = harness(cfg)
    slow = h.clients["planner"].register("slow", adapter="script", source={"path": str(script)})
    t0 = time.time()
    objects = [{**thing("job", slow, "script.run"), "variables": ["The job gave a total."]}, {**thing("all", table(h, events)), "variables": ["The count is a number."]}]
    run = h.submit({"objects": objects, "variables": [SLEEP]})["run"]
    shows(h, run, "all", lambda o: o["runs"] == 1)
    append(events, 2)
    assert shows(h, run, "all", lambda o: o["runs"] == 2)["output"] == json.dumps({"rows": 62})
    summary = finish(h, run)
    assert summary["state"] == "limit" and "wall time" in summary["why"] and time.time() - t0 < 6   # the job takes 30 s
    # The decision model saw that the operation ran, and for how long.
    seen = [o for state, _ in h.decide.seen for o in state["objects"] if o["name"] == "job"]
    assert seen and all("running_s" in o and "output" not in o for o in seen)
    # The check of an object is a question only after the object showed something: the job did not, so no look judged it.
    asked = {statement for _, statements in h.decide.seen for statement in statements}
    assert "About the object `all`: The count is a number." in asked and not [statement for statement in asked if "`job`" in statement]


def test_a_silent_feed_gets_a_look_at_times_that_double(cfg, events):
    h = harness(cfg, lambda state, statement: state["objects"][0]["changed_s"] < 1.0)
    run = h.submit({"objects": [thing("all", table(h, events))], "variables": ["The feed got a new row in the last second."]})["run"]
    summary = finish(h, run, until=("woke",))
    looks = lines(summary, "decisions")
    assert [look["reason"] for look in looks] == ["change"] + ["silence"] * (len(looks) - 1) and 3 <= len(looks) <= 6
    silent = [look["state"]["objects"][0]["changed_s"] for look in looks[1:]]
    # Look k is due after a silence of 2 ticks times 2 ** k. A slow machine makes a look late, never early.
    assert all(s >= 2 * cfg.wait_seconds * 2 ** k - 0.05 for k, s in enumerate(silent)), silent
    assert summary["pending"]["outside"][0]["variable"] == "The feed got a new row in the last second."


def test_a_busy_decision_model_makes_no_look_and_the_look_stays_due(cfg, events):
    h = harness(cfg)
    script, calls = h.decide, []

    def busy(state, statements):   # the API refuses the first two requests
        calls.append(len(statements))
        if len(calls) <= 2:
            raise Busy("HTTP 429: Too Many Requests")
        return script(state, statements)

    h.decide = busy
    run = h.submit({"objects": [thing("all", table(h, events))], "variables": [SLEEP]})["run"]
    summary = spent(h, run, 100)
    assert (summary["state"], summary["counts"]["looks"], summary["limits"]["tokens"]) == ("running", 1, 100)
    log = lines(summary, "events")
    assert [e["why"] for e in log if e["event"] == "no_look"] == ["HTTP 429: Too Many Requests"] * 2
    assert [look["reason"] for look in lines(summary, "decisions")] == ["change"] and summary["objects"][0]["output"] == json.dumps({"rows": 60})


def test_a_look_at_a_silence_stays_due_when_the_decision_model_is_busy(cfg, events):
    h = harness(cfg, lambda state, statement: state["objects"][0]["changed_s"] < 1.0)
    script, refused = h.decide, []

    def busy(state, statements):   # the API refuses one request: the look that is due after a silence of 0.8 s
        if not refused and state["objects"][0]["changed_s"] >= 0.75:
            refused.append(state["objects"][0]["changed_s"])
            raise Busy("HTTP 503: Service Unavailable")
        return script(state, statements)

    h.decide = busy
    run = h.submit({"objects": [thing("all", table(h, events))], "variables": ["The feed got a new row in the last second."]})["run"]
    summary = finish(h, run, until=("woke",))
    silent = [look["state"]["objects"][0]["changed_s"] for look in lines(summary, "decisions")[1:]]
    # The look comes at the next tick. It does not wait for the silence of 1.6 s, which is the time of the look after it.
    assert len(refused) == 1 and [s for s in silent if s >= 0.75][0] < 1.5, silent


def test_a_quiet_object_shows_that_its_source_is_live(cfg, events):
    """The output of the object does not change, so no look comes from a change. The time of its last run shows that the feed stopped."""
    h = harness(cfg, lambda state, statement: state["objects"][0].get("ran_s", 0.0) < 1.0)
    large = table(h, events).invoke("dataset.filter", {"where": [{"column": "amount", "op": "gt", "value": 10_000}]})
    run = h.submit({"objects": [thing("large", large)], "variables": ["The feed got a new row in the last second."]})["run"]
    for _ in range(15):   # a new row of no interest at each tick
        append(events, 1)
        time.sleep(cfg.wait_seconds)
    live = h.status(run)
    assert live["state"] == "running" and live["counts"]["operations"] >= 8 and live["counts"]["looks"] <= 5, live["counts"]
    summary = finish(h, run, until=("woke",), seconds=10)   # the feed stopped
    shown = lines(summary, "decisions")[-1]["state"]["objects"][0]
    assert shown["ran_s"] >= 1.0 and shown["changes"] == 0 and shown["output"] == json.dumps({"rows": 0})


def room(h):
    return h.clients["planner"].register("room", adapter="room", source={"interval_ms": 20})


def heat(h, on=True):
    target = room(h)
    return target, {"objects": [thing("heater", target, "room.heater", on=on), thing("reading", target, "room.latest")], "variables": ["The room is cold."]}


def test_an_action_waits_for_a_confirmation_and_runs_one_time(cfg):
    h = harness(cfg)
    target, plan = heat(h)
    run = h.submit(plan)["run"]
    summary = finish(h, run, until=("waiting_confirm",))
    request = summary["pending"]
    assert (request["object"], request["capability"], request["input"], request["human_only"]) == ("heater", "room.heater", {"on": True}, False)
    assert request["target"]["type"] == "room" and '"heater": false' in request["target"]["now"]   # the main agent decides on real information
    time.sleep(0.2)
    assert target.observe()["heater"] is False   # nothing ran before the decision
    assert h.confirm(run, request["id"], "approve")["state"] == "running"
    shown = shows(h, run, "heater", lambda o: o["runs"] == 1)
    assert json.loads(shown["output"])["heater"] is True and target.observe()["heater"] is True
    # The room has a new version at each reading. The reading runs again each time; the action does not.
    assert shows(h, run, "reading", lambda o: o["runs"] >= 5) and h.status(run)["objects"][0]["runs"] == 1
    target.invoke("room.heater", {"on": False})   # the main agent's own switch
    time.sleep(0.5)
    summary = h.status(run)
    assert target.observe()["heater"] is False and summary["counts"]["confirmations"] == 1 and summary["state"] == "running"


def test_a_denied_action_does_not_run_and_the_denial_wakes_the_main_agent(cfg):
    h = harness(cfg)
    target, plan = heat(h)
    run = h.submit(plan)["run"]
    request = finish(h, run, until=("waiting_confirm",))["pending"]
    h.confirm(run, request["id"], "deny")
    summary = finish(h, run, until=("woke",))
    assert target.observe()["heater"] is False
    assert summary["objects"][0]["error"]["code"] == "DENIED" and summary["pending"]["outside"][0]["variable"] == NO_ERROR
    # A denial is no trial of a repair: each next request on the object is with the main agent again.
    for wake, request in (("wake_1", "req_2"), ("wake_2", "req_3")):
        h.answer(run, wake, {"objects": [thing("heater", target, "room.heater", on=True)]})
        summary = finish(h, run, until=("waiting_confirm", "waiting_human"))
        assert (summary["state"], summary["pending"]["id"]) == ("waiting_confirm", request) and binds(summary) == []
        h.confirm(run, request, "deny")
        finish(h, run, until=("woke",))
    h.answer(run, "wake_3", {"remove": ["heater"], "end": True})
    summary = finish(h, run)
    assert summary["state"] == "done" and [o["name"] for o in summary["objects"]] == ["reading"]


def by_human(monkeypatch, summary, request, decision="approve"):
    monkeypatch.setattr(sys, "argv", ["uniselector-confirm", summary["logs"], request, decision])
    confirm.main()


def binds(summary):
    return [(e["object"], e["from"], e["to"]) for e in lines(summary, "events") if e["event"] == "rebind"]


def test_a_human_only_action_starts_with_the_human(cfg, monkeypatch, capsys):
    cfg.human_only = ["room.heater"]
    h = harness(cfg)
    target, plan = heat(h)
    run = h.submit(plan)["run"]
    summary = finish(h, run, until=("waiting_human",))   # the runner of the object is the human: the request does not stop at the main agent
    request = summary["pending"]
    assert (request["human_only"], request["with"], summary["objects"][0]["runner"]) == (True, "human", "human")
    assert request["command"] == f"uniselector-confirm {summary['logs']} req_1 approve"
    out = h.confirm(run, request["id"], "approve")   # the main agent cannot approve it
    assert out["state"] == "waiting_human" and "human" in out["note"]
    last = lines(summary, "events")[-1]   # the log shows where the request is
    assert last["event"] == "waiting_human" and last["pending"]["command"] == request["command"]
    time.sleep(0.7)
    assert target.observe()["heater"] is False
    by_human(monkeypatch, summary, request["id"])
    assert "room.heater on room" in capsys.readouterr().out
    shows(h, run, "heater", lambda o: o["runs"] == 1)
    summary = h.status(run)
    assert target.observe()["heater"] is True and summary["counts"]["human_requests"] == 1 and binds(summary) == []


def test_a_denial_by_the_human_wakes_the_main_agent(cfg, monkeypatch):
    cfg.human_only = ["room.heater"]
    h = harness(cfg)
    target, plan = heat(h)
    run = h.submit(plan)["run"]
    summary = finish(h, run, until=("waiting_human",))
    by_human(monkeypatch, summary, summary["pending"]["id"], "deny")
    summary = finish(h, run, until=("woke", *ENDED))   # the human confirms actions. The human repairs nothing
    assert (summary["state"], summary["pending"]["object"], summary["objects"][0]["error"]["code"]) == ("woke", "heater", "DENIED")


def test_the_main_agent_binds_the_human_for_one_decision(cfg, monkeypatch):
    h = harness(cfg)
    target, plan = heat(h)
    run = h.submit(plan)["run"]
    request = finish(h, run, until=("waiting_confirm",))["pending"]
    assert (request["with"], request["human_only"]) == ("main agent", False)
    assert h.confirm(run, request["id"], "human")["state"] == "waiting_human"
    summary = h.status(run)
    assert (summary["pending"]["with"], summary["objects"][0]["runner"]) == ("human", "human") and binds(summary) == [("heater", "main", "human")]
    by_human(monkeypatch, summary, request["id"])
    shows(h, run, "heater", lambda o: o["runs"] == 1)
    assert target.observe()["heater"] is True
    # The next decision on the same object is with the main agent again: the human had it for one decision.
    summary = h.status(run)
    assert binds(summary) == [("heater", "main", "human"), ("heater", "human", "main")] and summary["objects"][0]["runner"] == "main"


def test_the_human_runs_an_action_that_only_they_hold_with_a_token_that_the_run_does_not_have(cfg, tmp_path, monkeypatch):
    """A DataHandles HTTP server, as in a real session. The process of the run has the tokens of the main agent and of the orchestrator."""
    tokens = {role: f"token-of-{role}" for role in (cfg.planner, cfg.orchestrator, cfg.human)}
    (tmp_path / "tokens.json").write_text(json.dumps({token: {"principal": role, "create": True} for role, token in tokens.items()}))
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    server = subprocess.Popen([sys.executable, "-m", "datahandles.protocols.http", "--state", str(tmp_path / "state"), "--tokens",
                               str(tmp_path / "tokens.json"), "--port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cfg.target = f"http://127.0.0.1:{port}"
    try:
        end = time.time() + 20
        while True:
            try:
                human = connect(cfg.target, cfg.human, token=tokens[cfg.human])
                target = human.register("room", adapter="room", source={"interval_ms": 20})
                break
            except OSError:
                assert time.time() < end
                time.sleep(0.2)
        target.grant(cfg.planner, [c["name"] for c in target.describe()["capabilities"] if c["name"] != "room.heater"])
        h = Harness(cfg, clients={"planner": connect(cfg.target, cfg.planner, token=tokens[cfg.planner]),
                                  "orchestrator": connect(cfg.target, cfg.orchestrator, token=tokens[cfg.orchestrator])}, decide=Scripted())
        switch = {name: {**thing(name, target, "room.heater", on=name == "on"), "runners": ["human"]} for name in ("on", "off")}
        run = h.submit({"objects": [switch["on"], switch["off"]], "variables": ["The room is cold."]})["run"]
        summary = finish(h, run, until=("waiting_human",))
        assert summary["pending"]["holder"] == "human" and summary["pending"]["command"].endswith(f"req_1 approve --url {cfg.target}")
        monkeypatch.setenv("UNISELECTOR_HUMAN_TOKEN", tokens[cfg.human])
        monkeypatch.setattr(sys, "argv", ["uniselector-confirm", summary["logs"], "req_1", "approve", "--url", cfg.target])
        confirm.main()   # the command approves the graph and runs it, as the human's principal
        shown = shows(h, run, "on", lambda o: o["runs"] == 1)
        assert json.loads(shown["output"])["heater"] is True and target.observe()["heater"] is True
        # With no `--url`, nothing approved the second switch in DataHandles, and the process of the run cannot run it.
        finish(h, run, until=("waiting_human",))
        by_human(monkeypatch, summary, "req_2")
        assert shows(h, run, "off", lambda o: o["runs"] == 1)["error"]["code"] == "NOT_APPROVED" and target.observe()["heater"] is True
        h.close()
    finally:
        server.terminate()
        server.wait(10)


def test_an_action_that_only_the_human_holds_runs_with_the_human(cfg, monkeypatch):
    """The main agent's principal does not hold the switch, so it cannot go around the gate (docs/FAILURES.md, F52)."""
    h = harness(cfg)
    target = h.clients["human"].register("room", adapter="room", source={"interval_ms": 20})
    target.grant(cfg.planner, [c["name"] for c in target.describe()["capabilities"] if c["name"] != "room.heater"])
    theirs = h.clients["planner"].resolve(target.id)
    with pytest.raises(Exception, match="CAPABILITY_DENIED"):
        theirs.invoke("room.heater", {"on": True})
    heater = thing("heater", theirs, "room.heater", on=True)
    with pytest.raises(ValueError, match="only the human holds"):   # the plan must say who holds the action
        h.submit({"objects": [heater], "variables": ["The room is cold."]})
    run = h.submit({"objects": [{**heater, "runners": ["human"]}, thing("reading", theirs, "room.latest")], "variables": ["The room is cold."]})["run"]
    summary = finish(h, run, until=("waiting_human",))
    request = summary["pending"]
    assert (request["holder"], request["human_only"], request["capability"], request["input"]) == ("human", True, "room.heater", {"on": True})
    assert h.confirm(run, request["id"], "approve")["state"] == "waiting_human" and target.observe()["heater"] is False
    by_human(monkeypatch, summary, request["id"])
    shown = shows(h, run, "heater", lambda o: o["runs"] == 1)
    assert json.loads(shown["output"])["heater"] is True and target.observe()["heater"] is True


def test_a_run_ends_when_its_process_ends(cfg, events):
    h = harness(cfg)
    run = h.submit({"objects": [thing("all", table(h, events))], "variables": [SLEEP]})["run"]
    h.close()
    summary = h.status(run)
    assert summary["state"] == "left" and (Path(summary["logs"]) / "summary.json").exists()
    assert lines(summary, "events")[-1]["outcome"] == "left"
    # A new process reads the summary of the run from its directory.
    assert harness(cfg).status(run)["state"] == "left"


@pytest.mark.parametrize("how", ["the client closes the input", "the client sends SIGTERM"])
def test_a_run_ends_in_the_logs_when_the_client_ends_the_server(tmp_path, events, how):
    """The real server process, with a run that works: the model server of the test takes the request and gives no reply."""
    model = socket.socket()
    model.bind(("127.0.0.1", 0))
    model.listen()
    state, runs = tmp_path / "state", tmp_path / "runs"
    # Another process registers the table: the DuckDB adapter holds a lock on the state while its process lives.
    register = "import sys; from datahandles.sdk import connect; print(connect(sys.argv[1], 'planner').register('events', adapter='duckdb', source={'path': sys.argv[2]}).id)"
    handle = subprocess.run([sys.executable, "-c", register, str(state), str(events)], capture_output=True, text=True, check=True).stdout.strip()
    (tmp_path / "uniselector.toml").write_text(
        f'[limits]\nbudget_tokens = 1000\nwall_seconds = 60\n[runtime]\ntarget = "{state}"\nruns_dir = "{runs}"\n'
        f'[decision]\ndecision_url = "http://127.0.0.1:{model.getsockname()[1]}"\ndecision_model = "none"\ndecision_key_env = "UNISELECTOR_TEST_KEY"\n')
    server = subprocess.Popen([sys.executable, "-m", "uniselector.mcp", "--config", str(tmp_path / "uniselector.toml")],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env={**os.environ, "UNISELECTOR_TEST_KEY": "none"})
    try:
        def send(message):
            server.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
            server.stdin.flush()

        send({"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}})
        server.stdout.readline()
        send({"method": "notifications/initialized"})
        plan = {"objects": [{"name": "all", "handle": handle, "operation": {"capability": "dataset.count"}}], "variables": [SLEEP]}
        send({"id": 2, "method": "tools/call", "params": {"name": "plan_submit", "arguments": {"plan": plan}}})
        run = json.loads(json.loads(server.stdout.readline())["result"]["content"][0]["text"])["run"]
        request, _ = model.accept()   # the run works: its first look is at the model server now, and it gets no reply
        if how == "the client closes the input":
            server.stdin.close()
        else:
            server.send_signal(signal.SIGTERM)
        server.wait(20)
    finally:
        server.kill()
        request.close()
        model.close()
    last = json.loads((runs / run / "events.jsonl").read_text().splitlines()[-1])
    assert last["event"] == "end" and last["outcome"] == "left"
    assert json.loads((runs / run / "summary.json").read_text())["state"] == "left"
