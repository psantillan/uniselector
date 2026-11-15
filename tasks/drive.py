"""Run one long task with a real main agent, and record what the main agent's context held.

  python -m uniselector_tasks.drive TASK --work DIRECTORY --evidence DIRECTORY

The main agent is Claude Code in non-interactive mode. It gets a plain task, the DataHandles tools
and the four Uniselector tools. It writes the plan itself. The driver records the largest context
of the main agent, the tool calls, the runs, and whether the answer is right.

TASK is one of: pipeline, stream, stream-human, api, code, helper.
"""
import argparse
import csv
import hashlib
import json
import random
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .slow_api import REPORT
from .world import BIN, World

HERE = Path(__file__).resolve().parent
HOW = ("Do this work through Uniselector. Find the handle ids with handle_find. Then hand off a plan with plan_submit and follow the run "
       "with plan_status. Do not invoke operations on the handles yourself: you can only read a result that you need for the answer. "
       "Reply with the answer only.")
# ASSUMED: the hard limits of the task runs. The user decides the hard limits; these are the values of uniselector.example.toml.
BUDGET_TOKENS, WALL_SECONDS = 400_000, 1800
# ASSUMED: 40 minutes. The longest time that the driver gives the main agent for one task.
AGENT_SECONDS = 2400


def pipeline(world: World, a) -> dict:
    """A data pipeline: filter, join, aggregate, sort, limit."""
    rng = random.Random(7)
    countries = ["DE", "FR", "JP", "BR", "US", "IN", "MX", "KE"]
    customers = [(f"c{n:04d}", rng.choice(countries), rng.choice(["free", "pro", "team"])) for n in range(400)]
    events = [(n, rng.choice(customers)[0], rng.choice(["view", "view", "cart", "purchase", "refund"]), round(rng.uniform(5, 400), 2))
              for n in range(20_000)]
    for name, header, rows in (("customers", ["customer_id", "country", "segment"], customers),
                               ("events", ["id", "customer_id", "event_type", "amount"], events)):
        with open(world.dir / f"{name}.csv", "w", newline="") as f:
            csv.writer(f).writerows([header, *rows])
        world.planner.register(name, adapter="duckdb", source={"path": str(world.dir / f"{name}.csv")})
    country = {c[0]: c[1] for c in customers}
    revenue = {}
    for _, customer, kind, amount in events:
        if kind == "purchase":
            revenue[country[customer]] = revenue.get(country[customer], 0) + amount
    top = sorted(revenue.items(), key=lambda kv: -kv[1])[:3]
    return {"prompt": "Two datasets are registered in DataHandles: `events` and `customers`. Find the three countries with the highest total "
                      "purchase revenue: the sum of `amount` over the events whose `event_type` is `purchase`. Give each country and its total.",
            "truth": [[c, round(v, 2)] for c, v in top],
            # The answer names the three countries, and each total is right to the unit.
            "check": lambda text: all(c in text and str(int(v)) in text.replace(",", "") for c, v in top)}


def stream(world: World, a) -> dict:
    """A stream with a condition to watch for. The heater is an action: each switch needs a confirmation."""
    room = world.planner.register("room", adapter="room", source={"interval_ms": a.interval_ms})

    def check(text: str) -> bool:
        readings, seq = [], 0
        while True:
            part = room.invoke("room.read", {"from_seq": seq, "count": 50})["readings"]
            readings += part
            seq += len(part)
            if len(part) < 50:
                break
        on = [r["seq"] for r in readings if r["heater"]]
        if not on or readings[-1]["heater"]:
            return False
        last = next(r for r in readings if r["seq"] == on[-1])   # the last reading with the heater on
        world.facts = {"heater_on_seq": on[0], "heater_off_after_seq": on[-1], "temperature_then": last["temperature"]}
        return last["temperature"] >= a.temperature

    return {"prompt": f"A room is registered in DataHandles as `room`. Heat it: switch the heater on, wait until the temperature is "
                      f"{a.temperature} or more, then switch the heater off. Give the temperature of the last reading that you saw.",
            "truth": f"the heater goes on, then off after a reading of {a.temperature} or more", "check": check}


def api(world: World, a) -> dict:
    """A slow API: one response takes minutes."""
    from .world import free_port
    port = free_port()
    world.start([sys.executable, "-m", "uniselector_tasks.slow_api", "--port", str(port), "--seconds", str(a.seconds)])
    world.planner.register("reports", adapter="api", source={"base_url": f"http://127.0.0.1:{port}", "timeout_ms": int(a.seconds * 1000) + 120_000})
    best = max(REPORT["regions"], key=lambda r: r["revenue"])
    return {"prompt": "A reports service is registered in DataHandles as `reports`. It is slow: one request can take minutes. Get the "
                      "report at the path /reports/quarterly. Give the region with the highest revenue, and that revenue.",
            "truth": [best["name"], best["revenue"]],
            "check": lambda text: best["name"] in text.lower() and str(best["revenue"]) in text.replace(",", "")}


def code(world: World, a) -> dict:
    """Code that takes time to run."""
    script = world.dir / "slow_job.py"
    rounds, pause = 30, a.seconds / 30
    script.write_text((HERE / "slow_job.py").read_text().replace("else 30", f"else {rounds}").replace("else 5.0", f"else {pause}"))
    world.planner.register("job", adapter="script", source={"path": str(script)})
    value = b"uniselector"
    for _ in range(rounds * 100_000):
        value = hashlib.sha256(value).digest()
    return {"prompt": "A script is registered in DataHandles as `job`. It takes minutes to run. Run it one time, with no arguments, and "
                      "give the checksum that it prints at the end.",
            "truth": value.hex()[:16], "check": lambda text: value.hex()[:16] in text}


def helper(world: World, a) -> dict:
    """A sub-agent: a helper model that reads a long document, part by part."""
    world.planner.register("reader", adapter="helper", source={"document": str(Path(a.document).resolve()), "url": world.model_url, "model": world.model})
    return {"prompt": "A helper agent is registered in DataHandles as `reader`. It reads one long architecture document, and one question "
                      "takes minutes. Ask it this question: 'Which database does the document name for the initial registry, and which "
                      "database does it name for later?' Give the two database names.",
            "truth": ["SQLite", "PostgreSQL"], "check": lambda text: "sqlite" in text.lower() and "postgres" in text.lower()}


TASKS = {"pipeline": pipeline, "stream": stream, "stream-human": stream, "api": api, "code": code, "helper": helper}


def stand_in_for_the_human(world: World, stop: threading.Event, done: list) -> None:
    """The driver takes the place of the human: it approves each request that the main agent passed on."""
    while not stop.wait(1):
        for events in world.runs.glob("*/events.jsonl"):
            lines = events.read_text().splitlines()
            last = json.loads(lines[-1]) if lines else {}
            pending = last.get("pending") or {}
            if pending.get("with") == "human" and (events.parent.name, pending["id"]) not in done and pending.get("command"):
                done.append((events.parent.name, pending["id"]))
                out = subprocess.run([str(BIN / "uniselector-confirm"), *pending["command"].split()[1:]], env=world.human_env(), capture_output=True, text=True)
                (world.dir / "human.log").open("a").write(f"$ {pending['command']}\n{out.stdout}{out.stderr}\n")


def commit() -> str:
    """The commit of the code that ran, with `+` when the working tree has changes."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=Path(__file__).parent, capture_output=True, text=True).stdout.strip()
    return git("rev-parse", "--short", "HEAD") + ("+" if git("status", "--porcelain", "--untracked-files=no") else "")


def measure(stream_file: Path) -> dict:
    """Read the event stream of the main agent: its context at each turn, and its tool calls."""
    contexts, calls, result, model = [], {}, {}, None
    for line in stream_file.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            model = event.get("model")
        if event.get("type") == "assistant":
            usage = event["message"].get("usage", {})
            contexts.append(usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0))
            for block in event["message"].get("content", []):
                if block.get("type") == "tool_use":
                    calls[block["name"].split("__")[-1]] = calls.get(block["name"].split("__")[-1], 0) + 1
        if event.get("type") == "result":
            result = event
    return {"agent_model": model, "context_first_turn": contexts[0] if contexts else None, "context_peak": max(contexts, default=None),
            "context_added": max(contexts) - contexts[0] if contexts else None, "tool_calls": calls,
            "turns": result.get("num_turns"), "agent_seconds": round(result.get("duration_ms", 0) / 1000, 1),
            "answer": result.get("result"), "agent_error": result.get("is_error")}


def main() -> None:
    p = argparse.ArgumentParser(description="Run one long task with a real main agent.")
    p.add_argument("task", choices=list(TASKS))
    p.add_argument("--work", required=True, help="a directory for the world of the run: the runtime state and the tokens")
    p.add_argument("--evidence", required=True, help="a directory for the record and the logs of the run")
    p.add_argument("--model-url", default="http://localhost:11434", help="the Ollama server of the decision model")
    p.add_argument("--model", required=True, help="the decision model")
    p.add_argument("--agent-model", default="sonnet", help="the model of the main agent")
    p.add_argument("--seconds", type=float, default=150, help="api, code: how long the slow part takes")
    p.add_argument("--interval-ms", type=int, default=3000, help="stream: the time between two readings")
    p.add_argument("--temperature", type=float, default=26, help="stream: the temperature to wait for")
    p.add_argument("--document", help="helper: the long document")
    a = p.parse_args()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    world = World(Path(a.work) / f"{a.task}_{stamp}", a.model_url, a.model, BUDGET_TOKENS, WALL_SECONDS,
                  human_only=["room.heater"] if a.task == "stream-human" else [])
    world.facts = {}
    stop, confirmed = threading.Event(), []
    try:
        task = TASKS[a.task](world, a)
        if a.task == "stream-human":
            threading.Thread(target=stand_in_for_the_human, args=(world, stop, confirmed), daemon=True).start()
        command = ["claude", "-p", f"{task['prompt']}\n\n{HOW}", "--model", a.agent_model, "--tools", "", "--mcp-config", str(world.dir / "mcp.json"),
                   "--strict-mcp-config", "--settings", str(world.dir / "settings.json"), "--allowedTools", "mcp__datahandles__*,mcp__uniselector__*",
                   "--output-format", "stream-json", "--verbose", "--include-hook-events", "--no-session-persistence"]
        t0 = time.time()
        with open(world.dir / "agent-stream.jsonl", "w") as out:
            try:
                subprocess.run(command, cwd=world.dir / "agent", stdout=out, stderr=subprocess.STDOUT, timeout=AGENT_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        record = {"task": a.task, "what": TASKS[a.task].__doc__, "at": stamp, "commit": commit(), "prompt": task["prompt"], "truth": task["truth"],
                  "decision_model": a.model, "limits": {"budget_tokens": BUDGET_TOKENS, "wall_seconds": WALL_SECONDS},
                  "seconds": round(time.time() - t0, 1), **measure(world.dir / "agent-stream.jsonl")}
        record["right"] = bool(record["answer"]) and bool(task["check"](record["answer"]))
        record["facts"] = world.facts
        runs = [json.loads(line) for events in sorted(world.runs.glob("*/events.jsonl"))
                for line in events.read_text().splitlines()[-1:] if '"event": "end"' in line]
        record["runs"] = runs
        record["human_stand_in"] = [f"{run} {request}" for run, request in confirmed]
    finally:
        stop.set()
        world.close()
    evidence = Path(a.evidence) / f"{a.task}_{stamp}"
    evidence.mkdir(parents=True)
    (evidence / "record.json").write_text(json.dumps(record, indent=1))
    # The stream of the main agent, without the first event: it lists the tools and the settings of the machine.
    kept = [line for line in (world.dir / "agent-stream.jsonl").read_text().splitlines() if '"subtype":"init"' not in line.replace(" ", "")]
    (evidence / "agent-stream.jsonl").write_text("\n".join(kept) + "\n")
    for name in ("hook-input.jsonl", "human.log", "uniselector.toml"):
        if (world.dir / name).exists():
            shutil.copy(world.dir / name, evidence / name)
    if world.runs.exists():
        shutil.copytree(world.runs, evidence / "runs")
    print(json.dumps({k: v for k, v in record.items() if k not in ("prompt", "what")}, indent=1))
    print(evidence)


if __name__ == "__main__":
    main()
