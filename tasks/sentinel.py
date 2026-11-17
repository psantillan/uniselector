"""Be the agent of one SentinelBench task: Claude Code with the DataHandles tools, with Uniselector or alone.

  python -m uniselector_tasks.sentinel TASK_URL TASK_PROMPT --work DIRECTORY --evidence DIRECTORY --decision-model MODEL [--direct]

SentinelBench (arXiv 2606.05342, github.com/microsoft/sentinel_environments) grades an agent that
waits for a condition in a web application, and then tells the user. Its harness starts one agent
process for each task, and it gives the process the URL that starts the task and the prompt. In the
configuration of the harness, this module is the agent:

  agent_subprocess: [PYTHON, -m, uniselector_tasks.sentinel, __TASK_URL__, __TASK_PROMPT__, --work, DIRECTORY, --evidence, DIRECTORY,
                     --decision-model, MODEL]

The agent of the paper reads the application through a browser. This agent has no browser. It reads
the JSON data of the application through DataHandles: the handle `app` is the HTTP API, and each
list of the application is also a table, because an object of a run needs a small output
(docs/GAPS.md, G19). The module copies each list to a CSV file about one time each second. It knows
nothing about the task: the main agent reads the prompt, builds the objects and writes the variables.

With `--direct` the main agent has no Uniselector. It has the same handles and a `sleep` tool, and
it waits by itself.
"""
import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit

from .drive import BUDGET_TOKENS, DIRECT, HOW, SLEEP, WALL_SECONDS, commit, measure, public, write
from .world import World

# The two times of the benchmark (its file server/timing.py): the last moment of a condition, and the time to react after it.
MAX_CONDITION_AT, REACTION_WINDOW = 600.0, 30.0
# ASSUMED: 6 seconds. The harness of the benchmark kills the agent process with no warning. The module ends the main agent this long
# before that, and writes the record. The time to write a record was not measured.
RECORD_SECONDS = 6.0
# Half of one tick of a run (uniselector.config, wait_seconds), so that each tick reads a copy that is one second old or newer.
COPY_SECONDS = 1.0
APP = ("You have no browser. The web application of this task is in DataHandles. The handle `app` is its HTTP API: read with GET on a "
       "path, and act with POST. Each other handle is a table: a live copy of one list of the application, new about one time each "
       "second. A list with no rows has no table until its first row comes. To reach me at the end of the task, GET the path /contact "
       "on `app` one time: that GET is the form. I cannot reply to you in this session.")


def get(base: str, path: str):
    """GET a path of the application. Return the status and the JSON body, or None for a body that is not JSON. A redirect is not followed."""
    address = urlsplit(base)
    connection = HTTPConnection(address.netloc, timeout=10)
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        reply = connection.getresponse()
        body = reply.read()
    finally:
        connection.close()
    try:
        return reply.status, json.loads(body)
    except ValueError:
        return reply.status, None


def reference(api: dict, environment: str) -> tuple:
    """The paths of one application, from the OpenAPI document of the service: the lists to copy, and a short text for the main agent."""
    def fields(schema: dict) -> str:
        while "$ref" in schema:
            schema = api["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
        return "{" + ", ".join(f"{k}{'' if k in schema.get('required', []) else '?'}: {v.get('type') or 'value'}"
                               for k, v in schema.get("properties", {}).items()) + "}"

    lists, lines = [], []
    for path, methods in sorted(api["paths"].items()):
        if not path.startswith(f"/data/{environment}-"):
            continue
        for method, spec in sorted(methods.items()):
            query = [p["name"] for p in spec.get("parameters", []) if p["in"] == "query"]
            body = spec.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            lines.append(f"{method.upper()} {path}" + (f" query: {', '.join(query)}" if query else "") + (f" body: {fields(body)}" if body else ""))
            if method == "get" and "{" not in path:
                lists.append(path)
    return lists, "\n".join(lines)


def tables(name: str, body) -> dict:
    """The tables in one answer of the application. A list of mappings is a table. A mapping is a table of one row. The plain values
    of the answer are one more table of one row."""
    found, plain = {}, {}
    for key, value in (body.items() if isinstance(body, dict) else [("rows", body)]):
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            found[key] = value
        elif isinstance(value, dict):
            found[key] = [value]
        elif not isinstance(value, list):
            plain[key] = value
    if plain:
        found["values"] = [plain]
    return {name if len(found) == 1 else f"{name}_{key}": rows for key, rows in found.items()}


def cell(value):
    """One value of a row as a CSV field. A list or a mapping is JSON text."""
    if value is None:
        return ""
    return json.dumps(value) if isinstance(value, (list, dict)) else value


class Copy:
    """The copy of the lists of the application: one CSV file and one DataHandles table for each list."""

    def __init__(self, world: World, base: str, environment: str, lists: list):
        self.world, self.base, self.lists, self.prefix = world, base, lists, f"/data/{environment}-"
        self.client, self.last, self.columns = world.planner, {}, {}

    def once(self) -> None:
        for path in self.lists:
            try:
                status, body = get(self.base, path)
            except OSError:
                continue
            if status != 200 or body is None:
                continue
            for name, rows in tables(path.removeprefix(self.prefix).replace("-", "_"), body).items():
                columns = self.columns.setdefault(name, [])
                columns.extend(k for row in rows for k in row if k not in columns)   # a column stays when a later row does not have it
                lines = [columns, *([cell(row.get(k)) for k in columns] for row in rows)]
                if self.last.get(name) == lines:
                    continue
                write(self.world.dir / f"{name}.csv", lines)
                if name not in self.last:
                    self.client.register(name, adapter="duckdb", source={"path": str(self.world.dir / f"{name}.csv")})
                self.last[name] = lines

    def keep(self, stop: threading.Event) -> None:
        self.client = self.world._connect("planner")   # a client of its own for this thread
        while not stop.wait(COPY_SECONDS):
            self.once()


def spend(stream_file: Path) -> dict:
    """The tokens and the cost of the main agent. A session that the module ended has no result event, and so no cost: the tokens of
    its turns are the record. The output tokens of a turn in the stream can be lower than the final count."""
    seen, cost = {}, None
    for line in stream_file.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "assistant":
            seen[event["message"].get("id")] = event["message"].get("usage", {})   # one message can come in more than one event
        if event.get("type") == "result":
            cost = event.get("total_cost_usd")
    keys = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    return {"agent_cost_usd": cost, "agent_tokens": {k: sum(usage.get(k, 0) for usage in seen.values()) for k in keys}}


def main() -> None:
    p = argparse.ArgumentParser(description="Be the agent of one SentinelBench task.")
    p.add_argument("task_url", help="the URL that starts the task")
    p.add_argument("prompt", help="the prompt of the task")
    p.add_argument("--name", default=Path.cwd().name, help="the id of the task; the harness runs the agent in a directory with that name")
    p.add_argument("--work", required=True, help="a directory for the world of the run: the runtime state and the tokens")
    p.add_argument("--evidence", required=True, help="a directory for the record and the logs of the run")
    p.add_argument("--decision-model", required=True, help="the decision model, on the Decisions API of the configuration default")
    p.add_argument("--agent-model", default="sonnet", help="the model of the main agent")
    p.add_argument("--speed-factor", type=float, default=1.0, help="the speed factor in the configuration of the harness")
    p.add_argument("--direct", action="store_true", help="the baseline: the main agent waits by itself, with a sleep tool and no Uniselector")
    a = p.parse_args()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    address = urlsplit(a.task_url)
    base = f"{address.scheme}://{address.netloc}"
    world = World(Path(a.work) / f"{a.name}_{stamp}", a.decision_model, BUDGET_TOKENS, WALL_SECONDS)
    stop = threading.Event()
    try:
        environment = get(base, "/data/config")[1]["environment"]
        lists, paths = reference(get(base, "/openapi.json")[1], environment)
        world.planner.register("app", adapter="api", source={"base_url": base})
        copy = Copy(world, base, environment, lists)
        get(base, f"{address.path}?{address.query}")   # the task starts here
        t0 = time.time()
        copy.once()
        threading.Thread(target=copy.keep, args=(stop,), daemon=True).start()
        servers = json.loads((world.dir / "mcp.json").read_text())
        if a.direct:   # the baseline has no Uniselector server. It has a clock
            del servers["mcpServers"]["uniselector"]
            servers["mcpServers"]["clock"] = {"command": sys.executable, "args": ["-m", "uniselector_tasks.clock"]}
            (world.dir / "mcp-direct.json").write_text(json.dumps(servers))
        how = f"{APP}\n\nThe paths of the application:\n{paths}\n\n" + (f"{DIRECT} {SLEEP}" if a.direct else HOW)
        command = ["claude", "-p", f"{a.prompt}\n\n{how}", "--model", a.agent_model, "--tools", "", "--mcp-config",
                   str(world.dir / ("mcp-direct.json" if a.direct else "mcp.json")), "--strict-mcp-config", "--settings", str(world.dir / "settings.json"),
                   "--allowedTools", "mcp__datahandles__*,mcp__uniselector__*,mcp__clock__*",
                   "--output-format", "stream-json", "--verbose", "--include-hook-events", "--no-session-persistence"]
        seconds = MAX_CONDITION_AT / a.speed_factor + REACTION_WINDOW - RECORD_SECONDS
        with open(world.dir / "agent-stream.jsonl", "w") as out:
            agent = subprocess.Popen(command, cwd=world.dir / "agent", stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
            try:
                agent.wait(max(0.0, t0 + seconds - time.time()))
                ended = "the main agent ended"
            except subprocess.TimeoutExpired:
                agent.terminate()
                agent.wait(RECORD_SECONDS / 2)
                ended = "the module ended the main agent before the harness of the benchmark kills it"
        record = {"task": a.name, "environment": environment, "direct": a.direct, "at": stamp, "commit": commit(), "prompt": f"{a.prompt}\n\n{how}",
                  "decision_model": a.decision_model, "limits": {"budget_tokens": BUDGET_TOKENS, "wall_seconds": WALL_SECONDS},
                  "speed_factor": a.speed_factor, "seconds": round(time.time() - t0, 1), "ended": ended, "tables": sorted(copy.last),
                  **measure(world.dir / "agent-stream.jsonl"), **spend(world.dir / "agent-stream.jsonl")}
        record["runs"] = [json.loads(line) for events in sorted(world.runs.glob("*/events.jsonl"))
                          for line in events.read_text().splitlines()[-1:] if '"event": "end"' in line]
        evidence = Path(a.evidence) / f"{a.name}{'-direct' if a.direct else ''}_{stamp}"
        evidence.mkdir(parents=True)
        (evidence / "record.json").write_text(json.dumps(record, indent=1))
        (evidence / "agent-stream.jsonl").write_text(public(world.dir / "agent-stream.jsonl"))
        shutil.copy(world.dir / "uniselector.toml", evidence / "uniselector.toml")
        if world.runs.exists():
            shutil.copytree(world.runs, evidence / "runs")
        print(json.dumps({k: v for k, v in record.items() if k != "prompt"}, indent=1))
        print(evidence)
    finally:
        stop.set()
        world.close()


if __name__ == "__main__":
    main()
