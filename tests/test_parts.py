"""The small parts: the detector's call, the plan, the hook, and the tool surface."""
import http.server
import io
import json
import sys
import threading
import urllib.error
from types import SimpleNamespace

import pytest

from conftest import finish, harness
from uniselector import lineage, mcp, plan
from uniselector.decide import NO_ERROR, Busy, Decider, judge
from uniselector.config import load
from uniselector.hooks import pending
from uniselector.runner import ModelRunner
from uniselector.state import Entry, cut

OBJECT = {"name": "purchases", "handle": "hdl_1", "operation": {"capability": "dataset.count"}}


def stand_in(replies: list):
    """A server of the test in the place of a model API. It keeps each request, and it gives the next reply. A number is the status of
    a reply with no content."""
    seen = []

    class Api(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            reply = replies.pop(0)
            body = b"" if isinstance(reply, int) else json.dumps(reply).encode()
            self.send_response(reply if isinstance(reply, int) else 200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def test_one_look_is_one_call_to_the_decisions_api_with_one_question_for_each_variable():
    """A server of the test stands in for the Decisions API."""
    server, seen = stand_in([
        {"answers": {"v2": {"type": "noul", "noul": 0.12}, "v1": {"type": "noul", "noul": 0.93}, "v3": {"type": "refusal"}},
         "usage": {"input_tokens": 321, "output_tokens": 0, "cost": 0.0000064}}, 429, 503, 401])
    state = {"notes": "The feed gets new rows.",
             "objects": [{"name": "purchases", "handle": "hdl_1", "type": "computation", "operation": {"capability": "dataset.count"}, "written": "plan",
                          "made": 'dataset.filter {"where": []}', "made_before": "", "runner_tokens": 700, "output": '{"rows": 21}', "ran_s": 0.4, "runs": 3, "changed_s": 4.0,
                          "forms": {"summary": {"value": "21 purchases", "changes_behind": 0}, "old": {"value": "19", "changes_behind": 2},
                                    "embedding": None}},
                         {"name": "totals", "handle": "hdl_2", "type": "computation", "operation": {"capability": "dataset.head", "input": {"limit": 5}},
                          "written": "wake_1", "made_before": "dataset.sort {}", "error": {"code": "ADAPTER_ERROR", "message": "no column amount"}, "runs": 2, "changed_s": 0.1},
                         {"name": "job", "handle": "hdl_3", "type": "resource", "operation": {"capability": "script.run"}, "written": "plan",
                          "running_s": 12.5, "runs": 0, "changed_s": 12.5}],
             "limits": {"elapsed_s": 14.0, "wall_seconds": 60, "tokens": 500, "budget_tokens": 1000}}
    try:
        decide = Decider(f"http://127.0.0.1:{server.server_port}/decisions", "some/decision-model", "the-key")
        d = decide(state, [NO_ERROR, "The count is less than 200.", "The job did not finish."])
        for status in (429, 503):   # the API did not serve a right request: the look can come later
            with pytest.raises(Busy, match=f"HTTP {status}"):
                decide(state, [NO_ERROR])
        with pytest.raises(urllib.error.HTTPError):   # a wrong key is a fault, and a later look does not repair it
            decide(state, [NO_ERROR])
    finally:
        server.shutdown()
    headers, body = seen[0]
    assert headers["Authorization"] == "Bearer the-key" and body["model"] == "some/decision-model"
    # The decision model reads what each object shows. The statements are the questions, not a part of the state.
    for text in ("The feed gets new rows.", 'purchases (computation): dataset.count {}', 'output: {"rows": 21}', 'dataset.head {"limit": 5}',
                 "form `summary` of the output: 21 purchases", "form `old` of the output, 2 changes behind the output: 19",
                 'the handle is made by: dataset.filter {"where": []}', "the version before this one was made by: no operation",
                 "the handle is made by: no operation", "the version before this one was made by: dataset.sort {}",
                 "the runners of this object spent: 700 tokens",
                 "ERROR ADAPTER_ERROR: no column amount", "no output yet", "the operation runs now, for 12.5 s", "runs: 3, the last one started 0.4 s ago. last change of the output: 4.0 s ago.", "time: 14.0 s of 60 s. tokens: 500 of 1000."):
        assert text in body["state"], text
    assert "The count is less than 200." not in body["state"]
    assert "embedding" not in body["state"] and body["state"].count("made by") == 4   # a form that no runner made adds no line. Nor does an object with no `made`.
    assert list(body["questions"]) == ["v1", "v2", "v3"] and {q["type"] for q in body["questions"].values()} == {"noul"}
    assert body["questions"]["v2"]["instructions"].endswith("Statement: The count is less than 200.")
    # The probabilities are in the order of the statements, not in the order of the reply. A refusal is not a "true".
    assert (d.probs, d.tokens, d.cost) == ([0.93, 0.12, 0.0], 321, 0.0000064)
    assert judge(d, 0.5) == [1, 2] and judge(d, 0.1) == [2]


def test_a_plan_is_objects_and_variables_and_nothing_more(cfg):
    good = {"objects": [OBJECT], "variables": ["The count is less than 200."]}
    assert plan.check(good) is good and plan.check({**good, "notes": "n", "limits": {"wall_seconds": 5}})
    for bad in ({"objects": [OBJECT]}, {**good, "variables": []}, {**good, "objects": []}, {**good, "intents": ["count the rows"]},
                {**good, "objects": [{"name": "purchases", "handle": "hdl_1"}]},   # an object with no operation shows nothing
                {**good, "objects": [{**OBJECT, "expected_rows": 20}]}, {**good, "objects": [OBJECT, OBJECT]}):
        with pytest.raises(ValueError, match="not valid"):
            plan.check(bad)
    # The plan can ask for lower limits, never higher ones.
    assert plan.limits(cfg, {"limits": {"budget_tokens": 50, "wall_seconds": 10**6}}) == (50, cfg.wall_seconds)


def test_a_long_value_keeps_its_first_and_its_last_part():
    value = {"output": "first " + "x" * 2000 + " checksum: 81504f41"}
    text, size = cut(value, 600), len(json.dumps(value))
    assert text.startswith('{"output": "first ') and text.endswith('checksum: 81504f41"}')
    assert f"(cut: {size - 600} of {size} characters)" in text and len(text) < 700
    assert cut("short", 600) == "short"


def test_the_hook_shows_a_request_only_when_it_is_with_the_human(monkeypatch, capsys):
    summary = {"state": "waiting_human", "logs": "/runs/run_1",
               "pending": {"id": "req_1", "command": "uniselector-confirm /runs/run_1 req_1 approve", "capability": "room.heater", "input": {"on": True}, "target": {"id": "hdl_1", "name": "room"}}}
    for response in (summary, json.dumps(summary), [{"type": "text", "text": json.dumps(summary)}], {"structuredContent": summary}):
        assert pending.find(response) == summary
    assert pending.find({**summary, "state": "waiting_confirm"}) is None
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_name": "mcp__uniselector__plan_status", "tool_response": [{"type": "text", "text": json.dumps(summary)}]})))
    pending.main()
    shown = json.loads(capsys.readouterr().out)["systemMessage"]
    assert "uniselector-confirm /runs/run_1 req_1 approve" in shown and "room.heater" in shown
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_response": {"state": "running"}})))
    pending.main()
    assert capsys.readouterr().out == ""


def test_the_tool_surface_gives_errors_as_values(cfg, events):
    h = harness(cfg)
    variables = ["The count is less than 200."]
    assert [t["name"] for t in mcp.TOOLS] == ["plan_submit", "plan_status", "plan_answer", "plan_confirm"]
    assert "`main` (you), `local`, and `human`" in mcp.instructions(harness(cfg, runners={"local": None}))   # the main agent reads the names
    assert mcp.dispatch(h, "plan_submit", {"plan": {"objects": []}})["error"]["code"] == "INVALID_INPUT"
    assert mcp.dispatch(h, "plan_status", {"run": "run_none"})["error"]["code"] == "INVALID_INPUT"
    assert mcp.dispatch(h, "plan_submit", {"plan": {"objects": [{**OBJECT, "handle": "hdl_none"}], "variables": variables}})["error"]["code"] == "HANDLE_NOT_FOUND"
    source = h.clients["planner"].register("events", adapter="duckdb", source={"path": str(events)})
    room = h.clients["planner"].register("room", adapter="room", source={"interval_ms": 20})
    for handle, operation, fault in ((source, {"capability": "dataset.nothing"}, "It has: dataset.aggregate"),
                                     (source, {"capability": "dataset.head", "input": {"limit": "many"}}, "does not match the schema"),
                                     (room, {"capability": "room.readings"}, "is a stream")):
        error = mcp.dispatch(h, "plan_submit", {"plan": {"objects": [{"name": "x", "handle": handle.id, "operation": operation}], "variables": variables}})["error"]
        assert error["code"] == "INVALID_INPUT" and fault in error["message"], error
    assert h.runs == {}   # a plan with a fault starts no run
    run = mcp.dispatch(h, "plan_submit", {"plan": {"objects": [{**OBJECT, "handle": source.id}], "variables": variables}})["run"]
    assert mcp.dispatch(h, "plan_status", {"run": run, "wait_seconds": 0.3})["state"] == "running"
    assert mcp.dispatch(h, "plan_confirm", {"run": run, "request": "req_1", "decision": "approve"})["error"]["code"] == "INVALID_INPUT"
    assert mcp.dispatch(h, "plan_answer", {"run": run, "question": "wake_1", "value": {"end": True}})["error"]["code"] == "INVALID_INPUT"
    h.close()
    assert finish(h, run)["state"] == "left"


def test_a_model_runner_makes_one_request_and_changes_the_nodes_that_the_model_names(cfg, events):
    """A server of the test stands in for a chat completions API. The graph runs in a real DataHandles runtime."""
    h = harness(cfg)
    source = h.clients["planner"].register("events", adapter="duckdb", source={"path": str(events)})
    where = {"where": [{"column": "event_type", "op": "eq", "value": "purchase"}]}
    mine = source.invoke("dataset.filter", where).invoke("dataset.aggregate", {"group_by": ["customer_id"], "metrics": [{"fn": "sum", "column": "amount", "as": "total"}]})
    h.gate.delegate(mine.id, sources=True)
    client = h.clients["orchestrator"]
    graph = lineage.graph_of(client.resolve(mine.id))
    first, second = [n["id"] for n in graph["nodes"] if n["kind"] == "operation"]
    sort = {"by": [{"column": "total"}]}
    longer = lineage.made(lineage.graph_of(mine.invoke("dataset.sort", sort)))   # the text has each operation of a graph, in its order
    assert longer == f"{lineage.made(graph)} → dataset.sort {json.dumps(sort)}" and longer.startswith(f"dataset.filter {json.dumps(where)} → dataset.aggregate ")
    # A version that no operation made is also a version: the state shows it as the one before a replacement.
    handle = SimpleNamespace(id="hdl_1", info={"type": "computation"})
    replaced = Entry("all", handle, {"capability": "dataset.count"}, "wake_1", None, made=lineage.made(graph),
                     prior=Entry("all", handle, {"capability": "dataset.count"}, "plan", None)).summary(0.0)
    assert (replaced["made"], replaced["made_before"]) == (lineage.made(graph), "")
    rows = sum(line.split(",")[2] == "purchase" for line in events.read_text().splitlines())
    events.write_text(events.read_text().replace("event_type", "kind", 1))   # the schema breaks

    def says(tokens, name=None, arguments=None, text=None):
        calls = [{"id": "call_0", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}] if name else []
        return {"choices": [{"message": {"role": "assistant", "content": text, "tool_calls": calls}}], "usage": {"total_tokens": tokens, "cost": tokens / 1e6}}

    by_kind = {"id": first, "operation": {"capability": "dataset.filter", "input": {"where": [{**where["where"][0], "column": "kind"}]}}}
    server, seen = stand_in([
        says(700, "repair", {"nodes": [by_kind]}),
        says(10, "repair", {"nodes": [{**by_kind, "id": "op_none"}]}),
        says(10, "repair", {"nodes": [{"id": first, "operation": {"capability": "dataset.filter", "input": {"where": [{**where["where"][0], "column": "type"}]}}}]}),
        says(10, "repair", {}),
        says(10, "repair", {"operation": {"capability": "dataset.head", "input": {"n": 3}}}),
        says(100, text="The column has a new name."),
        says(50, "give_up", {"reason": "the source is gone"}),
        says(40, "to_main", {"reason": "the object works"})])
    url, given = f"http://127.0.0.1:{server.server_port}/v1/chat/completions", []
    question = {"object": {"name": "totals", "handle": mine.id, "operation": {"capability": "dataset.count"}}, "graph": graph,
                "not_true_now": [{"variable": NO_ERROR, "probability": 0.1}]}

    def wake(runner, tokens=100_000, seconds=30.0):
        """One wake in a run that has `tokens` tokens left. Return how it ended, and the spend that the runner reported."""
        spent = {"tokens": 0, "cost": 0.0}

        def spend(n, cost=0.0):
            spent["tokens"], spent["cost"] = spent["tokens"] + n, spent["cost"] + cost
            return max(tokens - spent["tokens"], 0)

        return runner(question, lambda value: given.append(value), spend, seconds), spent

    try:
        runner = ModelRunner(url, "some/chat-model", "the-key", client, {"chat_template_kwargs": {"enable_thinking": False}})
        out, spent = wake(runner)
        no_node, _ = wake(runner)
        still, _ = wake(runner)
        nothing, _ = wake(runner)
        other, _ = wake(runner)
        stopped, _ = wake(runner)
        gave_up, little = wake(runner)
        handed, _ = wake(ModelRunner(url, "some/chat-model", None, client))
        ended, none = wake(runner, tokens=0)   # the run has nothing left
    finally:
        server.shutdown()
        server.server_close()
    dead, _ = wake(runner)
    # One wake was one request: the question, the graph, and the fault of each node as it is now. The model has no tool to read more.
    assert len(seen) == 8 and (spent["tokens"], round(spent["cost"], 6), out.reason) == (700, 0.0007, "repair")
    headers, body = seen[0]
    assert headers["Authorization"] == "Bearer the-key" and body["model"] == "some/chat-model"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}   # the keys that the server of the model needs
    assert [t["function"]["name"] for t in body["tools"]] == ["repair", "give_up", "to_main"] and [m["role"] for m in body["messages"]] == ["system", "user"]
    asked = json.loads(body["messages"][1]["content"])
    assert asked["graph"] == graph and list(asked["faults_now"]) == [first] and "kind" in asked["faults_now"][first]["details"]["columns"]
    # The runner changed the one node, ran the graph one time, and gave the run the new handle with the old operation.
    assert given[0]["operation"] == {"capability": "dataset.count"} and given[0]["handle"] != mine.id
    new = client.resolve(given[0]["handle"])
    assert lineage.made(lineage.graph_of(new)) == lineage.made(graph).replace("event_type", "kind")   # each other node stayed as it was
    assert new.invoke("dataset.aggregate", {"metrics": [{"fn": "sum", "column": "total", "as": "all"}]}).invoke("dataset.head", {"n": 1})["rows"]
    assert [list(c) for c in out.calls] == [["graph", "faults"], ["tool", "arguments"], ["graph", "faults"]] and out.calls[2]["faults"] == {}
    assert no_node.reason == "the run did not take the repair: the graph has no operation node 'op_none'"
    assert still.reason.startswith("the run did not take the repair: the changed graph failed:") and "no such column" in still.reason
    assert nothing.reason == "the run did not take the repair: the answer changes nothing"
    assert other.reason == "repair" and given[1:] == [{"handle": mine.id, "operation": {"capability": "dataset.head", "input": {"n": 3}}}]
    assert stopped.reason == "the runner gave no repair: The column has a new name." and stopped.calls[-1] == {"tool": None, "text": "The column has a new name."}
    assert (gave_up.reason, gave_up.main, little["tokens"]) == ("the runner gave up: the source is gone", False, 50)
    assert (handed.reason, handed.main) == ("the runner gave the wake to the main agent: the object works", True)
    assert "Authorization" not in seen[7][0] and "chat_template_kwargs" not in seen[7][1]
    assert ended.reason == "the run has no tokens left for a repair" and none["tokens"] == 0   # no request
    assert dead.reason.startswith("the runner failed: URLError")
    assert rows > 0


def test_the_configuration_names_each_model_runner(tmp_path, monkeypatch):
    path, head = tmp_path / "uniselector.toml", "[limits]\nbudget_tokens = 1000\nwall_seconds = 60\n"
    path.write_text(head + '[runners.local]\nurl = "http://127.0.0.1:1/v1/chat/completions"\nmodel = "small"\n'
                           '[runners.top]\nurl = "https://example.org/v1/chat/completions"\nmodel = "large"\nkey_env = "TOP_KEY"\n'
                           'body = { reasoning = { enabled = false } }\n')
    cfg = load(path)
    assert list(cfg.runners) == ["local", "top"] and cfg.runners["top"]["key_env"] == "TOP_KEY" and cfg.budget_tokens == 1000
    assert cfg.runners["top"]["body"] == {"reasoning": {"enabled": False}} and "body" not in cfg.runners["local"]
    # The harness builds one runner for each table. The key comes from the environment.
    monkeypatch.setenv("TOP_KEY", "the-top-key")
    cfg.target, cfg.runs_dir = str(tmp_path / "state"), str(tmp_path / "runs")
    built = harness(cfg).runners
    assert (built["top"].key, built["top"].body, built["top"].model) == ("the-top-key", {"reasoning": {"enabled": False}}, "large")
    assert (built["local"].key, built["local"].body, built["local"].model) == (None, {}, "small")
    # A model runner makes one request for one wake, so its table has no limit of its own. A key is never a literal.
    for table in ('[runners.main]\nurl = "u"\nmodel = "m"\n', '[runners.local]\nurl = "u"\n', '[runners.local]\nurl = "u"\nmodel = "m"\nwake_tokens = 1\n',
                  '[runners.local]\nurl = "u"\nmodel = "m"\nkey = "a literal key"\n'):
        path.write_text(head + table)
        with pytest.raises(ValueError, match="must state url and model"):
            load(path)


def test_a_model_runner_makes_a_form_from_the_whole_output_with_one_request(cfg, events):
    h = harness(cfg)
    source = h.clients["planner"].register("events", adapter="duckdb", source={"path": str(events)})
    h.gate.delegate(source.id)
    reply = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_0", "type": "function", "function": {"name": "form", "arguments": json.dumps({"value": "three kinds"})}}]}}],
        "usage": {"total_tokens": 300}}
    text = {"choices": [{"message": {"role": "assistant", "content": "I cannot read it."}}], "usage": {"total_tokens": 10}}
    server, seen = stand_in([reply, text])
    given = []
    try:
        runner = ModelRunner(f"http://127.0.0.1:{server.server_port}/v1/chat/completions", "some/chat-model", None, h.clients["orchestrator"])
        # The state keeps a part of a long output. The runner reads the whole one from the handle.
        question = {"object": {"name": "rows", "handle": source.id, "operation": {"capability": "dataset.head", "input": {"n": 40}}, "output": "a part"},
                    "form": "summary", "true_of_a_good_form": ["It names each kind."]}
        out = runner(question, given.append, lambda n, cost=0.0: 1000, 30.0)
        stopped = runner(question, given.append, lambda n, cost=0.0: 1000, 30.0)
    finally:
        server.shutdown()
        server.server_close()
    assert (out.reason, given) == ("form", ["three kinds"]) and stopped.reason == "the runner gave no form: I cannot read it."
    body = seen[0][1]
    # The text of the job changes, and the tool that ends the turn. A runner of a form has no wake to give to the main agent.
    assert "You make one form of the output" in body["messages"][0]["content"] and len(seen) == 2
    asked = json.loads(body["messages"][1]["content"])
    assert asked["true_of_a_good_form"] == ["It names each kind."] and len(asked["whole_output"]["rows"]) == 40
    assert [t["function"]["name"] for t in body["tools"]] == ["form", "give_up"]


def test_the_configuration_names_a_function_as_a_runner(tmp_path):
    path, head = tmp_path / "uniselector.toml", "[limits]\nbudget_tokens = 1000\nwall_seconds = 60\n"
    (tmp_path / "mine.py").write_text("def build(client, word):\n    return lambda question, submit, spend, seconds: submit(word)\n")
    path.write_text(head + f'plan_callables = true\n[runners.script]\ncall = "{tmp_path / "mine.py"}:build"\nword = "a form"\n')
    cfg = load(path)
    assert cfg.plan_callables and cfg.runners["script"] == {"call": f"{tmp_path / 'mine.py'}:build", "word": "a form"}
    cfg.target, cfg.runs_dir = str(tmp_path / "state"), str(tmp_path / "runs")
    given = []
    harness(cfg).runners["script"](None, given.append, None, 0)   # each other key of the table is an argument of the function
    assert given == ["a form"]
    for table in ('[runners.script]\nword = "a form"\n', '[runners."a:b"]\ncall = "x:y"\n', '[runners.human]\ncall = "x:y"\n'):
        path.write_text(head + table)
        with pytest.raises(ValueError, match="Or it states call"):
            load(path)
