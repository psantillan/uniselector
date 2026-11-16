"""Wake one runner for one broken object, with no main agent, and record what the runner did.

  python -m uniselector_tasks.repair --work DIRECTORY --out DIRECTORY --decision-model MODEL --url URL --model MODEL --wake-tokens N
      [--key-env NAME] [--body JSON]

The object counts the paid orders of a feed. The column `status` gets the name `state`, so the
object shows an error, and the wake goes to the runner of the object. The record says if the new
version of the object shows the right count. A wake that reaches the main agent ends the check:
the runner gave no repair.
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


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--work", required=True, help="a directory for the runtime state and the feed")
    p.add_argument("--out", required=True, help="a directory for the record and the logs of the run")
    p.add_argument("--decision-model", required=True)
    p.add_argument("--url", required=True, help="the chat completions API of the runner")
    p.add_argument("--model", required=True)
    p.add_argument("--wake-tokens", type=int, required=True)
    p.add_argument("--key-env", help="the environment variable with the API key of the runner")
    p.add_argument("--body", type=json.loads, help="more keys for each request of the runner, as JSON")
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
    runner = {"url": a.url, "model": a.model, "wake_tokens": a.wake_tokens, **({"key_env": a.key_env} if a.key_env else {}),
              **({"body": a.body} if a.body else {})}
    # ASSUMED: the limits of this check. One wake of the runner is the largest spend, and 10 minutes are enough for a local model.
    cfg = Config(budget_tokens=2 * a.wake_tokens, wall_seconds=600, target=str(work / "state"), runs_dir=str(work / "runs"),
                 decision_model=a.decision_model, runners={"runner": runner})
    h = Harness(cfg)
    source = h.clients["planner"].register("orders", adapter="duckdb", source={"path": str(work / "orders.csv")})
    paid = source.invoke("dataset.filter", {"where": [{"column": "status", "op": "eq", "value": "paid"}]})
    run = h.submit({"objects": [{"name": "paid", "handle": paid.id, "operation": {"capability": "dataset.count"},
                                 "variables": ["It shows the count of the paid orders: a number that is more than 0."], "runners": ["runner"]}],
                    "variables": ["The object `paid` shows a count of rows."]})["run"]

    def wait(test) -> dict:
        while True:
            summary = h.status(run)
            if test(summary) or summary["state"] != "running":
                return summary
            time.sleep(0.5)

    wait(lambda s: s["objects"][0].get("output"))
    header[2] = "state"
    write()
    t0 = time.time()
    summary = wait(lambda s: s["last_wake"] is not None and s["objects"][0].get("output"))
    record = {"runner": runner, "decision_model": a.decision_model, "truth": {"rows": truth}, "state": summary["state"], "why": summary["why"],
              "object": summary["objects"][0], "right": summary["objects"][0].get("output") == json.dumps({"rows": truth}),
              "seconds": round(time.time() - t0, 1), "counts": summary["counts"], "limits": summary["limits"]}
    h.close()
    out = Path(a.out) / work.name
    shutil.copytree(work / "runs", out / "runs")
    (out / "record.json").write_text(json.dumps(record, indent=1))
    print(json.dumps(record, indent=1))
    print(out)


if __name__ == "__main__":
    main()
