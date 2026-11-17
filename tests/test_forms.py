"""The forms of an output: no runner makes a form while each variable is inside its limits. A wake waits for the forms of the objects
that it shows, and a runner that gives no good form leaves the list."""
import json
import threading
import time
from pathlib import Path

import pytest

from conftest import Runner, finish, harness, shows
from test_run import SLEEP, append, lines, purchases, rows_of, table, thing
from uniselector.decide import render
from uniselector.runner import Outcome

GOOD = "The summary starts with the word good."
FULL = "The count is the count of the purchases."


def says(word):
    """A runner with no model: its form is `word` and what the object shows."""
    def runner(question, submit, spend, seconds):
        runner.seen.append(question)
        spend(500)
        if word is not None:
            runner.faults.append(submit(f"{word} {question['object']['output']}"))
    runner.seen, runner.faults = [], []
    return runner


def world(cfg, events, runners, form, good=lambda state: True):
    """A run of one object `purchases` with one form `summary`. The variable of the plan is true while `sleep["on"]` is. `good` says
    if the check of the form is true in a state."""
    sleep = {"on": True}

    def rule(state, statement):
        return good(state) if GOOD in statement else statement != SLEEP or sleep["on"]

    h = harness(cfg, rule, runners)
    source = table(h, events)
    run = h.submit({"objects": [{**thing("purchases", purchases(source)), "forms": {"summary": form}}], "variables": [SLEEP]})["run"]
    return h, source, run, sleep


def form_of(rows, word="good", behind=0):
    return {"summary": {"value": f'{word} {json.dumps({"rows": rows})}', "changes_behind": behind}}


def binds(summary):
    return [(e["form"], e["from"], e["to"]) for e in lines(summary, "events") if e["event"] == "rebind"]


def looked(h, run, test, seconds=30):
    """Wait until the decision model made a look at a state that passes `test`. Return the looks of the run."""
    end = time.time() + seconds
    while time.time() < end:
        log = Path(h.status(run)["logs"]) / "decisions.jsonl"   # the first look makes the file
        looks = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        if any(test(look["state"]) for look in looks):
            return looks
        time.sleep(0.02)
    raise AssertionError("no look saw the state")


def event(h, run, name, seconds=30):
    """Wait until the log of the run has an event `name`."""
    end = time.time() + seconds
    while time.time() < end:
        if [e for e in lines(h.status(run), "events") if e["event"] == name]:
            return
        time.sleep(0.02)
    raise AssertionError(f"the run had no event {name}")


def test_a_binding_adds_nothing_while_each_variable_is_inside_its_limits(cfg, events):
    """The same object in two runs: one with no binding, and one with a runner and a form. Nothing fails."""
    h = harness(cfg, runners={"local": Runner(lambda question: None), "writer": says("good")})
    source = table(h, events)
    bare = {**thing("purchases", purchases(source)), "variables": [FULL]}
    bound = {**bare, "runners": ["local"], "forms": {"summary": {"runners": ["writer"], "variables": [GOOD]}}}
    runs = [h.submit({"objects": [o], "variables": [SLEEP]})["run"] for o in (bare, bound)]
    for more in (0, 2, 3):   # the output changes two times, and each run looks at each output
        append(events, more)
        looks = [looked(h, run, lambda state: state["objects"][0].get("output") == json.dumps({"rows": rows_of(events)})) for run in runs]

    def read(look):   # what the decision model got at one look, with no times and no counts
        return [line for line in render(look["state"]).splitlines() if not line.startswith(("  runs:", "  the operation runs now", "time:"))]

    # The decision model got the same text and the same questions: a binding adds no token to a look. A run can make its first
    # look before the first output, and the time decides that. So the test reads the looks at an output.
    looks = [[look for look in run if look["state"]["objects"][0].get("output")] for run in looks]
    assert {json.dumps(read(look)) for look in looks[0]} == {json.dumps(read(look)) for look in looks[1]}
    assert {json.dumps(look["statements"]) for look in looks[0]} == {json.dumps(look["statements"]) for look in looks[1]}
    # No runner started, and the run with the bindings spent only its looks.
    assert h.runners["local"].seen == [] and h.runners["writer"].seen == []
    for run in runs:
        summary = h.status(run)
        assert summary["limits"]["tokens"] == 100 * summary["counts"]["looks"] and summary["state"] == "running"
        assert summary["counts"] | {"looks": 0, "operations": 0} == {"looks": 0, "wakes": 0, "runner_wakes": 0, "rebinds": 0, "forms": 0,
                                                                      "operations": 0, "confirmations": 0, "human_requests": 0}
        assert {e["event"] for e in lines(summary, "events")} == {"object", "observation"}
    assert h.status(runs[1])["objects"][0]["forms"] == {"summary": None}


def test_a_runner_makes_a_form_only_when_a_wake_is_due_and_the_wake_has_it(cfg, events):
    h, source, run, sleep = world(cfg, events, {"local": says("good")}, {"runners": ["local"]})
    first = rows_of(events)
    shows(h, run, "purchases", lambda o: o.get("output"))   # an object with no output has no form, and its wake does not wait
    append(events, 1)   # the object shows the new output before the wake is due, so the form is of that output
    shows(h, run, "purchases", lambda o: o.get("output") == json.dumps({"rows": first + 1}))
    sleep["on"] = False   # a variable of the plan leaves its limits: a wake is due
    woke = finish(h, run, until=("woke",))
    assert woke["objects"][0]["forms"] == form_of(first + 1) and (woke["counts"]["forms"], woke["counts"]["wakes"]) == (1, 1)
    # The look that found the failure started the runner and held the wake. The look after the form sent the wake.
    order = [e["event"] for e in lines(woke, "events") if e["event"] in ("forming", "held", "form", "woke")]
    assert order == ["forming", "held", "form", "woke"]
    assert [look["reason"] for look in lines(woke, "decisions") if "statements" in look][-1] == "change"   # the form made a look due at once
    assert [e["forms"] for e in lines(woke, "events") if e["event"] == "held"] == [["purchases.summary"]]
    # The runner got the object and the name of the form. Its tokens count against the budget of the run, and the object shows them.
    question = h.runners["local"].seen[0]
    assert (question["form"], question["object"]["output"], question["tried"]) == ("summary", json.dumps({"rows": first + 1}), [])
    assert woke["limits"]["tokens"] == 500 + 100 * woke["counts"]["looks"] and woke["objects"][0]["runner_tokens"] == 500
    record = lines(woke, "runners")[0]
    assert (record["form"], record["runner"], record["reason"], record["value"]) == ("summary", "local", "form", form_of(first + 1)["summary"]["value"])
    # After the answer, the failure is over: the form is gone, and no runner makes it again.
    sleep["on"] = True
    h.answer(run, "wake_1", {"objects": []})
    append(events, 1)
    looked(h, run, lambda state: state["objects"][0].get("output") == json.dumps({"rows": first + 2}))
    after = h.status(run)
    assert (after["state"], after["objects"][0]["forms"], len(h.runners["local"].seen)) == ("running", {"summary": None}, 1)
    for runners, fault in ((["nobody"], "no runner 'nobody'"), (["main"], "no runner 'main'")):   # the list of a form names runners that work beside the loop
        with pytest.raises(ValueError, match=fault):
            h.submit({"objects": [{**thing("all", source), "forms": {"summary": {"runners": runners}}}], "variables": [SLEEP]})
    room = h.clients["planner"].register("room", adapter="room", source={"interval_ms": 20})
    with pytest.raises(ValueError, match="An action has no forms"):
        h.submit({"objects": [{**thing("heater", room, "room.heater", on=True), "forms": {"summary": {"runners": ["local"]}}}], "variables": [SLEEP]})


def test_one_look_reads_each_form_of_a_wake(cfg, events):
    go, true = threading.Event(), {SLEEP: True}

    def slow(question, submit, spend, seconds):
        go.wait(20)
        submit("a late form")

    h = harness(cfg, lambda state, statement: true.get(statement, True), {"fast": says("good"), "slow": slow})
    source = table(h, events)
    forms = {"summary": {"runners": ["fast"]}, "notes": {"runners": ["slow"]}}
    run = h.submit({"objects": [{**thing("purchases", purchases(source)), "forms": forms}], "variables": [SLEEP]})["run"]
    shows(h, run, "purchases", lambda o: o.get("output"))
    true[SLEEP] = False
    append(events, 1)
    event(h, run, "form")   # the first form came, and the runner of the other form works
    time.sleep(0.3)
    go.set()
    woke = finish(h, run, until=("woke",))
    made = woke["objects"][0]["forms"]
    assert (made["summary"]["value"], made["notes"]["value"], woke["counts"]["forms"]) == (f'good {json.dumps({"rows": rows_of(events)})}', "a late form", 2)
    # No look came for the first form alone: a look with a part of the forms can give no wake.
    part = [look for look in lines(woke, "decisions") if look["reason"] == "change"
            and [f is None for f in look["state"]["objects"][0]["forms"].values()] == [False, True]]
    assert not part


def test_a_runner_whose_form_does_not_pass_its_check_leaves_the_list(cfg, events):
    def good(state):   # the check of the form reads the form
        return state["objects"][0]["forms"]["summary"]["value"].startswith("good")

    h, source, run, sleep = world(cfg, events, {"local": says("poor"), "top": says("good")}, {"runners": ["local", "top"], "variables": [GOOD]}, good)
    first = rows_of(events)
    shows(h, run, "purchases", lambda o: o.get("output"))
    sleep["on"] = False
    woke = finish(h, run, until=("woke",))
    statement = f"About the form `summary` of the object `purchases`: {GOOD}"
    assert woke["objects"][0]["forms"] == form_of(first) and binds(woke) == [("summary", "local", "top")]
    # The next runner read the form that did not pass, and the sentence that was not true of it. The wake has them too.
    tried = h.runners["top"].seen[0]["tried"]
    assert (tried[0]["runner"], tried[0]["value"], tried[0]["not_true"][0]["variable"]) == ("local", form_of(first, "poor")["summary"]["value"], statement)
    assert h.runners["top"].seen[0]["true_of_a_good_form"] == [GOOD]
    assert [(t["form"], t["runner"], t["result"]) for t in woke["pending"]["tried"]] == [("summary", "local", "the form did not pass its check")]
    looks = [look for look in lines(woke, "decisions") if "statements" in look]
    assert [look["outside"] for look in looks if statement in look["statements"]] == [[SLEEP, statement], [SLEEP]]   # the check waits for a form
    for state, statements in h.decide.seen:
        assert (statement in statements) == bool(state["objects"][0]["forms"]["summary"])
    # The runner that failed stays out: the form of the next wake comes from the other one.
    h.answer(run, "wake_1", {"objects": []})
    again = finish(h, run, until=("woke",))
    assert (again["pending"]["id"], len(h.runners["local"].seen), len(h.runners["top"].seen), "tried" in again["pending"]) == ("wake_2", 1, 2, False)
    assert again["counts"]["rebinds"] == 1


def test_the_wake_goes_with_no_form_after_the_last_runner_of_the_form(cfg, events):
    def broken(question, submit, spend, seconds):
        raise RuntimeError("no such model")

    h, source, run, sleep = world(cfg, events, {"local": says(None), "top": broken}, {"runners": ["local", "top"]})
    shows(h, run, "purchases", lambda o: o.get("output"))
    sleep["on"] = False
    woke = finish(h, run, until=("woke",))
    assert (woke["pending"]["id"], woke["objects"][0]["forms"], woke["counts"]["forms"]) == ("wake_1", {"summary": None}, 0)
    assert woke["pending"]["tried"] == [{"form": "summary", "runner": "local", "result": "the runner returned, and the run took nothing from it"},
                                        {"form": "summary", "runner": "top", "result": "the runner failed: RuntimeError: no such model"}]
    assert binds(woke) == [("summary", "local", "top"), ("summary", "top", None)]
    # A new version keeps the forms of its name, and the list of a form stays as short as it is.
    h.answer(run, "wake_1", {"objects": [thing("purchases", purchases(source))]})
    kept = finish(h, run, until=("woke",))
    assert (kept["pending"]["id"], kept["objects"][0]["written"], kept["objects"][0]["forms"]) == ("wake_2", "wake_1", {"summary": None})
    assert len(h.runners["local"].seen) == 1 and "tried" not in kept["pending"]
    # The main agent states the runners of the form again, and the form has them again. `forms: {}` removes the form.
    h.runners["local"] = says("good")
    sleep["on"] = True   # the new version shows its first output before the next wake is due: an object with no output has no form
    h.answer(run, "wake_2", {"objects": [{**thing("purchases", purchases(source)), "forms": {"summary": {"runners": ["local"]}}}]})
    shows(h, run, "purchases", lambda o: o["written"] == "wake_2" and o.get("output"))
    append(events, 1)
    shows(h, run, "purchases", lambda o: o.get("output") == json.dumps({"rows": rows_of(events)}))
    sleep["on"] = False
    bound = finish(h, run, until=("woke",))
    assert (bound["pending"]["id"], bound["objects"][0]["forms"]) == ("wake_3", form_of(rows_of(events)))
    h.answer(run, "wake_3", {"objects": [{**thing("purchases", purchases(source)), "forms": {}}]})
    gone = finish(h, run, until=("woke",))
    assert gone["pending"]["id"] == "wake_4" and "forms" not in gone["objects"][0] and gone["counts"]["forms"] == 1


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

    h, source, run, sleep = world(cfg, events, {"embedder": embed}, {"runners": ["embedder"]})
    shows(h, run, "purchases", lambda o: o.get("output"))
    sleep["on"] = False
    woke = finish(h, run, until=("woke",))
    form = json.loads(woke["objects"][0]["forms"]["summary"]["value"])["handle"]
    # No value and a handle that is not there go back to the runner as faults.
    assert faults[0] == "the form has no value" and faults[1] and faults[2] is None
    # The form is an ordinary handle: the main agent's principal reads it, and the lineage of the run has it.
    assert h.clients["planner"].resolve(form).invoke("dataset.count") == {"rows": rows_of(events)}
    h.close()
    assert form in (h.runs[run].log.dir / "lineage.json").read_text()


def test_a_form_that_comes_after_the_failure_is_over_gives_no_wake(cfg, events):
    go = threading.Event()

    def slow(question, submit, spend, seconds):
        go.wait(20)
        submit("a late form")

    h, source, run, sleep = world(cfg, events, {"slow": slow}, {"runners": ["slow"], "variables": [GOOD]})
    shows(h, run, "purchases", lambda o: o.get("output"))
    sleep["on"] = False
    append(events, 1)
    event(h, run, "held")
    assert h.status(run)["state"] == "running"   # the wake waits for its form
    append(events, 1)   # one more look finds the same failure while the runner works. It does not start the runner again
    looked(h, run, lambda state: state["objects"][0].get("output") == json.dumps({"rows": rows_of(events)}))
    sleep["on"] = True   # the variable is true again before the form came
    append(events, 1)
    looked(h, run, lambda state: state["objects"][0].get("output") == json.dumps({"rows": rows_of(events)}))
    go.set()
    shows(h, run, "purchases", lambda o: h.status(run)["counts"]["forms"] == 1)
    looks = len(lines(h.status(run), "decisions"))
    looked(h, run, lambda state: len(lines(h.status(run), "decisions")) > looks)   # the look that the late form started
    time.sleep(0.3)
    summary = h.status(run)
    assert (summary["state"], summary["counts"]["wakes"], summary["objects"][0]["forms"]) == ("running", 0, {"summary": None})
    assert len([e for e in lines(summary, "events") if e["event"] == "forming"]) == 1
    # One look judged the late form, and the state said how old the form was.
    statement = f"About the form `summary` of the object `purchases`: {GOOD}"
    behind = [state["objects"][0]["forms"]["summary"]["changes_behind"] for state, statements in h.decide.seen if statement in statements]
    assert len(behind) == 1 and behind[0] >= 2


def test_a_form_of_a_version_that_is_gone_is_not_taken(cfg, events):
    go, true = threading.Event(), {SLEEP: True, FULL: True}

    def slow(question, submit, spend, seconds):
        go.wait(20)
        spend(500)
        submit("a late form")

    h = harness(cfg, lambda state, statement: next((v for k, v in true.items() if k in statement), True), {"slow": slow})
    source = table(h, events)
    formed = {**thing("purchases", purchases(source)), "forms": {"summary": {"runners": ["slow"]}}}
    run = h.submit({"objects": [formed, {**thing("all", source), "variables": [FULL]}], "variables": [SLEEP]})["run"]
    shows(h, run, "all", lambda o: o.get("output"))
    true[SLEEP] = False   # a wake about the plan is due, and it waits for the form
    event(h, run, "held")
    true[FULL] = False   # the check of the other object is not true: its wake waits for no form
    append(events, 1)
    woke = finish(h, run, until=("woke",))
    assert (woke["pending"]["id"], woke["pending"]["object"]) == ("wake_1", "all")
    true.update({SLEEP: True, FULL: True})
    h.answer(run, "wake_1", {"objects": [thing("purchases", purchases(source)), thing("all", source)]})   # the main agent writes the two objects again
    shows(h, run, "purchases", lambda o: o["written"] == "wake_1" and o.get("output"))
    go.set()
    event(h, run, "form")
    time.sleep(0.3)
    summary = h.status(run)
    # The form was of the old version, so the run did not take it. The new version shows what the runner spent.
    assert (summary["state"], summary["counts"]["forms"], summary["objects"][0]["forms"]) == ("running", 0, {"summary": None})
    assert summary["objects"][0]["runner_tokens"] == 500


def test_a_compaction_runner_works_only_when_the_context_of_the_main_agent_is_near_its_limit(cfg, events, tmp_path):
    near = "The context of the main agent is below 80% of the tokens at which it compacts by itself."
    report = tmp_path / "context.json"

    def context(tokens):
        report.write_text(json.dumps({"tokens": tokens, "compacts_at": 10_000}))

    def idle(question, submit, spend, seconds):   # a runner that says that it compacted, and the context stays as it is
        idle.seen.append(question)
        submit("compacted")

    def compact(question, submit, spend, seconds):
        compact.seen.append(question)
        context(3_000)
        submit("the context went from 9000 to 3000 tokens")

    idle.seen, compact.seen = [], []
    h = harness(cfg, lambda state, statement: near not in statement or state["main_context"]["tokens"] < 8_000, {"idle": idle, "compact": compact})
    source = table(h, events)
    plan = {"objects": [{**thing("purchases", purchases(source)), "forms": {"compaction": {"runners": ["idle", "compact"], "variables": [near]}}}],
            "variables": [SLEEP]}
    with pytest.raises(ValueError, match="main_context"):   # with no report of the context, no sentence about it can be judged
        h.submit(plan)
    cfg.main_context = str(report)
    context(1_000)
    run = h.submit(plan)["run"]
    append(events, 1)
    looks = looked(h, run, lambda state: state["objects"][0].get("output") == json.dumps({"rows": rows_of(events)}))
    # The sentence is in each look, and the look has the context. No runner started: the binding is silent.
    assert all(look["statements"][-1].endswith(near) and look["state"]["main_context"]["tokens"] == 1_000 for look in looks)
    assert "context of the main agent: 1000 tokens. That is 10% of the 10000 tokens" in render(looks[-1]["state"])
    assert (idle.seen, compact.seen, h.status(run)["counts"]["forms"]) == ([], [], 0)
    # The context is near its limit. The first runner gives a form, and the sentence stays false: it leaves. The second one compacts.
    context(9_000)
    append(events, 1)
    looked(h, run, lambda state: state["main_context"]["tokens"] == 3_000)
    summary = h.status(run)
    assert (len(idle.seen), len(compact.seen), binds(summary)) == (1, 1, [("compaction", "idle", "compact")])
    assert compact.seen[0]["tried"][0] | {"not_true": 0} == {"runner": "idle", "result": "the form did not pass its check", "value": "compacted",
                                                             "not_true": 0}
    # No wake came, and the run goes on. The look after the compaction read the form, and then the form is gone.
    assert (summary["state"], summary["counts"]["wakes"], summary["counts"]["forms"]) == ("running", 0, 2)
    assert [e["event"] for e in lines(summary, "events") if e["event"] in ("forming", "form", "rebind", "held", "woke")] == [
        "forming", "form", "rebind", "forming", "form"]
    append(events, 1)
    looked(h, run, lambda state: state["objects"][0].get("output") == json.dumps({"rows": rows_of(events)}))
    after = h.status(run)
    assert (after["objects"][0]["forms"], len(compact.seen), after["counts"]["forms"]) == ({"compaction": None}, 1, 2)
