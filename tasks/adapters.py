"""Two DataHandles adapters for the long tasks: a script that takes time to run, and a helper agent.

They are resources for the runs in `tasks/`. They are not a part of the orchestrator. DataHandles
loads them through the entry point group `datahandles.adapters` (see pyproject.toml), so they use
the adapter contract of the runtime. The package `uniselector` does not import them.
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from datahandles.core import HandleError, capability
from datahandles.runtime.adapter import ResourceAdapter, ResourceRef, Value

# The last characters of the output of a script that go to the caller. One screen of text.
TAIL_CHARS = 2000
# ASSUMED: one part of a document for one model call. It leaves room for the question in a context of 8192 tokens.
PART_CHARS = 6000


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


class HelperAdapter(ResourceAdapter):
    """A helper agent as a resource: a model that reads one long document, part by part, to answer a question."""
    name = "helper"

    def __init__(self, state_dir=None):
        pass

    def lock_key(self, ref: ResourceRef):
        return ref.locator["document"]

    def open(self, source: dict) -> ResourceRef:
        document = Path(source.get("document", "")).resolve()
        if not document.is_file() or not source.get("url") or not source.get("model"):
            raise HandleError("INVALID_INPUT", "a helper needs document (a file), url (an Ollama server), and model")
        return ResourceRef("helper", {"document": str(document), "url": source["url"].rstrip("/"), "model": source["model"]},
                           str(document.stat().st_mtime_ns))

    def describe(self, ref: ResourceRef) -> dict:
        document = Path(ref.locator["document"])
        return {"description": "A helper agent. It reads one long document to answer a question about it.",
                "schema": {"document": document.name, "characters": document.stat().st_size}}

    def discover(self, ref: ResourceRef) -> list[dict]:
        return [capability("helper.ask", "Give the helper agent a question about its document. It reads each part of the document, then answers. "
                                         "This takes a long time.", {"question": {"type": "string", "minLength": 1}}, required=["question"])]

    def observe(self, ref: ResourceRef) -> dict:
        return {"version": str(Path(ref.locator["document"]).stat().st_mtime_ns)}

    def _generate(self, ref: ResourceRef, prompt: str) -> tuple[str, int]:
        body = {"model": ref.locator["model"], "prompt": prompt, "stream": False, "think": False, "options": {"temperature": 0, "seed": 0}}
        request = urllib.request.Request(ref.locator["url"] + "/api/generate", json.dumps(body).encode(),
                                         {"Content-Type": "application/json", "User-Agent": "uniselector-tasks"})
        with urllib.request.urlopen(request, timeout=600) as response:
            reply = json.load(response)
        return reply["response"].strip(), reply.get("prompt_eval_count", 0) + reply.get("eval_count", 0)

    def invoke(self, ref: ResourceRef, capability: str, input: dict, *, eager: bool, call) -> Value:
        text, question = Path(ref.locator["document"]).read_text(), input["question"]
        parts = [text[i:i + PART_CHARS] for i in range(0, len(text), PART_CHARS)]
        notes, tokens = [], 0
        for index, part in enumerate(parts):
            call.check()
            note, used = self._generate(ref, f"Part {index + 1} of {len(parts)} of a document:\n\n{part}\n\nQuestion: {question}\n"
                                             "Write the facts in this part that help to answer the question, in one or two sentences. "
                                             "If this part has no such fact, write: nothing.")
            tokens += used
            notes.append(f"Part {index + 1}: {note}")
        call.check()
        answer, used = self._generate(ref, "Notes from each part of a document:\n\n" + "\n".join(notes) +
                                      f"\n\nQuestion: {question}\nAnswer the question from the notes in one or two sentences.")
        return Value({"answer": answer, "parts": len(parts), "tokens": tokens + used})
