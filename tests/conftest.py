"""Shared parts of the tests: a runtime in a temporary directory, and a decider that follows a script."""
import random

import pytest

from uniselector.config import Config
from uniselector.decide import Distribution
from uniselector.run import ENDED, Harness


class Scripted:
    """A decider with no model. Each decision takes the next line of the script: the option whose text has that line in it."""

    def __init__(self, script: list, tokens: int = 100):
        self.script, self.tokens, self.seen = list(script), tokens, []

    def __call__(self, state: dict, options: list) -> Distribution:
        self.seen.append((state["decision"]["text"], options))
        assert self.script, f"the script ended at: {state['decision']['text']} {options}"
        want = self.script.pop(0)
        if isinstance(want, list):   # a distribution, to test the abstain rule
            return Distribution(want, self.tokens, 0.0)
        index = next((i for i, text in enumerate(options) if want in text), None)
        assert index is not None, f"no option has {want!r}: {options}"
        return Distribution([float(i == index) for i in range(len(options))], self.tokens, 0.0)


def no_asker(question, max_tokens, seconds):
    return {"needs_planner": "the test has no sub-agent", "calls": [], "tokens": 0}


@pytest.fixture
def cfg(tmp_path):
    return Config(budget_tokens=100_000, wall_seconds=60, target=str(tmp_path / "state"), runs_dir=str(tmp_path / "runs"),
                  wait_seconds=0.2)


@pytest.fixture
def events(tmp_path):
    rng = random.Random(1)
    rows = ["id,customer_id,event_type,amount"]
    rows += [f"{i},{rng.randint(1, 9)},{rng.choice(['purchase', 'view', 'cart'])},{rng.randint(1, 300)}" for i in range(60)]
    path = tmp_path / "events.csv"
    path.write_text("\n".join(rows))
    return path


def harness(cfg, script, asker=no_asker, **kw) -> Harness:
    return Harness(cfg, decide=Scripted(script, **kw), asker=asker)


def finish(h: Harness, run: str, until=ENDED, seconds: float = 30) -> dict:
    """Wait until the run is in one of the states `until`. Return its summary."""
    import time
    end = time.time() + seconds
    while time.time() < end:
        summary = h.status(run)
        if summary["state"] in until:
            return summary
        time.sleep(0.02)
    raise AssertionError(f"the run stayed in {summary['state']}: {summary['why']}")
