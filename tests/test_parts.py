"""The small parts: the abstain rule, the plan, the candidate pool, the hook, and the tool surface."""
import http.server
import io
import json
import sys
import threading

import pytest

from conftest import finish, harness
from uniselector import mcp, plan
from uniselector.decide import Decider, Distribution, judge, render
from uniselector.hooks import pending
from uniselector.pool import leaves, literals


def test_a_decision_abstains_when_the_top_is_low_or_the_second_is_close_or_the_model_refuses():
    rule = dict(top=0.5, ratio=0.5)
    assert judge(Distribution([0.1, 0.8, 0.1], 0, 0.0), **rule) == 1
    assert judge(Distribution([0.45, 0.3, 0.25], 0, 0.0), **rule) is None    # the top option is low
    assert judge(Distribution([0.6, 0.4, 0.0], 0, 0.0), **rule) is None      # the second option is close
    assert judge(Distribution([0.0, 0.0, 0.0], 0, 0.0), **rule) is None      # the model refused the question
    assert judge(Distribution([1.0], 0, 0.0), **rule) == 0


def test_one_decision_is_one_choice_question_to_the_decisions_api():
    """A server of the test stands in for the Decisions API. It keeps the request, and it gives the next reply."""
    seen, replies = [], [
        {"answers": {"decision": {"type": "choice", "choice": "2", "probabilities": {"3": 0.1, "2": 0.7, "1": 0.2}, "confidence": 0.55}},
         "usage": {"input_tokens": 321, "output_tokens": 1, "cost": 0.0000064}},
        {"answers": {"decision": {"type": "refusal"}}, "usage": {"input_tokens": 300, "output_tokens": 0, "cost": 0.0}}]

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
    state = {"plan": {"intents": ["count the rows"]}, "intent": 0, "handles": [], "results": [], "decision": {"text": "Choose the next operation."},
             "limits": {"elapsed_s": 1.0, "wall_seconds": 60, "tokens": 0, "budget_tokens": 1000, "intents_done": 0, "intents": 1, "spent_share": 0.0},
             "stall": {"seconds": 0.0, "looks": 0}}
    try:
        decide = Decider(f"http://127.0.0.1:{server.server_port}/decisions", "some/decision-model", "the-key")
        d = decide(state, ["first", "second", "third"])
        refused = decide(state, ["first", "second"])
    finally:
        server.shutdown()
    headers, body = seen[0]
    assert headers["Authorization"] == "Bearer the-key" and body["model"] == "some/decision-model"
    assert "1. [CURRENT] count the rows" in body["state"] and "Choose the next operation." not in body["state"]
    question, = body["questions"].values()
    assert question["type"] == "choice" and question["instructions"].endswith("Choose the next operation.")
    assert question["criteria"] == {"1": "first", "2": "second", "3": "third"}
    # The distribution is in the order of the options, not in the order of the reply.
    assert (d.probs, d.tokens, d.cost) == ([0.2, 0.7, 0.1], 321, 0.0000064) and judge(d, 0.5, 0.5) == 1
    assert refused.probs == [0.0, 0.0] and judge(refused, 0.5, 0.5) is None


def test_a_plan_is_intents_and_handles_and_nothing_more(cfg):
    good = {"intents": ["count the rows"], "handles": ["hdl_1"]}
    assert plan.check(good) is good
    for bad in ({"intents": [], "handles": ["hdl_1"]}, {"intents": ["x"]}, {**good, "operations": []}, {**good, "limits": {"rows": 5}}):
        with pytest.raises(ValueError):
            plan.check(bad)
    assert plan.limits(cfg, {**good, "limits": {"wall_seconds": 5}}) == (cfg.budget_tokens, 5)


def test_a_table_output_gives_each_cell_with_its_column_as_the_place():
    table = {"columns": ["country", "n"], "rows": [["US", 3], ["DE", 1]], "truncated": False}
    assert ("US", ("rows", "country")) in leaves(table) and (1, ("rows", "n")) in leaves(table)
    assert literals('keep "gold" customers over $100 in 2 regions') == ["gold", 100, 2]
    # A token that is not a plain word is a value: a path, a dotted name, a date.
    assert literals("Get the report at the path /reports/quarterly (not v1.2), from 2026-01-31.") == ["/reports/quarterly", "v1.2", "2026-01-31"]


def test_the_first_page_of_an_open_value_has_the_values_with_evidence(cfg, events):
    h = harness(cfg, ["h1 dataset.filter", '"event_type"', '"eq"', '"value": x}', '"purchase"', "stop", "step_done", "step_done"])
    source = h.clients["planner"].register("events", adapter="duckdb", source={"path": str(events)})
    finish(h, h.submit({"intents": ["keep only the purchase events", "count the rows"], "handles": [source.id]})["run"])
    column = next(options for text, options in h.decide.seen if "`where[0].column`" in text)
    # The column names come from the descriptor. A bare word of the intent ("purchase") is not on the first page.
    first = [o.split("  ")[0] for o in column[:-1]]
    assert {'"id"', '"customer_id"', '"event_type"', '"amount"'} <= set(first) and '"purchase"' not in first
    shown = next(o for o in column if o.startswith('"event_type"'))
    assert column[-1].startswith("none of these") and 'Values at event_type: "purchase"' in shown
    value = next(options for text, options in h.decide.seen if "Choose the value of `where[0].value`" in text)
    assert {o.split("  ")[0] for o in value[:-1]} == {'"purchase"', '"view"', '"cart"'}   # the values at the chosen column


def test_the_state_text_has_the_plan_the_handles_and_the_results_by_intent(cfg, events):
    h = harness(cfg, ["step_done", "step_done"])
    source = h.clients["planner"].register("events", adapter="duckdb", source={"path": str(events)})
    run = h.submit({"intents": ["count the rows", "stop"], "handles": [source.id]})["run"]
    finish(h, run)
    text = render({**h.runs[run].state.view("Choose."), "intent": 1})   # the state at the second intent
    assert text.index("PLAN") < text.index("HANDLES") < text.index("LIMITS") < text.index("STALL") and "Choose." not in text
    assert "h1: dataset events" in text
    # The results of the current intent are apart from the results of the earlier intents.
    earlier, current = text.index("RESULTS OF EARLIER INTENTS"), text.index("RESULTS OF THE CURRENT INTENT (2. stop)")
    assert earlier < text.index("- step 1: step_done") < current < text.index("- step 2: step_done") < text.index("LIMITS")


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
    h = harness(cfg, ["step_done"])
    assert [t["name"] for t in mcp.TOOLS] == ["plan_submit", "plan_status", "plan_answer", "plan_confirm"]
    assert mcp.dispatch(h, "plan_submit", {"plan": {"intents": []}})["error"]["code"] == "INVALID_INPUT"
    assert mcp.dispatch(h, "plan_status", {"run": "run_none"})["error"]["code"] == "INVALID_INPUT"
    assert mcp.dispatch(h, "plan_submit", {"plan": {"intents": ["x"], "handles": ["hdl_none"]}})["error"]["code"] == "HANDLE_NOT_FOUND"
    source = h.clients["planner"].register("events", adapter="duckdb", source={"path": str(events)})
    run = mcp.dispatch(h, "plan_submit", {"plan": {"intents": ["count the rows"], "handles": [source.id]}})["run"]
    assert finish(h, run)["state"] == "done"
    assert mcp.dispatch(h, "plan_confirm", {"run": run, "request": "req_1", "decision": "approve"})["error"]["code"] == "INVALID_INPUT"
