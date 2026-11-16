"""The small parts: the detector's call, the plan, the hook, and the tool surface."""
import http.server
import io
import json
import sys
import threading
import urllib.error

import pytest

from conftest import finish, harness
from uniselector import mcp, plan
from uniselector.decide import NO_ERROR, Busy, Decider, judge
from uniselector.config import load
from uniselector.hooks import pending
from uniselector.runner import ModelRunner
from uniselector.state import cut

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
                          "output": '{"rows": 21}', "ran_s": 0.4, "runs": 3, "changed_s": 4.0},
                         {"name": "totals", "handle": "hdl_2", "type": "computation", "operation": {"capability": "dataset.head", "input": {"limit": 5}},
                          "written": "wake_1", "error": {"code": "ADAPTER_ERROR", "message": "no column amount"}, "runs": 2, "changed_s": 0.1},
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
                 "ERROR ADAPTER_ERROR: no column amount", "no output yet", "the operation runs now, for 12.5 s", "runs: 3, the last one started 0.4 s ago. last change of the output: 4.0 s ago.", "time: 14.0 s of 60 s. tokens: 500 of 1000."):
        assert text in body["state"], text
    assert "The count is less than 200." not in body["state"]
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


def test_a_model_runner_reads_with_the_tools_and_writes_one_repair(cfg, events):
    """A server of the test stands in for a chat completions API. The tools go to a real DataHandles runtime."""
    h = harness(cfg)
    source = h.clients["planner"].register("events", adapter="duckdb", source={"path": str(events)})
    h.gate.delegate(source.id)

    def says(tokens, *calls, text=None):
        message = {"role": "assistant", "content": text, "tool_calls": [
            {"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}} for i, (name, arguments) in enumerate(calls)]}
        return {"choices": [{"message": message}], "usage": {"total_tokens": tokens, "cost": tokens / 1e6}}

    repair = {"handle": source.id, "operation": {"capability": "dataset.count"}}
    server, seen = stand_in([
        says(700, ("handle_describe", {"handle": source.id}), ("handle_invoke", {"handle": source.id, "capability": "room.heater", "input": {}})),
        says(200, ("repair", repair)),
        says(300, ("repair", repair)),
        says(100, text="The column has a new name."),
        says(50, ("give_up", {"reason": "the source is gone"})),
        says(40, ("to_main", {"reason": "the object works"})),
        says(600, ("handle_find", {})), says(600, ("handle_find", {})), says(600, ("handle_find", {}))])
    url, faults, given = f"http://127.0.0.1:{server.server_port}/v1/chat/completions", ["the operation is wrong", None], []

    def submit(value):
        given.append(value)
        return faults.pop(0)

    def wake(runner, tokens=100_000, seconds=30.0):
        """One wake in a run that has `tokens` tokens left. Return how it ended, and the spend that the runner reported."""
        spent = {"tokens": 0, "cost": 0.0}

        def spend(n, cost=0.0):
            spent["tokens"], spent["cost"] = spent["tokens"] + n, spent["cost"] + cost
            return max(tokens - spent["tokens"], 0)

        return runner({"object": {"name": "purchases"}, "sources": [source.id]}, submit, spend, seconds), spent

    try:
        runner = ModelRunner(url, "some/chat-model", "the-key", 5000, h.clients["orchestrator"], {"chat_template_kwargs": {"enable_thinking": False}})
        waits, chat = [], runner.chat
        runner.chat = lambda messages, timeout: (waits.append(timeout), chat(messages, timeout))[1]
        out, spent = wake(runner)
        stopped, _ = wake(runner)   # an answer with no call of a tool ends the turn
        gave_up, little = wake(runner)
        handed, _ = wake(runner)
        capped, most = wake(ModelRunner(url, "some/chat-model", None, 1000, h.clients["orchestrator"]))   # the limit of one wake
        poor, all_of_it = wake(runner, tokens=600)   # the run has less left than one wake can take
        ended, nothing = wake(runner, tokens=0)      # the run has nothing left
        late, _ = wake(runner, seconds=0.0)          # the run has no time left
    finally:
        server.shutdown()
        server.server_close()
    dead, _ = wake(runner)
    assert (spent["tokens"], round(spent["cost"], 6), out.reason, given) == (1200, 0.0012, "repair", [repair, repair])
    assert [c["tool"] for c in out.calls] == ["handle_describe", "handle_invoke", "repair", "repair"]
    # It works as the orchestrator's principal: it reads, and the runtime refuses what that principal does not hold.
    assert "dataset.count" in json.dumps(out.calls[0]["result"]) and "error" in out.calls[1]["result"] and out.calls[2]["result"] == {"error": "the operation is wrong"}
    headers, body = seen[0]
    assert headers["Authorization"] == "Bearer the-key" and body["model"] == "some/chat-model"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}   # the keys that the server of the model needs
    assert [t["function"]["name"] for t in body["tools"]] == ["handle_find", "handle_describe", "handle_invoke", "repair", "give_up", "to_main"]
    assert source.id in body["messages"][1]["content"]   # the question
    # Each result goes back to the model as the reply to its call. The fault of a repair goes back too.
    said = seen[2][1]["messages"]
    assert [(m["role"], m.get("tool_call_id")) for m in said[2:]] == [
        ("assistant", None), ("tool", "call_0"), ("tool", "call_1"), ("assistant", None), ("tool", "call_0")]
    assert "the operation is wrong" in said[-1]["content"] and "dataset.count" in said[3]["content"]
    assert stopped.reason == "the runner stopped with no repair: The column has a new name." and stopped.calls == [{"tool": None, "text": "The column has a new name."}]
    assert (gave_up.reason, gave_up.main, little["tokens"]) == ("the runner gave up: the source is gone", False, 50)
    assert (handed.reason, handed.main) == ("the runner gave the wake to the main agent: the object works", True)
    assert "Authorization" not in seen[6][0] and "chat_template_kwargs" not in seen[6][1]
    assert "reached a limit" in capped.reason and "it spent 1200 tokens, one wake can spend 1000" in capped.reason and most["tokens"] == 1200
    assert "the run has 0 tokens" in poor.reason and all_of_it["tokens"] == 600
    assert "reached a limit" in ended.reason and "reached a limit" in late.reason and nothing["tokens"] == 0 and len(seen) == 9   # no request
    assert "the model of the runner failed" in dead.reason
    assert all(29 < wait <= 30 for wait in waits)   # a request can take the time that the run has left


def test_the_configuration_names_each_model_runner(tmp_path, monkeypatch):
    path, head = tmp_path / "uniselector.toml", "[limits]\nbudget_tokens = 1000\nwall_seconds = 60\n"
    path.write_text(head + '[runners.local]\nurl = "http://127.0.0.1:1/v1/chat/completions"\nmodel = "small"\nwake_tokens = 20000\n'
                           '[runners.top]\nurl = "https://example.org/v1/chat/completions"\nmodel = "large"\nwake_tokens = 60000\nkey_env = "TOP_KEY"\n'
                           'body = { reasoning = { enabled = false } }\n')
    cfg = load(path)
    assert list(cfg.runners) == ["local", "top"] and cfg.runners["top"]["key_env"] == "TOP_KEY" and cfg.budget_tokens == 1000
    assert cfg.runners["top"]["body"] == {"reasoning": {"enabled": False}} and "body" not in cfg.runners["local"]
    # The harness builds one runner for each table. The key comes from the environment.
    monkeypatch.setenv("TOP_KEY", "the-top-key")
    cfg.target, cfg.runs_dir = str(tmp_path / "state"), str(tmp_path / "runs")
    built = harness(cfg).runners
    assert (built["top"].key, built["top"].body, built["top"].wake_tokens) == ("the-top-key", {"reasoning": {"enabled": False}}, 60000)
    assert (built["local"].key, built["local"].body, built["local"].model) == (None, {}, "small")
    for table in ('[runners.main]\nurl = "u"\nmodel = "m"\nwake_tokens = 1\n', '[runners.local]\nurl = "u"\nmodel = "m"\n',
                  '[runners.local]\nurl = "u"\nmodel = "m"\nwake_tokens = 1\nkey = "a literal key"\n'):
        path.write_text(head + table)
        with pytest.raises(ValueError, match="must state url, model and wake_tokens"):
            load(path)
