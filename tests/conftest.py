"""Shared parts of the tests: a runtime in a temporary directory, and a detector that follows a rule."""
import random
import time

import pytest

from uniselector.config import Config
from uniselector.decide import NO_ERROR, Distribution
from uniselector.run import ENDED, Harness


class Scripted:
    """A detector with no model. A rule of the test says if a statement is true in the state."""

    def __init__(self, rule=None, tokens: int = 100):
        self.rule, self.tokens, self.seen = rule or (lambda state, statement: True), tokens, []

    def __call__(self, state: dict, statements: list) -> Distribution:
        self.seen.append((state, statements))
        true = [not any("error" in o for o in state["objects"]) if s == NO_ERROR else self.rule(state, s) for s in statements]
        return Distribution([0.9 if t else 0.1 for t in true], self.tokens, 0.0)


@pytest.fixture
def cfg(tmp_path):
    return Config(budget_tokens=100_000, wall_seconds=60, target=str(tmp_path / "state"), runs_dir=str(tmp_path / "runs"),
                  wait_seconds=0.1)


@pytest.fixture
def events(tmp_path):
    rng = random.Random(1)
    rows = ["id,customer_id,event_type,amount"]
    rows += [f"{i},{rng.randint(1, 9)},{rng.choice(['purchase', 'view', 'cart'])},{rng.randint(1, 300)}" for i in range(60)]
    path = tmp_path / "events.csv"
    path.write_text("\n".join(rows) + "\n")
    return path


def harness(cfg, rule=None, **kw) -> Harness:
    return Harness(cfg, decide=Scripted(rule, **kw))


def finish(h: Harness, run: str, until=ENDED, seconds: float = 30) -> dict:
    """Wait until the run is in one of the states `until`. Return its summary."""
    end = time.time() + seconds
    while time.time() < end:
        summary = h.status(run)
        if summary["state"] in until:
            return summary
        time.sleep(0.02)
    raise AssertionError(f"the run stayed in {summary['state']}: {summary['why']}")


def shows(h: Harness, run: str, name: str, test, seconds: float = 30) -> dict:
    """Wait until the object `name` shows something that passes `test`. Return what the object shows."""
    end = time.time() + seconds
    while time.time() < end:
        shown = next((o for o in h.status(run)["objects"] if o["name"] == name), None)
        if shown and test(shown):
            return shown
        time.sleep(0.02)
    raise AssertionError(f"the object {name} stayed at {shown}")


def spent(h: Harness, run: str, tokens: int, seconds: float = 30) -> dict:
    """Wait until the run spent `tokens` tokens or more. Return the summary."""
    end = time.time() + seconds
    while time.time() < end:
        summary = h.status(run)
        if summary["limits"]["tokens"] >= tokens:
            return summary
        time.sleep(0.02)
    raise AssertionError(f"the run stayed at {summary['limits']['tokens']} tokens")
