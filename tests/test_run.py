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

from conftest import finish, harness, shows, spent
from uniselector import confirm
from uniselector.decide import NO_ERROR

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
    run = h.submit({"objects": [thing("job", slow, "script.run"), thing("all", table(h, events))], "variables": [SLEEP]})["run"]
    shows(h, run, "all", lambda o: o["runs"] == 1)
    append(events, 2)
    assert shows(h, run, "all", lambda o: o["runs"] == 2)["output"] == json.dumps({"rows": 62})
    summary = finish(h, run)
    assert summary["state"] == "limit" and "wall time" in summary["why"] and time.time() - t0 < 6   # the job takes 30 s
    # The decision model saw that the operation ran, and for how long.
    seen = [o for state, _ in h.decide.seen for o in state["objects"] if o["name"] == "job"]
    assert seen and all("running_s" in o and "output" not in o for o in seen)


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
    h.answer(run, "wake_1", {"remove": ["heater"], "end": True})
    summary = finish(h, run)
    assert summary["state"] == "done" and [o["name"] for o in summary["objects"]] == ["reading"]


def test_a_human_only_action_needs_the_human(cfg, monkeypatch, capsys):
    cfg.human_only = ["room.heater"]
    h = harness(cfg)
    target, plan = heat(h)
    run = h.submit(plan)["run"]
    request = finish(h, run, until=("waiting_confirm",))["pending"]
    assert request["human_only"] is True
    out = h.confirm(run, request["id"], "approve")   # the main agent cannot approve it
    assert out["state"] == "waiting_human" and "human" in out["note"]
    summary = h.status(run)
    assert summary["state"] == "waiting_human" and summary["pending"]["command"] == f"uniselector-confirm {summary['logs']} req_1 approve"
    last = lines(summary, "events")[-1]   # the log shows where the request is
    assert last["event"] == "waiting_human" and last["pending"]["command"] == summary["pending"]["command"]
    time.sleep(0.7)
    assert target.observe()["heater"] is False
    monkeypatch.setattr(sys, "argv", ["uniselector-confirm", summary["logs"], request["id"], "approve"])
    confirm.main()
    assert "room.heater on room" in capsys.readouterr().out
    shows(h, run, "heater", lambda o: o["runs"] == 1)
    assert target.observe()["heater"] is True and h.status(run)["counts"]["human_requests"] == 1


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
