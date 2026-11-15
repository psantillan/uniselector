"""The logs of one run: JSON lines in the directory of the run.

decisions.jsonl  one line for each decision node, and one last line with the outcome of the run
questions.jsonl  one line for each question to a sub-agent, with the answer and each call it made
events.jsonl     one line for each step, probe, wait, wake and confirmation
"""
import json
import threading
import time
from pathlib import Path


class RunLog:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, name: str, record: dict) -> None:
        line = json.dumps({"t": round(time.time(), 3), **record}, default=str)
        with self._lock, open(self.dir / f"{name}.jsonl", "a") as f:
            f.write(line + "\n")

    def save(self, name: str, value: dict) -> Path:
        path = self.dir / name
        path.write_text(json.dumps(value, indent=1, default=str))
        return path
