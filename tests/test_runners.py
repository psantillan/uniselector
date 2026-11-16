"""The binding of an object to its runners, with runners that follow a function and a real DataHandles runtime."""
import json
import threading
import time

import pytest

from conftest import Runner, finish, harness, shows, spent
from test_run import SLEEP, append, lines, purchases, rows_of, table, thing
from uniselector.decide import NO_ERROR
from uniselector.runner import Outcome

FULL = "The count is the count of the purchases."
GOAL = "The work can go on."


def rename(events, old="event_type", new="kind"):
    """Give a column a new name. The file changes in one step, so a reader never gets a part of it."""
    new_file = events.with_suffix(".new")
    new_file.write_text(events.read_text().replace(old, new, 1))
    new_file.replace(events)


def world(cfg, events, runners):
    """A run of one object `purchases`. Each runner writes a filter on the source of the object, as the orchestrator's principal:
    `kind = VALUE` for a text, an operation that the handle does not have for "fault", and no repair for None."""
    made = {"doubt": False}

    def build(value):
        def repair(question):
            source = h.clients["orchestrator"].resolve(question["sources"][-1])
            new = source.invoke("dataset.filter", {"where": [{"column": "kind", "op": "eq", "value": value}]})
            return thing("ignored", new, "dataset.total" if value == "fault" else "dataset.count")
        return repair

    def rule(state, statement):   # the check of the object is true when its count is the real count
        shown = state["objects"][0].get("output")
        return FULL not in statement or (shown == json.dumps({"rows": rows_of(events)}) and not made["doubt"])

    h = harness(cfg, rule, {name: Runner(build(value) if value else lambda question: None) for name, value in runners.items()})
    source = table(h, events)
    plan = {"objects": [{**thing("purchases", purchases(source)), "variables": [FULL], "runners": list(runners)}], "variables": [SLEEP]}
    return h, source, h.submit(plan)["run"], made


def binds(summary):
    return [(e["from"], e["to"]) for e in lines(summary, "events") if e["event"] == "rebind"]


def test_the_runner_of_an_object_repairs_it_and_the_main_agent_sleeps(cfg, events):
    h, source, run, _ = world(cfg, events, {"local": "purchase"})
    first = shows(h, run, "purchases", lambda o: o.get("output"))
    assert (first["runner"], first["variables"]) == ("local", [FULL])
    rename(events)   # the schema breaks. The object shows an error, and the wake goes to the runner of the object
    shown = shows(h, run, "purchases", lambda o: o["written"] == "wake_1" and o.get("output") == json.dumps({"rows": rows_of(events)}))
    assert shown["runner"] == "local" and shown["handle"] != first["handle"]
    summary = finish(h, run, until=("running",))
    assert summary["counts"] | {"looks": 0, "operations": 0} == {"looks": 0, "wakes": 1, "runner_wakes": 1, "rebinds": 0, "forms": 0, "operations": 0,
                                                                  "confirmations": 0, "human_requests": 0}
    assert not [e for e in lines(summary, "events") if e["event"] == "woke"]   # the main agent got nothing
    assert (summary["last_wake"]["by"], summary["last_wake"]["object"]) == ("local", "purchases")
    # The runner got its object, the reason, and the source of the object. It got no other history.
    question = h.runners["local"].seen[0]
    assert question["object"]["error"]["code"] and question["not_true_now"][0]["variable"] == NO_ERROR and question["sources"] == [source.id]
    record = lines(summary, "runners")[0]
    assert (record["runner"], record["reason"], record["repair"]["objects"][0]["name"]) == ("local", "repair", "purchases")
    assert record["calls"] and summary["limits"]["tokens"] == 1000 + 100 * summary["counts"]["looks"]   # its tokens count against the budget
    # The check of the object is a variable of the run: the look asks it with the name of the object.
    assert f"About the object `purchases`: {FULL}" in lines(summary, "decisions")[-1]["statements"]


def test_a_repair_that_does_not_hold_binds_the_next_runner_for_one_wake(cfg, events):
    """The local model writes a filter that counts nothing. The same wake path binds the top model, and then the local model again."""
    h, source, run, made = world(cfg, events, {"local": "buy", "top": "purchase"})
    shows(h, run, "purchases", lambda o: o.get("output"))
    rename(events)
    shows(h, run, "purchases", lambda o: o["written"] == "wake_2" and o.get("output") == json.dumps({"rows": rows_of(events)}))
    shows(h, run, "purchases", lambda o: o["runner"] == "local")   # the next look judged the repair of the top model: it held
    summary = finish(h, run, until=("running",))
    assert binds(summary) == [("local", "top"), ("top", "local")]
    assert [(r["wake"], r["runner"]) for r in lines(summary, "runners")] == [("wake_1", "local"), ("wake_2", "top")]
    assert summary["counts"]["wakes"] == 2 and not [e for e in lines(summary, "events") if e["event"] == "woke"]
    # The next fault goes to the local model again: the top model had the object for one wake.
    made["doubt"] = True
    append(events, 1)
    shows(h, run, "purchases", lambda o: o["written"] == "wake_3")
    assert lines(h.status(run), "runners")[2]["runner"] == "local"


def test_the_wake_comes_to_the_main_agent_after_the_last_runner(cfg, events):
    h, source, run, _ = world(cfg, events, {"local": None, "top": "fault"})   # one gives up, one writes a fault
    shows(h, run, "purchases", lambda o: o.get("output"))
    rename(events)
    summary = finish(h, run, until=("woke",))
    # One wake: each runner of the list got it, and no one gave a repair that the run could take.
    assert (summary["pending"]["id"], summary["pending"]["object"], summary["counts"]["runner_wakes"]) == ("wake_1", "purchases", 2)
    assert binds(summary) == [("local", "top"), ("top", "main")] and summary["objects"][0]["runner"] == "main"
    assert "in_repair" not in summary["objects"][0]
    assert h.runners["local"].faults == ["the runner gave up"] and "dataset.total" in h.runners["top"].faults[0]
    assert [t["runner"] for t in summary["pending"]["tried"]] == ["local", "top"] and "dataset.total" in summary["pending"]["tried"][1]["result"]
    assert h.runners["top"].seen[0]["tried"] == [{"runner": "local", "result": "the runner gave up"}]   # the next runner reads what the first one found
    h.answer(run, "wake_1", {"objects": [thing("purchases", purchases(source, "kind"))]})
    shown = shows(h, run, "purchases", lambda o: o["written"] == "wake_1" and o["runner"] == "local")   # the repair held
    assert shown["variables"] == [FULL] and h.status(run)["last_wake"]["by"] == "main"   # the new version kept the check and the runners


def test_a_step_that_keeps_the_runner_is_not_a_rebind(cfg, events):
    made = {"doubt": False}
    h = harness(cfg, lambda state, statement: FULL not in statement or not made["doubt"])
    source = table(h, events)
    mine = {**thing("purchases", purchases(source)), "variables": [FULL], "runners": ["main"]}   # the list has only the main agent
    run = h.submit({"objects": [mine], "variables": [SLEEP]})["run"]
    shows(h, run, "purchases", lambda o: o.get("output"))
    rename(events)
    finish(h, run, until=("woke",))
    made["doubt"] = True   # the repair of the main agent does not hold
    h.answer(run, "wake_1", {"objects": [thing("purchases", purchases(source, "kind"))]})
    shows(h, run, "purchases", lambda o: o["written"] == "wake_1")
    summary = finish(h, run, until=("woke",))
    assert (summary["pending"]["id"], summary["objects"][0]["runner"]) == ("wake_2", "main")
    assert binds(summary) == [] and summary["counts"]["rebinds"] == 0


def test_a_model_runner_cannot_write_an_action_and_a_plan_cannot_name_a_runner_that_is_not_there(cfg, events):
    room = {}
    h = harness(cfg, runners={"local": Runner(lambda question: thing("ignored", room["handle"], "room.heater", on=True))})
    room["handle"] = h.clients["planner"].register("room", adapter="room", source={"interval_ms": 20})
    source = table(h, events)
    for runners, fault in ((["nobody"], "no runner 'nobody'"), (["human"], "no runner 'human'")):
        with pytest.raises(ValueError, match=fault):
            h.submit({"objects": [{**thing("all", source), "runners": runners}], "variables": [SLEEP]})
    with pytest.raises(ValueError, match="is an action"):
        h.submit({"objects": [{**thing("heater", room["handle"], "room.heater", on=True), "runners": ["local"]}], "variables": [SLEEP]})
    run = h.submit({"objects": [{**thing("purchases", purchases(source)), "runners": ["local"]}], "variables": [SLEEP]})["run"]
    shows(h, run, "purchases", lambda o: o.get("output"))
    rename(events)
    summary = finish(h, run, until=("woke",))
    assert h.runners["local"].faults == ["a model runner cannot write an action"] and room["handle"].observe()["heater"] is False
    assert summary["counts"]["confirmations"] == 0


def test_a_variable_with_no_object_wakes_the_main_agent_while_a_runner_works_and_waits_after_the_answer(cfg, events):
    go, made = threading.Event(), {"goal": True}

    def slow(question):   # the runner works until the test lets it end
        go.wait(20)
        return thing("ignored", purchases(h.clients["orchestrator"].resolve(question["sources"][-1]), "kind"))

    h = harness(cfg, lambda state, statement: statement != GOAL or made["goal"], {"local": Runner(slow)})
    source = table(h, events)
    mine = {**thing("purchases", purchases(source)), "variables": [FULL], "runners": ["local"]}
    run = h.submit({"objects": [mine, thing("all", source)], "variables": [GOAL]})["run"]
    shows(h, run, "purchases", lambda o: o.get("output"))
    rename(events)
    shown = shows(h, run, "purchases", lambda o: "in_repair" in o)
    assert (shown["in_repair"], shown["runner"]) == ("wake_1", "local") and "error" in shown
    made["goal"] = False   # a variable of the plan leaves its limits while the runner works
    append(events, 3)
    # The main agent gets the variable at the next look. It does not wait for the runner, and the summary shows the repair.
    woke = finish(h, run, until=("woke",))
    assert (woke["pending"]["id"], "object" in woke["pending"], woke["objects"][0]["in_repair"]) == ("wake_2", False, "wake_1")
    assert [o["variable"] for o in woke["pending"]["outside"]] == [NO_ERROR, GOAL]
    h.answer(run, "wake_2", {"objects": []})   # the main agent lets the runner work
    append(events, 2)
    # The other object ran again, and the decision model looked again. The statements that the main agent knows wait for the repair.
    all_ = shows(h, run, "all", lambda o: o.get("output") == json.dumps({"rows": 65}))
    held = spent(h, run, woke["limits"]["tokens"] + 200)
    assert all_["runs"] > woke["objects"][1]["runs"] and held["counts"]["looks"] >= woke["counts"]["looks"] + 2
    assert (held["state"], held["counts"]["wakes"], held["objects"][0]["in_repair"]) == ("running", 2, "wake_1")
    go.set()
    again = finish(h, run, until=("woke",))
    # The repair came. Its result is new, so the variable that still is not true wakes the main agent again.
    assert again["objects"][0]["written"] == "wake_1" and "in_repair" not in again["objects"][0]
    assert (again["pending"]["id"], again["pending"]["outside"][0]["variable"], "object" in again["pending"]) == ("wake_3", GOAL, False)
    log = lines(again, "events")
    held = [e for e in log if e["event"] == "held"]
    assert len(held) >= 2 and held[-1]["outside"] == [NO_ERROR, GOAL]
    # A look does not ask the check of an object that is with its runner.
    looks = lines(again, "decisions")
    assert held[0]["look"] > next(look["look"] for look in looks if look["outside"])   # the look that starts a runner holds nothing
    assert looks[held[-1]["look"] - 1]["statements"] == [NO_ERROR, GOAL] and FULL in looks[0]["statements"][-1]
    order = [(e["event"], e.get("by")) for e in log if e["event"] in ("repair", "runner", "answer", "woke")]
    assert order == [("repair", None), ("woke", None), ("answer", "main"), ("runner", None), ("answer", "local"), ("woke", None)]
    h.answer(run, "wake_3", {"objects": []})   # no runner works now, so the statement does not wait
    spent(h, run, again["limits"]["tokens"] + 100)
    assert finish(h, run, until=("woke",))["pending"]["id"] == "wake_4"


def test_one_look_wakes_the_runner_of_the_object_and_the_main_agent(cfg, events):
    go = threading.Event()

    def slow(question):
        go.wait(20)
        return thing("ignored", purchases(h.clients["orchestrator"].resolve(question["sources"][-1]), "kind"))

    # The variable of the plan leaves its limits at the look that sees the break of the object.
    h = harness(cfg, lambda state, statement: statement != GOAL or "error" not in state["objects"][0], {"local": Runner(slow)})
    mine = {**thing("purchases", purchases(table(h, events))), "variables": [FULL], "runners": ["local"]}
    run = h.submit({"objects": [mine], "variables": [GOAL]})["run"]
    shows(h, run, "purchases", lambda o: o.get("output"))
    rename(events)
    woke = finish(h, run, until=("woke",))
    assert (woke["pending"]["id"], "object" in woke["pending"], woke["objects"][0]["in_repair"]) == ("wake_2", False, "wake_1")
    assert (woke["counts"]["wakes"], woke["counts"]["runner_wakes"]) == (2, 1)
    assert [look["outside"] for look in lines(woke, "decisions") if look["outside"]] == [[NO_ERROR, GOAL]]   # one look gave the two wakes
    h.answer(run, "wake_2", {"objects": []})
    go.set()
    shows(h, run, "purchases", lambda o: o["written"] == "wake_1" and o.get("output"))
    summary = finish(h, run, until=("running",))
    assert summary["counts"]["wakes"] == 2 and summary["last_wake"]["by"] == "local"   # the repair made the variable true again


def test_the_version_of_the_main_agent_stays_when_a_runner_ends_after_it(cfg, events):
    go = threading.Event()

    def slow(question):
        go.wait(20)
        return thing("ignored", purchases(h.clients["orchestrator"].resolve(question["sources"][-1]), "kind"))

    h = harness(cfg, runners={"local": Runner(slow)})
    source = table(h, events)
    large = source.invoke("dataset.filter", {"where": [{"column": "amount", "op": "gt", "value": 100}]})
    run = h.submit({"objects": [{**thing("purchases", purchases(source)), "runners": ["local"]}, thing("large", large)], "variables": [SLEEP]})["run"]
    shows(h, run, "large", lambda o: o.get("output"))
    # The objects break one after the other, so that no look can see the two breaks in a different order.
    rename(events)   # the first object breaks, and it goes to its runner
    shows(h, run, "purchases", lambda o: "in_repair" in o)
    rename(events, "amount", "price")   # the second object breaks, and it goes to the main agent
    woke = finish(h, run, until=("woke",))
    assert (woke["pending"]["id"], woke["pending"]["object"], woke["objects"][0]["in_repair"]) == ("wake_2", "large", "wake_1")
    mine = purchases(source, "kind")
    h.answer(run, "wake_2", {"objects": [thing("purchases", mine), thing("large", mine)]})   # the main agent writes the two objects
    shows(h, run, "purchases", lambda o: o["written"] == "wake_2" and o.get("output"))
    go.set()
    time.sleep(1)
    summary = finish(h, run, until=("running",))
    assert [(o["written"], o["handle"]) for o in summary["objects"]] == [("wake_2", mine.id)] * 2 and "in_repair" not in summary["objects"][0]
    log = lines(summary, "events")
    assert [e["object"] for e in log if e["event"] == "dropped"] == ["purchases"] and [e["by"] for e in log if e["event"] == "answer"] == ["main"]
    assert lines(summary, "runners")[0]["reason"] == "repair"   # the log keeps what the runner gave


def test_a_runner_that_raises_gave_no_repair_and_the_next_runner_gets_the_wake(cfg, events):
    def broken(question, submit, spend, seconds):
        raise RuntimeError("no such model")

    h, source, run, _ = world(cfg, events, {"local": "purchase", "top": "purchase"})
    h.runners["local"] = broken
    shows(h, run, "purchases", lambda o: o.get("output"))
    rename(events)
    shows(h, run, "purchases", lambda o: o["written"] == "wake_1" and o.get("output") == json.dumps({"rows": rows_of(events)}))
    summary = h.status(run)
    assert [(r["runner"], r["reason"], r["tokens"]) for r in lines(summary, "runners")] == [
        ("local", "the runner failed: RuntimeError: no such model", 0), ("top", "repair", 1000)]
    assert binds(summary)[0] == ("local", "top") and h.runners["top"].seen[0]["tried"][0]["result"] == "the runner failed: RuntimeError: no such model"


def test_a_runner_gives_a_wake_about_an_object_that_works_to_the_main_agent(cfg, events):
    def works(question, submit, spend, seconds):
        return Outcome([], "the runner gave the wake to the main agent: the object works", main=True)

    h, source, run, made = world(cfg, events, {"local": "purchase", "top": "purchase"})
    h.runners["local"] = works
    shows(h, run, "purchases", lambda o: o.get("output"))
    made["doubt"] = True   # the check of the object is not true, and the object has no break
    append(events, 1)
    summary = finish(h, run, until=("woke",))
    # The wake did not go to the next runner of the list.
    assert (summary["pending"]["object"], binds(summary), h.runners["top"].seen) == ("purchases", [("local", "main")], [])
    assert summary["pending"]["tried"] == [{"runner": "local", "result": "the runner gave the wake to the main agent: the object works"}]


def test_the_token_budget_holds_while_a_runner_works(cfg, events):
    ended, late = threading.Event(), []

    def greedy(question, submit, spend, seconds):   # it spends until the run has nothing left
        requests = 0
        while spend(1000) > 0:
            requests += 1
            time.sleep(0.005)
        while h.status(run)["state"] != "limit":
            time.sleep(0.005)
        late.append(submit(thing("ignored", purchases(source, "kind"))))   # a repair after the end of the run
        ended.set()
        return Outcome([], f"{requests} requests")

    h, source, run, _ = world(cfg, events, {"local": "purchase"})
    h.runners["local"] = greedy
    shows(h, run, "purchases", lambda o: o.get("output"))
    rename(events)
    summary = finish(h, run)
    assert ended.wait(5) and summary["state"] == "limit" and "the token budget is spent" in summary["why"]
    assert late == ["the run ended"] and h.status(run)["objects"][0]["written"] == "plan"   # the run takes no repair after its end
    # The runner stopped at the request that passed the budget: the spend is over the budget by less than one request and one look.
    assert cfg.budget_tokens <= h.status(run)["limits"]["tokens"] < cfg.budget_tokens + 1100


CODE = '''
def by_kind(client, value="purchase"):
    """A runner with no model: a filter on the new column name, on the source of the broken object."""
    def repair(question, submit, spend, seconds):
        source = client.resolve(question["sources"][-1])
        new = source.invoke("dataset.filter", {"where": [{"column": "kind", "op": "eq", "value": value}]})
        # A fault goes back as a text, and the runner corrects its value.
        if submit("the new filter") == "a repair is an object with `handle` and `operation`":
            submit({"handle": new.id, "operation": {"capability": "dataset.count"}})
    return repair

not_a_runner = lambda client: "a text"
'''


def test_a_runner_is_any_function_of_the_users_code_or_of_the_agents_code(cfg, events, tmp_path):
    code = tmp_path / "fix.py"
    code.write_text(CODE)
    cfg.runners = {"script": {"call": f"{code}:by_kind", "value": "purchase"}}   # the user names this function in the configuration
    h = harness(cfg)
    source = table(h, events)

    def plan(runner):
        return {"objects": [{**thing("purchases", purchases(source)), "runners": [runner]}], "variables": [SLEEP]}

    # A plan can name code only when the user lets it: that code runs in the process of the harness.
    with pytest.raises(ValueError, match="plan_callables"):
        h.submit(plan(f"{code}:by_kind"))
    h.cfg.plan_callables = True
    for name, fault in (("nothing", "did not load: AttributeError"), ("not_a_runner", "must return a function")):
        with pytest.raises(ValueError, match=fault):
            h.submit(plan(f"{code}:{name}"))
    runs = [h.submit(plan(runner))["run"] for runner in ("script", f"{code}:by_kind")]
    for run in runs:
        shows(h, run, "purchases", lambda o: o.get("output"))
    rename(events)
    for run, runner in zip(runs, ("script", f"{code}:by_kind")):
        shown = shows(h, run, "purchases", lambda o: o["written"] == "wake_1" and o.get("output") == json.dumps({"rows": rows_of(events)}))
        summary = finish(h, run, until=("running",))
        # A function that returns nothing is a runner: the run took its repair, and the main agent got no wake.
        record = lines(summary, "runners")[0]
        assert (shown["runner"], record["runner"], record["reason"], record["tokens"]) == (runner, runner, "repair", 0)
        assert not [e for e in lines(summary, "events") if e["event"] == "woke"]
