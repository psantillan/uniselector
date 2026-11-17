"""Check the detector alone: states with statements of a known truth, and the probability of each.

  python -m uniselector_tasks.probe --decision-model MODEL --out FILE

Each case is a state in the form of a run, and statements that are true (1) or not true (0) in
that state. The check makes one look for each case and writes each probability to FILE. It needs
the key of the Decisions API in the environment variable OPENROUTER_API_KEY. It uses no runtime.
"""
import argparse
import json
import os
import sys

from uniselector.config import Config
from uniselector.decide import KEEPS, NO_ERROR, Decider, judge
from uniselector.state import cut

LIMITS = {"elapsed_s": 40.0, "wall_seconds": 1800, "tokens": 3000, "budget_tokens": 400000}
ROWS = "The object `rows` shows fewer than 600 rows."
RECENT = "The output of the object `latest` changed in the last 10 seconds."
STATUSES = "Each status in the object `statuses` is one of: paid, pending, refunded."
COLD = "The newest temperature is below 26."
RUNS = "The script of the object `job` has not finished."
LIVE = "About the object `highest`: Its operation ran in the last 15 seconds."
# The check of an object, in the form that a look gives it.
THREE = "About the object `totals`: It shows one row for each of the regions north, south and east, with a number in total."
# A form of an output, and its check in the form that a look gives it.
KNOWN = "The table of the object `totals` has only the statuses paid, pending and refunded."
NAMES = "About the form `summary` of the object `totals`: The summary names one status or more."
# The check of an object with runners names each condition that a replacement must keep. The look reads how the handle is made.
PAID = "About the object `totals`: It sums only the paid orders."
REGION = "About the object `totals`: It has one row for each region."
SAME = f"About the object `totals`: {KEEPS}"   # the sentence that a look adds for a replacement by a runner
CHEAP = "About the object `totals`: The runners of this object spent less than 20000 tokens."
FILTER = 'dataset.filter {"where": [{"column": "%s", "op": "eq", "value": "%s"}]}'
NO_COLUMN = {"code": "ADAPTER_ERROR", "message": 'BinderException: Binder Error: Referenced column "amount" not found in FROM clause!\n'
                                                 'Candidate bindings: "amount_usd"'}


def obj(name: str, capability: str, input: dict | None = None, **shows) -> dict:
    return {"name": name, "handle": "hdl_x", "type": "computation", "operation": {"capability": capability, **({"input": input} if input else {})},
            "written": "plan", **shows}


def state(*objects: dict, **limits) -> dict:
    return {"objects": list(objects), "limits": {**LIMITS, **limits}}


def cases() -> list:
    rows = obj("rows", "dataset.count", output='{"rows": 320}', runs=7, changed_s=1.2)
    totals = obj("totals", "dataset.head", {"limit": 10}, runs=7, changed_s=1.2,
                 output='{"columns": ["region", "total"], "rows": [["north", 5120.5], ["south", 4411.0], ["east", 3899.25]]}')
    statuses = '{"columns": ["status", "n"], "rows": [["paid", 150], ["pending", 80], ["refunded", 40]%s]}'

    def room(temperature: float) -> dict:
        return state(obj("reading", "room.latest", runs=41, changed_s=0.2,
                         output=json.dumps({"seq": 41, "t_ms": 123000, "heater": True, "temperature": temperature})))

    def long(summary: str | None, behind: int = 0) -> dict:
        """A table that is longer than the bound of the state. A status of the region north is in the part that the state does not keep."""
        table = [[region, status, 1000.5 + 7 * i + j, 20 + i + j] for i, region in enumerate(("central", "east", "islands", "north", "south", "west"))
                 for j, status in enumerate(("paid", "pending", "refunded"))]
        return state(obj("totals", "dataset.head", {"n": 50}, runs=2, changes=1, changed_s=3.1,
                         output=cut({"columns": ["region", "status", "total", "n"], "rows": table}, 600),
                         forms={"summary": summary and {"value": summary, "changes_behind": behind}}))

    def made(*steps: str, before: tuple | None = None, **shows) -> dict:
        """The totals of the regions, on a handle that `steps` made. `before` has the steps of the version before a replacement."""
        shows.setdefault("output", '{"columns": ["region", "total", "n"], "rows": [["north", 21515.21, 109], ["south", 19004.1, 98], ["east", 17650.0, 93]]}')
        return state(obj("totals", "dataset.head", {"n": 50}, runs=1, changed_s=0.4, made=" → ".join(steps),
                         **({"made_before": " → ".join(before)} if before else {}), **shows))

    def total(column: str, by: str = "region", count: bool = True) -> str:
        metrics = [{"fn": "sum", "column": column, "as": "total"}, *([{"fn": "count", "as": "n"}] if count else [])]
        return f"dataset.aggregate {json.dumps({'group_by': [by], 'metrics': metrics})}"

    paid, was = FILTER % ("status", "paid"), (FILTER % ("status", "paid"), total("amount"))
    return [
        ("each object reads", state(rows, totals), {NO_ERROR: 1, ROWS: 1}),
        ("a schema break", state(rows, obj("totals", "dataset.head", {"limit": 10}, error=NO_COLUMN, runs=8, changed_s=0.3)), {NO_ERROR: 0, ROWS: 1}),
        ("the goal is met", state(obj("rows", "dataset.count", output='{"rows": 600}', runs=21, changed_s=0.4), totals), {NO_ERROR: 1, ROWS: 0}),
        ("a silent feed", state(obj("latest", "dataset.count", output='{"rows": 11}', runs=11, changed_s=16.4)), {NO_ERROR: 1, RECENT: 0}),
        ("a live feed", state(obj("latest", "dataset.count", output='{"rows": 11}', runs=11, changed_s=1.9)), {NO_ERROR: 1, RECENT: 1}),
        ("a new status", state(obj("statuses", "dataset.head", {"limit": 20}, output=statuses % ', ["settled", 12]', runs=9, changed_s=0.5)),
         {NO_ERROR: 1, STATUSES: 0}),
        ("the known statuses", state(obj("statuses", "dataset.head", {"limit": 20}, output=statuses % "", runs=9, changed_s=0.5)),
         {NO_ERROR: 1, STATUSES: 1}),
        ("room at 21.4", room(21.4), {NO_ERROR: 1, COLD: 1}),
        ("room at 25.9", room(25.9), {NO_ERROR: 1, COLD: 1}),
        ("room at 26.1", room(26.1), {NO_ERROR: 1, COLD: 0}),
        ("a job runs", state(obj("job", "script.run", running_s=64.0, runs=0, changed_s=64.0)), {NO_ERROR: 1, RUNS: 1}),
        ("a job ended", state(obj("job", "script.run", runs=1, changed_s=0.1,
                                  output='{"exit_code": 0, "seconds": 150.2, "output": "round 30\\nchecksum 9f2c11aa03b4d7e1\\n"}')),
         {NO_ERROR: 1, RUNS: 0}),
        ("a quiet object whose source is live", state(obj("highest", "dataset.aggregate", output='{"max": 28.9}', ran_s=2.1, runs=14, changes=3,
                                                      changed_s=31.4)), {NO_ERROR: 1, LIVE: 1}),
        ("a quiet object whose source stopped", state(obj("highest", "dataset.aggregate", output='{"max": 28.9}', ran_s=31.4, runs=14, changes=3,
                                                      changed_s=47.0)), {NO_ERROR: 1, LIVE: 0}),
        ("the check of an object holds", state(rows, totals), {NO_ERROR: 1, THREE: 1}),
        ("the check of an object does not hold",
         state(rows, obj("totals", "dataset.head", {"limit": 10}, runs=7, changed_s=1.2, output='{"columns": ["region", "total"], "rows": [["north", null]]}')),
         {NO_ERROR: 1, THREE: 0}),
        ("a form of a long output", long("The table has three statuses: paid, pending, and refunded."), {NO_ERROR: 1, KNOWN: 1, NAMES: 1}),
        ("a form shows a value of the part that the state does not keep",
         long("The table has four statuses: disputed, paid, pending, and refunded. Only the region north has disputed orders."),
         {NO_ERROR: 1, KNOWN: 0, NAMES: 1}),
        ("a form that does not pass its check", long("I could not read the table."), {NO_ERROR: 1, NAMES: 0}),
        ("the handle has the filter that the check names", made(paid, total("amount_usd")), {NO_ERROR: 1, PAID: 1, REGION: 1}),
        ("the handle has no filter", made(total("amount_usd")), {PAID: 0, REGION: 1}),
        ("the handle has another filter", made(FILTER % ("status", "pending"), total("amount_usd")), {PAID: 0}),
        ("a replacement changes only the name of a column", made(paid, total("amount_usd"), before=was), {PAID: 1, SAME: 1}),
        ("a replacement changes the names of two columns", made(FILTER % ("state", "paid"), total("amount_usd"), before=was), {PAID: 1, SAME: 1}),
        ("a replacement drops the filter", made(total("amount_usd"), before=was), {PAID: 0, SAME: 0}),
        ("a replacement has another filter", made('dataset.filter {"where": [{"column": "amount_usd", "op": "gt", "value": 0}]}', total("amount_usd"),
                                                  before=was), {PAID: 0, SAME: 0}),
        # The state of a look of a run (docs/FAILURES.md, F72): the replacement of one filter is another filter.
        ("a run: the replacement of a filter is another filter",
         state({**obj("paid", "dataset.count", output='{"rows": 204}', ran_s=0.1, runs=1, changes=0, changed_s=0.0, written="wake_1",
                      made='dataset.filter {"where": [{"column": "amount", "op": "gt", "value": 0}]}', made_before=FILTER % ("status", "paid")),
                 "type": "dataset"},
               {**obj("orders", "dataset.count", output='{"rows": 204}', ran_s=2.1, runs=6, changes=4, changed_s=4.0), "type": "dataset"},
               elapsed_s=26.1, wall_seconds=600, tokens=3944, budget_tokens=1000000),
         {NO_ERROR: 1, "The object `orders` shows fewer than 1000 rows.": 1,
          "About the object `paid`: It shows a count of rows: a number that is more than 0.": 1,
          "About the object `paid`: It counts only the paid orders.": 0, f"About the object `paid`: {KEEPS}": 0}),
        ("a replacement has another group", made(paid, total("amount_usd", "status"), before=was,
                                                 output='{"columns": ["status", "total", "n"], "rows": [["paid", 58169.31, 300]]}'), {REGION: 0, SAME: 0}),
        ("a replacement drops a metric", made(paid, total("amount_usd", count=False), before=was,
                                              output='{"columns": ["region", "total"], "rows": [["north", 21515.21], ["south", 19004.1], ["east", 17650.0]]}'),
         {SAME: 0}),
        ("the runners spent little", made(paid, total("amount_usd"), runner_tokens=3100), {CHEAP: 1}),
        ("the runners spent much", made(paid, total("amount_usd"), runner_tokens=1090476), {CHEAP: 0}),
    ]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--decision-model", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    cfg = Config(budget_tokens=0, wall_seconds=0, decision_model=a.decision_model)
    decide = Decider(cfg.decision_url, cfg.decision_model, os.environ[cfg.decision_key_env])
    record = {"decision_model": a.decision_model, "inside": cfg.inside, "cases": [], "statements": 0, "wrong": 0, "tokens": 0, "cost": 0.0}
    for name, view, truth in cases():
        d = decide(view, list(truth))
        outside = judge(d, cfg.inside)
        answers = [{"statement": s, "true": bool(t), "probability": round(p, 4), "right": (i not in outside) == bool(t)}
                   for i, ((s, t), p) in enumerate(zip(truth.items(), d.probs))]
        record["cases"].append({"case": name, "state": view, "answers": answers, "tokens": d.tokens})
        record["statements"] += len(answers)
        record["wrong"] += sum(not x["right"] for x in answers)
        record["tokens"] += d.tokens
        record["cost"] = round(record["cost"] + d.cost, 6)
    with open(a.out, "w") as f:
        json.dump(record, f, indent=1)
    true = [x["probability"] for c in record["cases"] for x in c["answers"] if x["true"]]
    false = [x["probability"] for c in record["cases"] for x in c["answers"] if not x["true"]]
    print(f"{record['statements']} statements, {record['wrong']} wrong. true: {min(true)} to {max(true)}. not true: {min(false)} to {max(false)}. "
          f"{record['tokens']} tokens, ${record['cost']}.")
    sys.exit(1 if record["wrong"] else 0)


if __name__ == "__main__":
    main()
