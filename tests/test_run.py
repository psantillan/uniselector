"""The loop, with a decider that follows a script and a real DataHandles runtime."""
import json
import sys
import time
from pathlib import Path

import pytest

from conftest import finish, harness
from uniselector import confirm
from uniselector.run import ENDED

FILTER = ["h1 dataset.filter", '"event_type"', '"eq"', '"value": x}', '"purchase"', "stop"]
PIPELINE = [*FILTER, "step_done", "h2:", "h2 dataset.count", "step_done"]
PLAN = ["keep only the purchase events", "count the rows"]


def table(h, events):
    return h.clients["planner"].register("events", adapter="duckdb", source={"path": str(events)})


def lines(summary: dict, name: str) -> list:
    return [json.loads(line) for line in (Path(summary["logs"]) / f"{name}.jsonl").read_text().splitlines()]


def test_a_plan_runs_to_the_end_and_its_lineage_replays(cfg, events):
    h = harness(cfg, PIPELINE)
    source = table(h, events)
    summary = finish(h, h.submit({"intents": PLAN, "handles": [source.id]})["run"])
    assert (summary["state"], [i["state"] for i in summary["plan"]["intents"]]) == ("done", ["done", "done"])
    expected = sum(line.split(",")[2] == "purchase" for line in events.read_text().splitlines())
    assert expected > 5 and summary["results"][-2]["output"] == json.dumps({"rows": expected})
    assert summary["results"][0]["input"] == {"where": [{"column": "event_type", "op": "eq", "value": "purchase"}]}
    # Each decision node is one log line. The last line is the outcome of the task.
    decisions = lines(summary, "decisions")
    assert len(decisions) == len(PIPELINE) + 1 and decisions[-1]["outcome"] == "done"
    assert {"state", "partial", "options", "probs", "choice"} <= set(decisions[1])
    # The lineage is a graph that DataHandles runs again.
    graph = json.loads(Path(summary["lineage"]).read_text())
    replay = h.clients["planner"].submit(graph)
    made = [h.clients["planner"].resolve(i) for i in sorted(set(replay["handles"].values()) - {source.id})]
    assert made[0].id != summary["handles"][1]["id"] and [m.invoke("dataset.count") for m in made] == [{"rows": expected}]


def test_the_token_budget_stops_the_run(cfg, events):
    cfg.budget_tokens = 350   # each scripted decision costs 100 tokens
    h = harness(cfg, PIPELINE)
    summary = finish(h, h.submit({"intents": PLAN, "handles": [table(h, events).id]})["run"])
    assert summary["state"] == "limit" and "token budget" in summary["why"]
    assert summary["limits"]["tokens"] == 400 and summary["results"] == []


def test_a_plan_can_lower_a_limit_and_cannot_raise_it(cfg, events):
    h = harness(cfg, PIPELINE)
    source = table(h, events)
    low = finish(h, h.submit({"intents": PLAN, "handles": [source.id], "limits": {"budget_tokens": 150}})["run"])
    assert low["state"] == "limit" and low["limits"]["budget_tokens"] == 150
    h.decide.script = list(PIPELINE)
    high = finish(h, h.submit({"intents": PLAN, "handles": [source.id], "limits": {"budget_tokens": 10 ** 9, "wall_seconds": 10 ** 6}})["run"])
    assert (high["limits"]["budget_tokens"], high["limits"]["wall_seconds"]) == (cfg.budget_tokens, cfg.wall_seconds)


def test_the_wall_time_stops_a_run_that_waits(cfg, events):
    cfg.wall_seconds = 0.5
    h = harness(cfg, ["wait:"] * 50)
    summary = finish(h, h.submit({"intents": PLAN, "handles": [table(h, events).id]})["run"])
    assert summary["state"] == "limit" and "wall time" in summary["why"]


def room(h):
    return h.clients["planner"].register("room", adapter="room", source={"interval_ms": 20})


HEAT = ["h1 room.heater", "true"]


def test_an_action_waits_for_a_confirmation_and_runs_one_time(cfg):
    h = harness(cfg, [*HEAT, "step_done"])
    target = room(h)
    run = h.submit({"intents": ["switch the heater on"], "handles": [target.id]})["run"]
    summary = finish(h, run, until=("waiting_confirm",))
    request = summary["pending"]
    assert (request["capability"], request["input"], request["human_only"]) == ("room.heater", {"on": True}, False)
    assert request["target"]["type"] == "room" and request["observed"]   # the main agent decides on real information
    time.sleep(0.2)
    assert target.observe()["heater"] is False   # nothing ran before the decision
    assert h.confirm(run, request["id"], "approve")["state"] == "running"
    summary = h.status(run, wait_seconds=20)   # one call waits for the end of the run
    assert summary["state"] == "done" and target.observe()["heater"] is True
    assert summary["counts"]["confirmations"] == 1 and json.loads(summary["results"][0]["output"])["heater"] is True


def test_a_denied_action_does_not_run_and_the_decision_model_sees_it(cfg):
    h = harness(cfg, [*HEAT, "wake_agent", "the handles cannot carry out"])
    target = room(h)
    run = h.submit({"intents": ["switch the heater on"], "handles": [target.id]})["run"]
    request = finish(h, run, until=("waiting_confirm",))["pending"]
    h.confirm(run, request["id"], "deny")
    summary = finish(h, run)
    assert target.observe()["heater"] is False
    assert summary["results"][0]["error"]["code"] == "DENIED" and summary["state"] == "woke"


def test_a_human_only_action_needs_the_human(cfg, monkeypatch, capsys):
    cfg.human_only = ["room.heater"]
    h = harness(cfg, [*HEAT, "step_done"])
    target = room(h)
    run = h.submit({"intents": ["switch the heater on"], "handles": [target.id]})["run"]
    request = finish(h, run, until=("waiting_confirm",))["pending"]
    assert request["human_only"] is True
    out = h.confirm(run, request["id"], "approve")   # the main agent cannot approve it
    assert out["state"] == "waiting_human" and "human" in out["note"]
    summary = h.status(run)
    assert summary["state"] == "waiting_human" and summary["pending"]["command"] == f"uniselector-confirm {summary['logs']} req_1 approve"
    time.sleep(0.7)
    assert target.observe()["heater"] is False
    monkeypatch.setattr(sys, "argv", ["uniselector-confirm", summary["logs"], request["id"], "approve"])
    confirm.main()
    summary = finish(h, run)
    assert summary["state"] == "done" and target.observe()["heater"] is True and summary["counts"]["human_requests"] == 1


def test_a_decision_that_abstains_goes_to_the_sub_agent_then_to_the_main_agent(cfg, events):
    asked = []

    def asker(question, max_tokens, seconds):
        asked.append(question)
        if question["field"] == "where[0].column":
            return {"value": "event_type", "reason": "dataset.head shows it", "calls": [{"tool": "handle_invoke"}], "tokens": 500}
        return {"needs_planner": "the plan does not say which events", "calls": [], "tokens": 300}

    # The column decision has two options that are too close. The value decision chooses `none fits`.
    script = ["h1 dataset.filter", [0.5, 0.45, 0.05, 0, 0, 0, 0, 0], '"eq"', '"value": x}', "show more", "none fits", "stop", "step_done", "step_done"]
    h = harness(cfg, script, asker=asker)
    run = h.submit({"intents": PLAN, "handles": [table(h, events).id]})["run"]
    summary = finish(h, run, until=("waiting_answer",))
    pending = summary["pending"]
    assert pending["field"] == "where[0].value" and 'chosen:     {"where": [{"column": "event_type", "op": "eq"}]}' in pending["text"]
    assert "capability: dataset.filter" in pending["text"] and pending["why"] == "the plan does not say which events"
    bad = h.runs[run]
    with pytest.raises(ValueError):
        bad.answer(pending["id"], {"not": "a scalar"})   # the value must match the schema of the field
    h.answer(run, pending["id"], "purchase")
    summary = finish(h, run)
    assert summary["state"] == "done"
    assert summary["results"][0]["input"] == {"where": [{"column": "event_type", "op": "eq", "value": "purchase"}]}
    assert summary["limits"]["tokens"] == len(script) * 100 + 800   # the spend of the sub-agent counts against the budget
    assert [q["answer"].get("value", q["answer"].get("planner_value", "-")) for q in lines(summary, "questions")] == ["event_type", "-", "purchase"]
    assert set(h.runs[run].pool.answers.values()) == {"event_type", "purchase"}   # the answers are candidates for the rest of the run


def test_a_wake_gives_a_summary_for_a_fresh_context(cfg, events):
    h = harness(cfg, [*FILTER, "step_done", "wake_agent", "the current intent is not clear"])
    source = table(h, events)
    summary = finish(h, h.submit({"intents": PLAN, "handles": [source.id], "notes": "the report is for the sales team"})["run"])
    assert (summary["state"], summary["why"]) == ("woke", "the current intent is not clear")
    assert [i["state"] for i in summary["plan"]["intents"]] == ["done", "current"] and summary["plan"]["notes"]
    assert [x["id"] for x in summary["handles"]][0] == source.id and summary["handles"][1]["origin"].startswith("from h1 by dataset.filter")
    assert summary["results"][0]["capability"] == "dataset.filter" and "plan_submit" in summary["next"]
    assert len(json.dumps(summary)) < 6000   # the summary is bounded
    assert json.loads((Path(summary["logs"]) / "summary.json").read_text())["state"] == "woke"


def test_a_slow_operation_becomes_a_job_that_the_decision_model_waits_for(cfg, tmp_path):
    script = tmp_path / "slow.py"
    script.write_text('"""Sum the numbers."""\nimport time\ntime.sleep(1.0)\nprint("total", 42)\n')
    h = harness(cfg, ["h1 script.run", "leave it out", *["wait:"] * 5, "h2 job.result", "step_done"])
    handle = h.clients["planner"].register("slow", adapter="script", source={"path": str(script)})
    summary = finish(h, h.submit({"intents": ["run the script and get its output"], "handles": [handle.id]})["run"])
    assert summary["state"] == "done", summary["why"]
    assert summary["handles"][1]["origin"] == "a job: script.run on h1" and summary["counts"]["waits"] == 5
    assert len(summary["results"]) == cfg.results_kept   # the state keeps the last results only
    assert "total 42" in summary["results"][-2]["output"]
    assert summary["handles"][1]["status"] and "completed" in summary["handles"][1]["status"]


def test_a_join_key_takes_column_names_and_a_structure_decision_is_never_a_question(cfg, tmp_path):
    (tmp_path / "orders.csv").write_text("order_id,customer,total\n" + "\n".join(f"{i},c{i % 4},{i * 10}" for i in range(12)))
    (tmp_path / "customers.csv").write_text("customer,country\n" + "\n".join(f"c{i},{'DE' if i % 2 else 'FR'}" for i in range(4)))
    # The decision on one more key is below the abstain threshold: [add, stop]. The top option stands, and no question starts.
    script = ["h1:", "h1 dataset.join", '"customer"', '"customer"', [0.45, 0.55], "leave it out", "step_done"]
    h = harness(cfg, script)
    orders, customers = (h.clients["planner"].register(n, adapter="duckdb", source={"path": str(tmp_path / f"{n}.csv")}) for n in ("orders", "customers"))
    summary = finish(h, h.submit({"intents": ["join the orders to the customers"], "handles": [orders.id, customers.id]})["run"])
    assert summary["state"] == "done" and summary["counts"]["questions"] == 0, summary["why"]
    assert summary["results"][0]["input"]["on"] == [{"left": "customer", "right": "customer"}]
    right = next(options for text, options in h.decide.seen if "`on[0].right`" in text)
    # `right` has the schema of `left`, so its first page has the column names of the two datasets, and no customer id.
    first = {o.split("  ")[0] for o in right[:-1]}
    assert first == {'"order_id"', '"customer"', '"total"', '"country"'} and right[-1].startswith("none of these")
    second = next(options for text, options in h.decide.seen if "Choose the operation on h1" in text)
    assert any(o.startswith("step_done") for o in second)   # the control options stay on the second level of the root


def test_a_run_ends_when_its_process_ends(cfg, events):
    h = harness(cfg, ["wait"] * 400)
    run = h.submit({"intents": PLAN, "handles": [table(h, events).id]})["run"]
    h.close()
    summary = h.status(run)
    assert summary["state"] == "left" and (Path(summary["logs"]) / "summary.json").exists()
    assert lines(summary, "events")[-1]["outcome"] == "left"
