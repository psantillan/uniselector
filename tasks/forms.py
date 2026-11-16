"""Watch one long table with and with no forms of its output, with no main agent, and record what the decision model found.

  python -m uniselector_tasks.forms --work DIRECTORY --out DIRECTORY --decision-model MODEL --url URL --model MODEL --wake-tokens N
      [--key-env NAME] [--body JSON]

The object shows the total of the orders for each region and each status. Its output is longer
than the bound of the state, so the state keeps the first part and the last part. Then orders with
a new status come to a region in the middle of the table. Two runs watch the same handle, with the
same variable: "the table has only the statuses paid, pending and refunded."

- The run `plain` has the object with no forms.
- The run `formed` has two forms of the object. The model runner makes the form `summary`. A
  function of this file makes the form `embedding`: it writes one vector for each row to a file,
  and it gives the run a handle to that file.

A wake ends the watch of a run: it has no main agent. The record says which run woke.

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
GOAL = "The table of the object `totals` has only the statuses paid, pending and refunded."
# The check of a form states only what the form shows. The decision model sees a part of a long output, so it cannot judge a
# sentence that compares the form with the full output (docs/DECISIONS.md, D54).
SUMMARY = "The summary names one status or more."
# ASSUMED: the time that each run can watch after the new status. A repair by the local model took 18 s to 40 s in the task runs
# before this one, and a summary is less work. One look takes about 1 s.
WATCH_SECONDS = 180
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
    p.add_argument("--wake-tokens", type=int, required=True)
    p.add_argument("--key-env", help="the environment variable with the API key of the runner")
    p.add_argument("--body", type=json.loads, help="more keys for each request of the runner, as JSON")
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
    model = {"url": a.url, "model": a.model, "wake_tokens": a.wake_tokens, **({"key_env": a.key_env} if a.key_env else {}),
             **({"body": a.body} if a.body else {})}
    # ASSUMED: the limits of this check. Each new output starts the runner of the summary one time, and the check has two outputs.
    cfg = Config(budget_tokens=4 * a.wake_tokens, wall_seconds=600, target=str(work / "state"), runs_dir=str(work / "runs"),
                 decision_model=a.decision_model,
                 runners={"local": model, "embedder": {"call": "uniselector_tasks.forms:embedder", "directory": str(work)}})
    h = Harness(cfg)
    orders = h.clients["planner"].register("orders", adapter="duckdb", source={"path": str(work / "orders.csv")})
    totals = orders.invoke("dataset.aggregate", {"group_by": ["region", "status"], "metrics": [
        {"fn": "sum", "column": "amount", "as": "total"}, {"fn": "count", "as": "n"}]})
    totals = totals.invoke("dataset.sort", {"by": [{"column": "region"}, {"column": "status"}]})
    thing = {"name": "totals", "handle": totals.id, "operation": {"capability": "dataset.head", "input": {"n": 50}}}
    forms = {"summary": {"runners": ["local"], "variables": [SUMMARY]}, "embedding": {"runners": ["embedder"]}}
    runs = {"plain": h.submit({"objects": [thing], "variables": [GOAL]})["run"],
            "formed": h.submit({"objects": [{**thing, "forms": forms}], "variables": [GOAL]})["run"]}

    def wait(test, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and not test():
            time.sleep(0.5)

    def shown(name: str) -> dict:
        return h.status(runs[name])["objects"][0]

    wait(lambda: h.status(runs["formed"])["state"] != "running"
         or shown("plain").get("output") and all(shown("formed").get("forms", {}).get(form) for form in forms), WATCH_SECONDS)
    before = {name: h.status(run) for name, run in runs.items()}
    rows += [[400 + i, "north", NEW, 120.0] for i in range(5)]
    write()
    t0 = time.time()
    wait(lambda: h.status(runs["formed"])["state"] != "running", WATCH_SECONDS)
    seconds = round(time.time() - t0, 1)
    wait(lambda: False, 10)   # the run `plain` gets its looks at the new output
    after = {name: h.status(run) for name, run in runs.items()}
    full = totals.invoke("dataset.head", {"n": 50})["rows"]
    vectors = json.loads((before["formed"]["objects"][0]["forms"]["embedding"] or {"value": "{}"})["value"]).get("handle")
    record = {
        "runner": a.model, "decision_model": a.decision_model, "table_rows": len(full), "table_characters": len(json.dumps({"rows": full})),
        "new_status": NEW,
        "runs": {name: {
            "state": s["state"], "why": s["why"], "pending": s["pending"], "counts": s["counts"], "limits": s["limits"],
            "looks_after_the_new_status": s["counts"]["looks"] - before[name]["counts"]["looks"],
            "new_status_in_the_output_of_the_state": NEW in s["objects"][0].get("output", ""),
            "forms_before": before[name]["objects"][0].get("forms"), "forms_after": s["objects"][0].get("forms"),
        } for name, s in after.items()},
        "seconds_to_the_wake_of_formed": seconds if after["formed"]["state"] == "woke" else None,
        "vectors_before_the_new_status": h.clients["planner"].resolve(vectors).invoke("dataset.count") if vectors else None,
    }
    h.close()
    out = Path(a.out) / work.name
    shutil.copytree(work / "runs", out / "runs")
    (out / "record.json").write_text(json.dumps(record, indent=1))
    print(json.dumps(record, indent=1))
    print(out)


if __name__ == "__main__":
    main()
