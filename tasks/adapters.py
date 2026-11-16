"""A DataHandles adapter for the long tasks: a script that takes time to run.

It is a resource for the runs in `tasks/`. It is not a part of the orchestrator. DataHandles
loads it through the entry point group `datahandles.adapters` (see pyproject.toml), so it uses
the adapter contract of the runtime. The package `uniselector` does not import it.
"""
import subprocess
import sys
import time
from pathlib import Path

from datahandles.core import HandleError, capability
from datahandles.runtime.adapter import ResourceAdapter, ResourceRef, Value

# The last characters of the output of a script that go to the caller. One screen of text.
TAIL_CHARS = 2000


class ScriptAdapter(ResourceAdapter):
    """A Python script as a resource. `script.run` runs it and waits for it to end."""
    name = "script"

    def __init__(self, state_dir=None):
        pass

    def lock_key(self, ref: ResourceRef):
        return ref.locator["path"]

    def open(self, source: dict) -> ResourceRef:
        path = Path(source.get("path", "")).resolve()
        if not path.is_file():
            raise HandleError("INVALID_INPUT", f"no script at {path}")
        return ResourceRef("script", {"path": str(path)}, str(path.stat().st_mtime_ns))

    def describe(self, ref: ResourceRef) -> dict:
        text = Path(ref.locator["path"]).read_text()
        first = next((line.strip(' "#') for line in text.splitlines() if line.strip(' "#')), "")
        return {"description": f"A Python script. {first}", "schema": {"file": Path(ref.locator["path"]).name, "lines": text.count("\n") + 1}}

    def discover(self, ref: ResourceRef) -> list[dict]:
        return [
            capability("script.source", "Return the first lines of the script."),
            capability("script.run", "Run the script and wait until it ends. Return the exit code, the time, and the last lines of the output. "
                                     "A run can take a long time.", {"args": {"type": "array", "items": {"type": "string"}}}),
        ]

    def observe(self, ref: ResourceRef) -> dict:
        return {"version": str(Path(ref.locator["path"]).stat().st_mtime_ns)}

    def invoke(self, ref: ResourceRef, capability: str, input: dict, *, eager: bool, call) -> Value:
        path = ref.locator["path"]
        if capability == "script.source":
            return Value({"lines": Path(path).read_text().splitlines()[:40]})
        t0 = time.time()
        process = subprocess.Popen([sys.executable, path, *input.get("args", [])], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        remove = call.on_cancel(process.kill)
        try:
            output, _ = process.communicate()
        finally:
            remove()
        call.check()
        return Value({"exit_code": process.returncode, "seconds": round(time.time() - t0, 1), "output": output[-TAIL_CHARS:]})
