"""A compaction runner: it asks the harness of the main agent to compact the context of the main agent.

  [runtime]
  main_context = "DIRECTORY/context.json"

  [runners.compact]
  call = "uniselector_tasks.compact:compact"
  directory = "DIRECTORY"

Only the process that holds the input of the main agent can compact it (docs/HOOKS.md). So the runner
and that process share a directory. The runner writes `compact-request.json`. The harness compacts
the main agent with the method of the main agent, writes the new context to `context.json`, and then
writes `compact-done.json`. The driver of the tasks (uniselector_tasks.drive) is such a harness.
"""
import json
import os
import time
from pathlib import Path

CONTEXT, REQUEST, DONE = "context.json", "compact-request.json", "compact-done.json"
# ASSUMED: 0.2 seconds between two reads of the directory. A compaction takes seconds, so the wait adds little to it.
READ_SECONDS = 0.2


def put(path: Path, value: dict) -> None:
    """Replace a JSON file in one step, so that no reader sees a part of it."""
    path.with_suffix(".new").write_text(json.dumps(value))
    os.replace(path.with_suffix(".new"), path)


def compact(client, directory: str):
    """Make the runner. `directory` is the directory that the runner and the harness of the main agent share."""
    request, done = Path(directory) / REQUEST, Path(directory) / DONE

    def runner(question: dict, submit, spend, seconds: float) -> None:
        done.unlink(missing_ok=True)
        put(request, {"object": question["object"]["name"], "form": question["form"]})
        end = time.time() + seconds
        while not done.exists():
            if time.time() > end:
                raise TimeoutError("the harness of the main agent did not compact")
            time.sleep(READ_SECONDS)
        result = json.loads(done.read_text())
        submit(f"the context of the main agent went from {result['before']} to {result['after']} tokens")

    return runner
