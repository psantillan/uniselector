"""The forms of an output: a runner makes a form beside the loop, and the same binding replaces a runner that gives no good form."""
import json
import threading
import time

import pytest

from conftest import finish, harness, shows
from test_run import SLEEP, append, lines, purchases, rows_of, table, thing
from uniselector.runner import Outcome

GOOD = "The summary starts with the word good."


def says(word):
    """A runner with no model: its form is `word` and what the object shows."""
    def runner(question, submit, spend, seconds):
        runner.seen.append(question)
        spend(500)
        if word is not None:
            runner.faults.append(submit(f"{word} {question['object']['output']}"))
    runner.seen, runner.faults = [], []
    return runner


def world(cfg, events, runners, form, rule=None):
    h = harness(cfg, rule, runners)
    source = table(h, events)
    run = h.submit({"objects": [{**thing("purchases", purchases(source)), "forms": {"summary": form}}], "variables": [SLEEP]})["run"]
    return h, source, run


def form_of(rows, word="good", behind=0):
    return {"summary": {"value": f'{word} {json.dumps({"rows": rows})}', "changes_behind": behind}}


def binds(summary):
    return [(e["form"], e["from"], e["to"]) for e in lines(summary, "events") if e["event"] == "rebind"]


def test_a_runner_makes_a_form_of_each_new_output_and_the_decision_model_reads_it(cfg, events):
    h, source, run = world(cfg, events, {"local": says("good")}, {"runners": ["local"]})
    first = rows_of(events)
    assert shows(h, run, "purchases", lambda o: o["forms"]["summary"])["forms"] == form_of(first)
    append(events, 2)
    assert shows(h, run, "purchases", lambda o: o["forms"] == form_of(first + 2))["runs"] == 2
    summary = finish(h, run, until=("running",))
    time.sleep(0.5)
    assert (summary["counts"]["forms"], summary["counts"]["wakes"], summary["counts"]["rebinds"]) == (2, 0, 0)   # the main agent slept
    # The runner got the object and the name of the form. Its tokens count against the budget of the run.
    question = h.runners["local"].seen[0]
    assert (question["form"], question["object"]["output"], question["tried"]) == ("summary", json.dumps({"rows": first}), [])
    assert h.status(run)["limits"]["tokens"] == 2 * 500 + 100 * h.status(run)["counts"]["looks"]
    # A new form is a change of the state: a look came after it, and the decision model saw the form in the object.
    seen = [state["objects"][0]["forms"] for state, _ in h.decide.seen]
    assert seen[0] == {"summary": None} and seen[-1] == form_of(first + 2)
    assert form_of(first)["summary"]["value"] in [form["summary"]["value"] for form in seen if form["summary"]]
    record = lines(summary, "runners")[0]
    assert (record["form"], record["runner"], record["reason"], record["value"]) == ("summary", "local", "form", form_of(first)["summary"]["value"])
    for runners, fault in ((["nobody"], "no runner 'nobody'"), (["main"], "no runner 'main'")):   # the list of a form names runners that work beside the loop
        with pytest.raises(ValueError, match=fault):
            h.submit({"objects": [{**thing("all", source), "forms": {"summary": {"runners": runners}}}], "variables": [SLEEP]})
    room = h.clients["planner"].register("room", adapter="room", source={"interval_ms": 20})
    with pytest.raises(ValueError, match="An action has no forms"):
        h.submit({"objects": [{**thing("heater", room, "room.heater", on=True), "forms": {"summary": {"runners": ["local"]}}}], "variables": [SLEEP]})


def test_a_form_that_does_not_pass_its_check_binds_the_next_runner_for_one_output(cfg, events):
    def rule(state, statement):   # the check of the form reads the form
        return GOOD not in statement or state["objects"][0]["forms"]["summary"]["value"].startswith("good")

    h, source, run = world(cfg, events, {"local": says("poor"), "top": says("good")}, {"runners": ["local", "top"], "variables": [GOOD]}, rule)
    first = rows_of(events)
    shows(h, run, "purchases", lambda o: o["forms"] == form_of(first))
    summary = finish(h, run, until=("running",))
    statement = f"About the form `summary` of the object `purchases`: {GOOD}"
    shows(h, run, "purchases", lambda o: len(binds(summary)) == 2)
    assert binds(summary) == [("summary", "local", "top"), ("summary", "top", "local")]   # one output with the next runner, then back
    # The next runner read the form that did not pass, and the sentence that was not true of it.
    tried = h.runners["top"].seen[0]["tried"]
    assert (tried[0]["runner"], tried[0]["form"], tried[0]["not_true"][0]["variable"]) == ("local", form_of(first, "poor")["summary"]["value"], statement)
    assert h.runners["top"].seen[0]["true_of_a_good_form"] == [GOOD]
    looks = [look for look in lines(summary, "decisions") if "statements" in look]
    assert [statement in look["statements"] for look in looks][:2] == [False, True]   # the check waits for a form
    assert [look["outside"] for look in looks if statement in look["statements"]][:2] == [[statement], []]
    # A form that does not pass is not a break of the object: no wake, and the object is not in repair.
    assert summary["counts"]["wakes"] == 0 and not [e for e in lines(summary, "events") if e["event"] in ("woke", "repair", "held")]
    append(events, 1)   # the first runner makes the form of the next output
    shows(h, run, "purchases", lambda o: o["forms"] == form_of(first + 1))
    assert [q["object"]["output"] for q in h.runners["local"].seen] == [json.dumps({"rows": n}) for n in (first, first + 1)]
    for state, statements in h.decide.seen:   # a look does not judge a form of an older output
        form = state["objects"][0]["forms"]["summary"]
        assert (statement in statements) == bool(form and form["changes_behind"] == 0)


def test_the_main_agent_gets_the_wake_after_the_last_runner_of_a_form(cfg, events):
    def broken(question, submit, spend, seconds):
        raise RuntimeError("no such model")

    h, source, run = world(cfg, events, {"local": says(None), "top": broken}, {"runners": ["local", "top"]})
    woke = finish(h, run, until=("woke",))
    assert (woke["pending"]["object"], woke["pending"]["form"], woke["pending"]["outside"]) == ("purchases", "summary", [])
    assert woke["pending"]["tried"] == [{"runner": "local", "result": "the runner returned, and the run took nothing from it"},
                                        {"runner": "top", "result": "the runner failed: RuntimeError: no such model"}]
    assert woke["why"] == "the object purchases has no form `summary`: each runner of the form was tried, and none gave a good form"
    assert woke["objects"][0]["forms"] == {"summary": None} and woke["objects"][0]["output"]   # the object works
    h.answer(run, "wake_1", {"objects": []})   # the form stays empty for this output, and the run does not ask again
    time.sleep(0.5)
    assert h.status(run)["state"] == "running" and h.status(run)["counts"]["wakes"] == 1
    append(events, 1)   # a new output: the first runner of the form makes it again
    again = finish(h, run, until=("woke",))
    assert again["pending"]["id"] == "wake_2" and len(h.runners["local"].seen) == 2
    # A new version keeps the forms of its name. `forms: {}` removes them.
    h.answer(run, "wake_2", {"objects": [thing("purchases", purchases(source))]})
    kept = finish(h, run, until=("woke",))
    assert (kept["pending"]["form"], kept["objects"][0]["written"]) == ("summary", "wake_2")
    h.answer(run, "wake_3", {"objects": [{**thing("purchases", purchases(source)), "forms": {}}]})
    shown = shows(h, run, "purchases", lambda o: o["written"] == "wake_3" and o.get("output"))
    time.sleep(0.5)
    assert "forms" not in shown and h.status(run)["state"] == "running" and h.status(run)["counts"]["wakes"] == 3
    assert binds(h.status(run))[:3] == [("summary", "local", "top"), ("summary", "top", "main"), ("summary", "main", "local")]


def test_a_form_can_be_in_a_handle_that_the_main_agent_reads(cfg, events, tmp_path):
    faults = []

    def embed(question, submit, spend, seconds):   # the engine and its vectors are code of the runner. The core gets one handle.
        client = h.clients["orchestrator"]
        rows = json.loads(question["object"]["output"])["rows"]
        path = tmp_path / f"vectors_{rows}.csv"
        path.write_text("id,x,y\n" + "".join(f"{i},{i % 3},{i % 5}\n" for i in range(rows)))
        faults.append(submit(None))
        faults.append(submit({"handle": "hdl_none"}))
        faults.append(submit({"handle": client.register(f"vectors_{rows}", adapter="duckdb", source={"path": str(path)}).id}))
        return Outcome([], "form")

    h, source, run = world(cfg, events, {"first": says(None), "embedder": embed}, {"runners": ["first", "embedder"]})
    shown = shows(h, run, "purchases", lambda o: o["forms"]["summary"])
    form = json.loads(shown["forms"]["summary"]["value"])["handle"]
    # No value and a handle that is not there go back to the runner as faults.
    assert faults[0] == "the form has no value" and faults[1] and faults[2] is None
    # No check judges this form, so the form is good when a runner gives it: the first runner has the next output.
    shows(h, run, "purchases", lambda o: len(binds(h.status(run))) == 2)
    assert binds(h.status(run)) == [("summary", "first", "embedder"), ("summary", "embedder", "first")]
    # The form is an ordinary handle: the main agent's principal reads it, and the lineage of the run has it.
    assert h.clients["planner"].resolve(form).invoke("dataset.count") == {"rows": rows_of(events)}
    h.close()
    assert form in (h.runs[run].log.dir / "lineage.json").read_text()


def test_the_end_of_a_run_waits_for_the_form_of_the_newest_version(cfg, events):
    go, state = threading.Event(), {"sleep": True}

    def slow(question, submit, spend, seconds):
        go.wait(20)
        submit(f"a form of the version of {question['object']['written']}")

    h = harness(cfg, lambda _, statement: statement != SLEEP or state["sleep"], {"slow": slow})
    source = table(h, events)
    run = h.submit({"objects": [{**thing("purchases", purchases(source)), "forms": {"summary": {"runners": ["slow"]}}}],
                    "variables": [SLEEP]})["run"]
    shows(h, run, "purchases", lambda o: o.get("output"))
    state["sleep"] = False
    append(events, 1)
    finish(h, run, until=("woke",))
    # The main agent writes a new version while the runner makes a form of the old one.
    h.answer(run, "wake_1", {"objects": [thing("purchases", purchases(source))], "end": True})
    time.sleep(0.5)
    assert h.status(run)["state"] == "running"   # the runner of the form still works
    go.set()
    done = finish(h, run)
    # The run did not take the form of the old version. It ended with a form of the new one.
    assert done["state"] == "done" and done["counts"]["forms"] == 1
    assert done["objects"][0]["forms"] == {"summary": {"value": "a form of the version of wake_1", "changes_behind": 0}}
