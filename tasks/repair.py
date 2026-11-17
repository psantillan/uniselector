"""Wake one runner for one broken object, with no main agent, and record what the runner did.

  python -m uniselector_tasks.repair --work DIRECTORY --out DIRECTORY --decision-model MODEL --url URL --model MODEL
      [--key-env NAME] [--body JSON] [--budget-tokens N] [--careless]

The object `paid` counts the paid orders of a feed. Its check names the condition that a
replacement must keep: it counts only the paid orders. The object `orders` counts each order, and
the variable of the plan reads it. First the feed gets rows, and the record says what the
binding of the runner cost while nothing failed. Then the column `status` gets the name `state`,
so the object shows an error, and the wake goes to the runner of the object. The runner makes one
request. The next look judges the replacement. The record says if the new version of the object
shows the right count, how its handle is made, and what the runner spent. A wake that reaches the
main agent ends the check: the runner gave no replacement that holds.

With `--careless`, the first runner of the object is a script of this file, and it is wrong: it
replaces the broken filter with a filter that cannot fail, so the object counts each order. The
check of the object must reject that replacement. Then the script leaves the list, and the model
runner gets the wake. The record has each look that judged a replacement.
"""
import argparse
import csv
import json
import random
import shutil
import time
from pathlib import Path

from uniselector.config import Config
from uniselector.run import Harness

ROWS = 200
# ASSUMED: the token budget of this check. A wake of a model runner is one request, and a look is some hundred tokens, so a run
# that works spends some thousand tokens. Only a fault of the harness reaches this limit.
BUDGET_TOKENS = 1_000_000
# The variable of the plan reads an object that the break does not change. A variable of the plan that states the check of the
# object `paid` again wakes the main agent at each break of that object (docs/DECISIONS.md, D52).
GOAL = "The object `orders` shows fewer than 1000 rows."
QUIET_SECONDS = 20   # ASSUMED: the time that the feed gets rows before the break. It gives the run some looks with nothing outside.


def careless(client):
    """A runner with no model, and a wrong one: a script that replaces the broken filter with a filter that cannot fail."""
    def repair(question, submit, spend, seconds):
        source = client.resolve(question["sources"][-1])
        submit({"handle": source.invoke("dataset.filter", {"where": [{"column": "amount", "op": "gt", "value": 0}]}).id,
                "operation": {"capability": "dataset.count"}})
    return repair


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--work", required=True, help="a directory for the runtime state and the feed")
    p.add_argument("--out", required=True, help="a directory for the record and the logs of the run")
    p.add_argument("--decision-model", required=True)
    p.add_argument("--url", required=True, help="the chat completions API of the runner")
    p.add_argument("--model", required=True)
    p.add_argument("--key-env", help="the environment variable with the API key of the runner")
    p.add_argument("--body", type=json.loads, help="more keys for each request of the runner, as JSON")
    p.add_argument("--budget-tokens", type=int, default=BUDGET_TOKENS, help="the token budget of the run")
    p.add_argument("--careless", action="store_true", help="the first runner is a script that gives a wrong replacement")
    a = p.parse_args()
    work = Path(a.work).resolve() / f"repair_{time.strftime('%Y%m%d_%H%M%S')}"
    work.mkdir(parents=True)
    rng = random.Random(3)
    header = ["id", "region", "status", "amount"]
    rows = [[i, rng.choice(["north", "south", "east"]), rng.choice(["paid", "paid", "pending"]), round(rng.uniform(5, 400), 2)] for i in range(ROWS)]
    truth = sum(row[2] == "paid" for row in rows)

    def write() -> None:
        with open(work / "orders.csv", "w", newline="") as f:
            csv.writer(f).writerows([header, *rows])

    write()
    runner = {"url": a.url, "model": a.model, **({"key_env": a.key_env} if a.key_env else {}), **({"body": a.body} if a.body else {})}
    # ASSUMED: 10 minutes are enough for one request of a local model and the looks of this check.
    cfg = Config(budget_tokens=a.budget_tokens, wall_seconds=600, target=str(work / "state"), runs_dir=str(work / "runs"),
                 decision_model=a.decision_model, runners={"runner": runner, "careless": {"call": "uniselector_tasks.repair:careless"}})
    h = Harness(cfg)
    source = h.clients["planner"].register("orders", adapter="duckdb", source={"path": str(work / "orders.csv")})
    paid = source.invoke("dataset.filter", {"where": [{"column": "status", "op": "eq", "value": "paid"}]})
    run = h.submit({"objects": [{"name": "paid", "handle": paid.id, "operation": {"capability": "dataset.count"},
                                 "variables": ["It shows a count of rows: a number that is more than 0.", "It counts only the paid orders."],
                                 "runners": [*(["careless"] if a.careless else []), "runner"]},
                                {"name": "orders", "handle": source.id, "operation": {"capability": "dataset.count"}}],
                    "variables": [GOAL]})["run"]

    def wait(test) -> dict:
        while True:
            summary = h.status(run)
            if test(summary) or summary["state"] != "running":
                return summary
            time.sleep(0.5)

    wait(lambda s: s["objects"][0].get("output"))
    end = time.time() + QUIET_SECONDS
    while time.time() < end:   # the feed is live, and nothing fails: the runner must stay silent
        time.sleep(QUIET_SECONDS / 4)
        rows.append([len(rows), "north", "paid", 20.0])
        truth += 1
        write()
    quiet = wait(lambda s: s["objects"][0].get("output") == json.dumps({"rows": truth}))
    header[2] = "state"
    write()
    t0 = time.time()
    # The look after the replacement judges it: `made_before` goes when the replacement held.
    summary = wait(lambda s: s["objects"][0]["written"] != "plan" and s["objects"][0].get("output") and "made_before" not in s["objects"][0])
    seconds = round(time.time() - t0, 1)
    logs = Path(summary["logs"])
    looks = [json.loads(line) for line in (logs / "decisions.jsonl").read_text().splitlines()]
    requests = [json.loads(line) for line in (logs / "runners.jsonl").read_text().splitlines()] if (logs / "runners.jsonl").exists() else []
    events = [json.loads(line) for line in (logs / "events.jsonl").read_text().splitlines()]
    record = {"runner": a.model, "decision_model": a.decision_model, "truth": {"rows": truth}, "state": summary["state"], "why": summary["why"],
              "object": summary["objects"][0], "right": summary["objects"][0].get("output") == json.dumps({"rows": truth}),
              "while_nothing_failed": {"looks": quiet["counts"]["looks"], "tokens": quiet["limits"]["tokens"], "runner_wakes": quiet["counts"]["runner_wakes"],
                                       "runner_tokens": quiet["objects"][0].get("runner_tokens", 0)},
              "seconds_from_the_break": seconds, "counts": summary["counts"], "limits": summary["limits"],
              "runner_requests": [{"runner": r["runner"], "reason": r["reason"], "tokens": r["tokens"], "ms": r["ms"]} for r in requests],
              "rebinds": [{k: e[k] for k in ("object", "from", "to", "why") if k in e} for e in events if e["event"] == "rebind"],
              "main_agent_wakes": sum(e["event"] == "woke" for e in events),
              "look_that_judged_the_replacement": [{"statements": look["statements"], "probs": look["probs"], "tokens": look["tokens"]}
                                                   for look in looks if any("is made by the same operations" in s for s in look["statements"])]}
    h.close()
    out = Path(a.out) / work.name
    shutil.copytree(work / "runs", out / "runs")
    (out / "record.json").write_text(json.dumps(record, indent=1))
    print(json.dumps(record, indent=1))
    print(out)


if __name__ == "__main__":
    main()
