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
from uniselector.decide import NO_ERROR, Decider, judge

LIMITS = {"elapsed_s": 40.0, "wall_seconds": 1800, "tokens": 3000, "budget_tokens": 400000}
ROWS = "The object `rows` shows fewer than 600 rows."
RECENT = "The output of the object `latest` changed in the last 10 seconds."
STATUSES = "Each status in the object `statuses` is one of: paid, pending, refunded."
COLD = "The newest temperature is below 26."
RUNS = "The script of the object `job` has not finished."
NO_COLUMN = {"code": "ADAPTER_ERROR", "message": 'BinderException: Binder Error: Referenced column "amount" not found in FROM clause!\n'
                                                 'Candidate bindings: "amount_usd"'}


def obj(name: str, capability: str, input: dict | None = None, **shows) -> dict:
    return {"name": name, "handle": "hdl_x", "type": "computation", "operation": {"capability": capability, **({"input": input} if input else {})},
            "written": "plan", **shows}


def state(*objects: dict) -> dict:
    return {"objects": list(objects), "limits": LIMITS}


def cases() -> list:
    rows = obj("rows", "dataset.count", output='{"rows": 320}', runs=7, changed_s=1.2)
    totals = obj("totals", "dataset.head", {"limit": 10}, runs=7, changed_s=1.2,
                 output='{"columns": ["region", "total"], "rows": [["north", 5120.5], ["south", 4411.0], ["east", 3899.25]]}')
    statuses = '{"columns": ["status", "n"], "rows": [["paid", 150], ["pending", 80], ["refunded", 40]%s]}'

    def room(temperature: float) -> dict:
        return state(obj("reading", "room.latest", runs=41, changed_s=0.2,
                         output=json.dumps({"seq": 41, "t_ms": 123000, "heater": True, "temperature": temperature})))

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
