"""Run one long task with a real main agent, and record what the main agent's context held.

  python -m uniselector_tasks.drive TASK --work DIRECTORY --evidence DIRECTORY --decision-model MODEL

The main agent is Claude Code in non-interactive mode. It gets a plain task, the DataHandles tools
and the four Uniselector tools. It builds the objects and writes the variables itself. The driver
changes the world while the main agent sleeps: a column gets a new name, a sensor stops. It records
the largest context of the main agent, the tool calls, the runs, and whether the answer is right.

TASK is one of: schema, shift, silent, goal, goal-human, goal-pass, goal-held, code, api.

`--runners` gives the configuration two model runners, `local` and `top`, and tells the main agent
to name them in its objects. `--budget-tokens` sets a lower budget than the default.
"""
import argparse
import csv
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from uniselector.gate import is_action

from .slow_api import REPORT
from .world import BIN, KEY_ENV, World, free_port

HERE = Path(__file__).resolve().parent
HOW = ("Use Uniselector for each part of this work that waits or watches: do not wait or watch yourself. Reply with the answer only.")
# The baseline: the main agent does the same task with the DataHandles tools only, and no run of Uniselector.
DIRECT = "Do this work yourself with the DataHandles tools: find the handles with handle_find and invoke the operations. Reply with the answer only."
# The session of a task has one turn. The human decides at a terminal, and the main agent must stay in its turn while it waits.
ALONE = ("The heater needs my approval for each switch. Do not switch it yourself: give each switch to the run as an object, and pass its "
         "request to me. I cannot reply to you in this session. When a request is with me, I see the notice and I decide at my terminal. "
         "Follow the run with plan_status and wait_seconds until I decide.")
# One decision goes to the human, and the next one stays with the main agent.
PASS = ("Do not switch the heater yourself: give each switch to the run as an object. To switch the heater on is my decision: pass that "
        "request to me. You can approve the request to switch it off yourself. I cannot reply to you in this session. When a request is "
        "with me, I see the notice and I decide at my terminal. Follow the run with plan_status and wait_seconds until I decide.")
# The main agent cannot read the descriptor of a capability that it does not hold (docs/GAPS.md, G16), so the prompt gives the input.
HELD = ('Only my principal holds the capability that switches the heater: `room.heater`, with the input {"on": true} or {"on": false}. '
        'You cannot invoke it. Give each switch to the run as an object with the runners ["human"]. I cannot reply to you in this session. '
        "When a request is with me, I see the notice and I decide at my terminal. Follow the run with plan_status and wait_seconds until I decide.")
BY_HUMAN = {"goal-human": ALONE, "goal-pass": PASS, "goal-held": HELD}
RUNNERS = ('The configuration has the model runners `local` and `top`. Give an object that a model can repair the runners ["local", "top"] '
           "and a check of its own (`variables` of the object), so that its repair does not need you.")
# ASSUMED: the hard limits of the task runs. The user decides the hard limits; these are the values of uniselector.example.toml.
# The budget is 5 wakes of the runner `top` at its limit: about 0.75 USD at 0.15 USD for each million input tokens.
BUDGET_TOKENS, WALL_SECONDS = 5_000_000, 1800
# ASSUMED: 40 minutes. The longest time that the driver gives the main agent for one task.
AGENT_SECONDS = 2400
# The first operation of the main agent in its event stream. The name of the tool is also in the first event, in the list of the tools.
FIRST_OPERATION = re.compile(r'"type":"tool_use","id":"[^"]*","name":"mcp__datahandles__handle_invoke"')
REGIONS = ["north", "south", "east", "west"]
# The feed of the two order tasks: it starts with FIRST rows, it gets BATCH rows at each step, it changes at CHANGE rows, and it ends at LAST rows.
FIRST, BATCH, CHANGE, LAST = 200, 20, 300, 600


def write(path: Path, rows: list) -> None:
    """Replace a CSV file in one step, so that no reader sees a part of a row."""
    with open(path.with_suffix(".new"), "w", newline="") as f:
        csv.writer(f).writerows(rows)
    os.replace(path.with_suffix(".new"), path)


def orders(world: World, a, change: str) -> dict:
    """A feed of orders that gets rows, and that changes one time: the name of a column, or the name of a status."""
    rng = random.Random(11)
    header, rows = ["id", "region", "status", "amount"], []

    def more(n: int, paid: str) -> None:
        rows.extend([len(rows) + i, rng.choice(REGIONS), rng.choice([paid, paid, "pending", "refunded"]), round(rng.uniform(5, 400), 2)] for i in range(n))

    more(FIRST, "paid")
    path = world.dir / "orders.csv"
    write(path, [header, *rows])
    world.planner.register("orders", adapter="duckdb", source={"path": str(path)})

    def schedule(stop: threading.Event, mark) -> None:
        paid = "paid"
        while len(rows) < LAST and not stop.wait(a.interval_ms / 1000):
            if len(rows) == CHANGE:
                if change == "schema":
                    header[3] = "amount_usd"
                else:
                    paid = "settled"
                mark(change)
            more(BATCH, paid)
            write(path, [header, *rows])
        mark("the feed ended")

    def check(text: str) -> bool:
        total = {r: round(sum(row[3] for row in rows if row[1] == r and row[2] in ("paid", "settled")), 2) for r in REGIONS}
        world.facts |= {"rows": len(rows), "truth": total}
        return len(rows) == LAST and all(r in text.lower() and str(int(v)) in text.replace(",", "") for r, v in total.items())

    what = {"schema": "The team that owns the feed can give a column a new name without notice. When that happens, repair your work.",
            "shift": "The team that owns the feed can give a status a new name without notice. A status with a new name has the meaning of the "
                     "status that it replaced, and the total must then have the orders of the two names."}[change]
    return {"prompt": f"A feed of orders is registered in DataHandles as `orders`. It gets new rows for the next minutes, and it ends at {LAST} "
                      f"rows. Keep a live total of `amount` for each region, over the orders whose `status` is `paid`. {what} "
                      f"When the feed has {LAST} rows, give the total of each region.",
            "truth": "the total of each region over the paid orders of the final feed (record: facts.truth)", "check": check, "schedule": schedule}


def schema(world: World, a) -> dict:
    """A live data pipeline with a schema break: the column `amount` gets the name `amount_usd`."""
    return orders(world, a, "schema")


def shift(world: World, a) -> dict:
    """A live data pipeline with a shift of the values: new orders have the status `settled` in place of `paid`."""
    return orders(world, a, "shift")


def silent(world: World, a) -> dict:
    """A missing sensor: the feed in use stops, and a second feed goes on."""
    rng = random.Random(5)
    header, feeds, last = ["seq", "temperature"], {"sensor_a": [], "sensor_b": []}, 60

    def more(name: str) -> None:
        feeds[name].append([len(feeds[name]), round(rng.uniform(18, 29) + (2.5 if name == "sensor_b" else 0), 1)])
        write(world.dir / f"{name}.csv", [header, *feeds[name]])

    for name in feeds:
        for _ in range(5):
            more(name)
        world.planner.register(name, adapter="duckdb", source={"path": str(world.dir / f"{name}.csv")})

    def schedule(stop: threading.Event, mark) -> None:
        while len(feeds["sensor_b"]) < last and not stop.wait(a.interval_ms / 1000):
            if len(feeds["sensor_a"]) == 15 and len(feeds["sensor_b"]) == 15:
                mark("sensor_a stopped")
            if len(feeds["sensor_b"]) < 15:
                more("sensor_a")
            more("sensor_b")
        mark("the feed ended")

    def check(text: str) -> bool:
        world.facts |= {"readings": {name: len(rows) for name, rows in feeds.items()}, "truth": max(r[1] for r in feeds["sensor_b"])}
        return len(feeds["sensor_b"]) == last and str(world.facts["truth"]) in text

    seconds = a.interval_ms / 1000
    return {"prompt": f"Two sensors of one room are registered in DataHandles as the feeds `sensor_a` and `sensor_b`. A sensor writes one new "
                      f"reading each {seconds:g} seconds. Keep a live highest `temperature` from `sensor_a`. A sensor can stop without notice. "
                      f"When the sensor that you use gives no new reading for {5 * seconds:g} seconds or more, use the other sensor in its place. "
                      f"The work ends when the sensor that you use has {last} readings. Then give the highest temperature of that sensor.",
            "truth": "the highest temperature of sensor_b (record: facts.truth)", "check": check, "schedule": schedule}


def goal(world: World, a) -> dict:
    """A stream with a goal to watch for. The heater is an action."""
    if a.task == "goal-held":   # the human's principal makes the room, and it keeps the action
        room = world.human().register("room", adapter="room", source={"interval_ms": a.interval_ms})
        room.grant("planner", [c["name"] for c in room.describe()["capabilities"] if not is_action(c)])
    else:
        room = world.planner.register("room", adapter="room", source={"interval_ms": a.interval_ms})

    def check(text: str) -> bool:
        readings, made = [], room.invoke("room.latest")["seq"] + 1
        while len(readings) < made:   # `room.read` refuses a range that is not all made, and its output has a size limit
            readings += room.invoke("room.read", {"from_seq": len(readings), "count": made - len(readings)})["readings"]
        on = [r["seq"] for r in readings if r["heater"]]
        if not on or readings[-1]["heater"]:
            return False
        last = next(r for r in readings if r["seq"] == on[-1])   # the last reading with the heater on
        world.facts |= {"heater_on_seq": on[0], "heater_off_after_seq": on[-1], "temperature_then": last["temperature"]}
        return last["temperature"] >= a.temperature

    return {"prompt": f"A room is registered in DataHandles as `room`. Heat it: switch the heater on, wait until the temperature is "
                      f"{a.temperature:g} or more, then switch the heater off. Give the temperature of the last reading that you saw.",
            "truth": f"the heater goes on, then off after a reading of {a.temperature:g} or more", "check": check}


def api(world: World, a) -> dict:
    """A slow API: one response takes minutes."""
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


TASKS = {"schema": schema, "shift": shift, "silent": silent, "goal": goal, "goal-human": goal, "goal-pass": goal, "goal-held": goal,
         "code": code, "api": api}


def change_the_world(world: World, task: dict, direct: bool, stop: threading.Event, t0: float) -> None:
    """Start the schedule of the task when the main agent started its work: at the first run, or at the first operation of the baseline."""
    stream = world.dir / "agent-stream.jsonl"
    while not (any(world.runs.glob("*/plan.json")) or (direct and FIRST_OPERATION.search(stream.read_text()))):
        if stop.wait(0.5):
            return
    world.facts["changes"] = [{"what": "the schedule started", "agent_s": round(time.time() - t0, 1)}]
    task["schedule"](stop, lambda what: world.facts["changes"].append({"what": what, "agent_s": round(time.time() - t0, 1)}))


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
    p.add_argument("--decision-model", required=True, help="the decision model, on the Decisions API of the configuration default")
    p.add_argument("--agent-model", default="sonnet", help="the model of the main agent")
    p.add_argument("--seconds", type=float, default=150, help="api, code: how long the slow part takes")
    p.add_argument("--interval-ms", type=int, default=3000, help="the time between two readings of the room or a sensor, or two steps of a feed")
    p.add_argument("--temperature", type=float, default=26, help="goal: the temperature to wait for")
    p.add_argument("--direct", action="store_true", help="the baseline: the main agent does the task itself, without Uniselector")
    p.add_argument("--budget-tokens", type=int, default=BUDGET_TOKENS, help="the token budget of the configuration")
    p.add_argument("--runners", action="store_true", help="configure the model runners `local` and `top`")
    p.add_argument("--local-url", default="http://127.0.0.1:8000/v1/chat/completions", help="the chat completions API of the runner `local`")
    p.add_argument("--local-model", help="the model of the runner `local`")
    p.add_argument("--local-key-env", help="the environment variable with the API key of the runner `local`")
    p.add_argument("--local-body", type=json.loads, help="more keys for each request of the runner `local`, as JSON")
    p.add_argument("--top-model", help="the model of the runner `top`, on the chat completions API of OpenRouter")
    a = p.parse_args()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    runners = {"local": {"url": a.local_url, "model": a.local_model,
                         **({"key_env": a.local_key_env} if a.local_key_env else {}), **({"body": a.local_body} if a.local_body else {})},
               "top": {"url": "https://openrouter.ai/api/v1/chat/completions", "model": a.top_model,
                       "key_env": KEY_ENV}} if a.runners else {}
    world = World(Path(a.work) / f"{a.task}_{stamp}", a.decision_model, a.budget_tokens, WALL_SECONDS,
                  human_only=["room.heater"] if a.task == "goal-human" else [], runners=runners)
    stop, confirmed = threading.Event(), []
    try:
        task = TASKS[a.task](world, a)
        if a.task in BY_HUMAN:
            threading.Thread(target=stand_in_for_the_human, args=(world, stop, confirmed), daemon=True).start()
        how, servers = " ".join([HOW, *([BY_HUMAN[a.task]] if a.task in BY_HUMAN else []), *([RUNNERS] if a.runners else [])]), world.dir / "mcp.json"
        if a.direct:   # the baseline has no Uniselector server: the main agent cannot hand the work off
            how, servers = DIRECT, world.dir / "mcp-direct.json"
            servers.write_text(json.dumps({"mcpServers": {"datahandles": json.loads((world.dir / "mcp.json").read_text())["mcpServers"]["datahandles"]}}))
        command = ["claude", "-p", f"{task['prompt']}\n\n{how}", "--model", a.agent_model, "--tools", "", "--mcp-config", str(servers),
                   "--strict-mcp-config", "--settings", str(world.dir / "settings.json"), "--allowedTools", "mcp__datahandles__*,mcp__uniselector__*",
                   "--output-format", "stream-json", "--verbose", "--include-hook-events", "--no-session-persistence"]
        t0 = time.time()
        if "schedule" in task:
            threading.Thread(target=change_the_world, args=(world, task, a.direct, stop, t0), daemon=True).start()
        with open(world.dir / "agent-stream.jsonl", "w") as out:
            try:
                subprocess.run(command, cwd=world.dir / "agent", stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, timeout=AGENT_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        record = {"task": a.task, "direct": a.direct, "what": TASKS[a.task].__doc__, "at": stamp, "commit": commit(), "prompt": task["prompt"], "truth": task["truth"],
                  "decision_model": a.decision_model, "limits": {"budget_tokens": a.budget_tokens, "wall_seconds": WALL_SECONDS}, "runners": runners,
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
    evidence = Path(a.evidence) / f"{a.task}{'-direct' if a.direct else ''}_{stamp}"
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
