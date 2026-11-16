"""The small parts: the detector's call, the plan, the hook, and the tool surface."""
import http.server
import io
import json
import sys
import threading

import pytest

from conftest import finish, harness
from uniselector import mcp, plan
from uniselector.decide import NO_ERROR, Decider, judge
from uniselector.hooks import pending
from uniselector.state import cut

OBJECT = {"name": "purchases", "handle": "hdl_1", "operation": {"capability": "dataset.count"}}


def test_one_look_is_one_call_to_the_decisions_api_with_one_question_for_each_variable():
    """A server of the test stands in for the Decisions API. It keeps the request, and it gives the next reply."""
    seen, replies = [], [
        {"answers": {"v2": {"type": "noul", "noul": 0.12}, "v1": {"type": "noul", "noul": 0.93}, "v3": {"type": "refusal"}},
         "usage": {"input_tokens": 321, "output_tokens": 0, "cost": 0.0000064}}]

    class Api(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            body = json.dumps(replies.pop(0)).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state = {"notes": "The feed gets new rows.",
             "objects": [{"name": "purchases", "handle": "hdl_1", "type": "computation", "operation": {"capability": "dataset.count"}, "written": "plan",
                          "output": '{"rows": 21}', "runs": 3, "changed_s": 4.0},
                         {"name": "totals", "handle": "hdl_2", "type": "computation", "operation": {"capability": "dataset.head", "input": {"limit": 5}},
                          "written": "wake_1", "error": {"code": "ADAPTER_ERROR", "message": "no column amount"}, "runs": 2, "changed_s": 0.1},
                         {"name": "job", "handle": "hdl_3", "type": "resource", "operation": {"capability": "script.run"}, "written": "plan",
                          "running_s": 12.5, "runs": 0, "changed_s": 12.5}],
             "limits": {"elapsed_s": 14.0, "wall_seconds": 60, "tokens": 500, "budget_tokens": 1000}}
    try:
        decide = Decider(f"http://127.0.0.1:{server.server_port}/decisions", "some/decision-model", "the-key")
        d = decide(state, [NO_ERROR, "The count is less than 200.", "The job did not finish."])
    finally:
        server.shutdown()
    headers, body = seen[0]
    assert headers["Authorization"] == "Bearer the-key" and body["model"] == "some/decision-model"
    # The decision model reads what each object shows. The statements are the questions, not a part of the state.
    for text in ("The feed gets new rows.", 'purchases (computation): dataset.count {}', 'output: {"rows": 21}', 'dataset.head {"limit": 5}',
                 "ERROR ADAPTER_ERROR: no column amount", "no output yet", "the operation runs now, for 12.5 s", "time: 14.0 s of 60 s. tokens: 500 of 1000."):
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
