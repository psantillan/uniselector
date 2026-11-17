"""Watch one feed with and with no forms of an output, with no main agent, and record what the forms cost and what a wake gets.

  python -m uniselector_tasks.forms --work DIRECTORY --out DIRECTORY --decision-model MODEL --url URL --model MODEL
      [--key-env NAME] [--body JSON] [--budget-tokens N]

The object `totals` shows the total of the orders for each region and each status. Its output is
longer than the bound of the state. The object `statuses` shows the count of each status: a small
output that shows the essential variable, "the table has only the statuses paid, pending and
refunded." Two runs watch the same handles with the same variable.

- The run `plain` has the objects with no forms.
- The run `formed` has two forms of the object `totals`. The model runner makes the form
  `summary`. A function of this file makes the form `embedding`: it writes one vector for each row
  to a file, and it gives the run a handle to that file.

First the feed gets orders with the known statuses. The record says what each run spent: a form
must add nothing while each variable is inside its limits. Then orders with a new status come.
Each run wakes, and a wake ends the watch of a run: it has no main agent. The record says what the
wake of the run `formed` has, how long the forms held it, and what the runners spent.

The vectors come from a hash of the words of a row. No model makes them. This check is a check of
the binding. It is not a check of an embedding engine.
"""
import argparse
import csv
import hashlib
import json
import random
import shutil
import time
from pathlib import Path

from uniselector.config import Config
from uniselector.run import Harness

REGIONS = ["central", "east", "north", "south", "west", "islands"]
STATUSES = ["paid", "pending", "refunded"]
NEW = "disputed"
GOAL = "The table of the object `statuses` has only the statuses paid, pending and refunded."
# The check of a form states only what the form shows. The decision model sees a part of a long output, so it cannot judge a
# sentence that compares the form with the full output (docs/DECISIONS.md, D54).
SUMMARY = "The summary names one status or more."
# ASSUMED: the time that each run can watch after the new status. A summary by the local model took 4 s to 5 s in the check
# before this one (docs/evidence, forms). One look takes about 1 s.
WATCH_SECONDS = 180
QUIET_STEPS, STEP_SECONDS = 4, 5   # ASSUMED: the feed gets orders four times before the new status, so each run has some looks
# ASSUMED: the token budget of this check. A form by a model runner is one request, and a look is some hundred tokens. Only a
# fault of the harness reaches this limit.
BUDGET_TOKENS = 1_000_000
DIMENSIONS = 16   # ASSUMED: the size of one vector. The check reads only the number of the vectors.


def embedder(client, directory: str):
    """The runner of the form `embedding`: one vector for each row of the object, in a file, as a handle."""
    def embed(question, submit, spend, seconds):
        table = client.resolve(question["object"]["handle"])
        shown = table.invoke(question["object"]["operation"]["capability"], question["object"]["operation"].get("input", {}))
        rows = [dict(zip(shown["columns"], row)) for row in shown["rows"]]
        path = Path(directory) / f"vectors_{question['object']['changes']}.csv"
        with open(path, "w", newline="") as f:
            out = csv.writer(f)
            out.writerow(["region", "status", *[f"v{i}" for i in range(DIMENSIONS)]])
            for row in rows:
                vector = [0] * DIMENSIONS
                for word in (str(row["region"]), str(row["status"])):
                    vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIMENSIONS] += 1
                out.writerow([row["region"], row["status"], *vector])
        submit({"handle": client.register(path.stem, adapter="duckdb", source={"path": str(path)}).id})
    return embed


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--work", required=True, help="a directory for the runtime state and the feed")
    p.add_argument("--out", required=True, help="a directory for the record and the logs of the runs")
    p.add_argument("--decision-model", required=True)
    p.add_argument("--url", required=True, help="the chat completions API of the runner of the summary")
    p.add_argument("--model", required=True)
    p.add_argument("--key-env", help="the environment variable with the API key of the runner")
    p.add_argument("--body", type=json.loads, help="more keys for each request of the runner, as JSON")
    p.add_argument("--budget-tokens", type=int, default=BUDGET_TOKENS, help="the token budget of each run")
    a = p.parse_args()
    work = Path(a.work).resolve() / f"forms_{time.strftime('%Y%m%d_%H%M%S')}"
    work.mkdir(parents=True)
    rng = random.Random(5)
    rows = [[i, rng.choice(REGIONS), rng.choice(STATUSES), round(rng.uniform(5, 400), 2)] for i in range(400)]

    def write() -> None:
        with open(work / "orders.tmp", "w", newline="") as f:
            csv.writer(f).writerows([["id", "region", "status", "amount"], *rows])
        (work / "orders.tmp").replace(work / "orders.csv")   # a reader never gets a part of the file

    write()
    model = {"url": a.url, "model": a.model, **({"key_env": a.key_env} if a.key_env else {}), **({"body": a.body} if a.body else {})}
    # ASSUMED: 10 minutes are enough for the looks of this check and one request of a local model.
    cfg = Config(budget_tokens=a.budget_tokens, wall_seconds=600, target=str(work / "state"), runs_dir=str(work / "runs"),
                 decision_model=a.decision_model,
                 runners={"local": model, "embedder": {"call": "uniselector_tasks.forms:embedder", "directory": str(work)}})
    h = Harness(cfg)
    orders = h.clients["planner"].register("orders", adapter="duckdb", source={"path": str(work / "orders.csv")})
    totals = orders.invoke("dataset.aggregate", {"group_by": ["region", "status"], "metrics": [
        {"fn": "sum", "column": "amount", "as": "total"}, {"fn": "count", "as": "n"}]})
    totals = totals.invoke("dataset.sort", {"by": [{"column": "region"}, {"column": "status"}]})
    by_status = orders.invoke("dataset.aggregate", {"group_by": ["status"], "metrics": [{"fn": "count", "as": "n"}]})
    by_status = by_status.invoke("dataset.sort", {"by": [{"column": "status"}]})
    thing = {"name": "totals", "handle": totals.id, "operation": {"capability": "dataset.head", "input": {"n": 50}}}
    statuses = {"name": "statuses", "handle": by_status.id, "operation": {"capability": "dataset.head", "input": {"n": 20}}}
    forms = {"summary": {"runners": ["local"], "variables": [SUMMARY]}, "embedding": {"runners": ["embedder"]}}
    runs = {"plain": h.submit({"objects": [thing, statuses], "variables": [GOAL]})["run"],
            "formed": h.submit({"objects": [{**thing, "forms": forms}, statuses], "variables": [GOAL]})["run"]}

    def wait(test, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and not test():
            time.sleep(0.5)

    def looks(name: str) -> list:
        log = Path(h.status(runs[name])["logs"]) / "decisions.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    wait(lambda: all(o.get("output") for run in runs.values() for o in h.status(run)["objects"]), WATCH_SECONDS)
    for _ in range(QUIET_STEPS):   # the feed is live, and each variable is inside its limits
        rows += [[len(rows) + i, rng.choice(REGIONS), rng.choice(STATUSES), round(rng.uniform(5, 400), 2)] for i in range(5)]
        write()
        wait(lambda: False, STEP_SECONDS)
    before = {name: h.status(run) for name, run in runs.items()}
    quiet = {name: looks(name) for name in runs}
    rows += [[len(rows) + i, "north", NEW, 120.0] for i in range(5)]
    write()
    t0 = time.time()
    woke = {}
    while time.time() - t0 < WATCH_SECONDS and len(woke) < len(runs):
        for name, run in runs.items():
            if name not in woke and h.status(run)["state"] != "running":
                woke[name] = round(time.time() - t0, 1)
        time.sleep(0.2)
    after = {name: h.status(run) for name, run in runs.items()}
    full = totals.invoke("dataset.head", {"n": 50})["rows"]
    made = after["formed"]["objects"][0].get("forms") or {}
    vectors = json.loads((made.get("embedding") or {"value": "{}"})["value"]).get("handle")
    log = Path(after["formed"]["logs"]) / "runners.jsonl"
    requests = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    record = {
        "runner": a.model, "decision_model": a.decision_model, "table_rows": len(full), "table_characters": len(json.dumps({"rows": full})),
        "new_status": NEW,
        # What each run spent while nothing failed. The two runs make their looks at their own times, so the count of the looks
        # can differ by a look. The tokens of one look show what a form adds to it.
        "while_nothing_failed": {name: {
            "looks": len(quiet[name]), "tokens": before[name]["limits"]["tokens"],
            "tokens_of_one_look": sorted({look["tokens"] for look in quiet[name]}),
            "forms": before[name]["counts"]["forms"], "forms_in_the_state": before[name]["objects"][0].get("forms"),
        } for name in runs},
        "runs": {name: {
            "state": s["state"], "why": s["why"], "pending": s["pending"], "counts": s["counts"], "limits": s["limits"],
            "seconds_to_the_wake": woke.get(name), "forms_at_the_wake": s["objects"][0].get("forms"),
            "runner_tokens": s["objects"][0].get("runner_tokens", 0),
        } for name, s in after.items()},
        "form_requests": [{"form": r["form"], "runner": r["runner"], "reason": r["reason"], "tokens": r["tokens"], "ms": r["ms"]} for r in requests],
        "summary_names_the_region_of_the_new_status": "north" in ((made.get("summary") or {}).get("value") or "").lower(),
        "vectors_at_the_wake": h.clients["planner"].resolve(vectors).invoke("dataset.count") if vectors else None,
    }
    h.close()
    out = Path(a.out) / work.name
    shutil.copytree(work / "runs", out / "runs")
    (out / "record.json").write_text(json.dumps(record, indent=1))
    print(json.dumps(record, indent=1))
    print(out)


if __name__ == "__main__":
    main()
