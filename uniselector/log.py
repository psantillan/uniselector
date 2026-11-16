"""The logs of one run: JSON lines in the directory of the run.

decisions.jsonl  one line for each look of the decision model, and one last line with the outcome of the run
events.jsonl     one line for each version of an object, each change of an output, each wake, answer and confirmation
"""
import json
import threading
import time
from pathlib import Path


class RunLog:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock, self._closed = threading.Lock(), False

    def write(self, name: str, record: dict) -> None:
        line = json.dumps({"t": round(time.time(), 3), **record}, default=str)
        with self._lock:
            if not self._closed:
                with open(self.dir / f"{name}.jsonl", "a") as f:
                    f.write(line + "\n")

    def close(self) -> None:
        """The run ended. The line of the end stays the last line: the thread of a run can still work when its process ends the run."""
        with self._lock:
            self._closed = True

    def save(self, name: str, value: dict) -> Path:
        path = self.dir / name
        path.write_text(json.dumps(value, indent=1, default=str))
        return path
