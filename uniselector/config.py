"""The configuration: the two hard limits that the user decides, and the settings of the harness.

The file is TOML. `[limits]` has no default: the user must state the budget and the wall time.
Each other value has a default, and docs/DECISIONS.md gives the reason for each default.
"""
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from datahandles.sdk import Client, connect


@dataclass
class Config:
    # The hard limits. A plan can ask for lower limits, never for higher ones.
    budget_tokens: int    # model tokens of the decision model (input) and of the model runners (input + output)
    wall_seconds: float   # seconds that the run works; the clock stops while the run waits for the main agent or the human

    # Where the DataHandles runtime is: a state directory (a runtime in this process) or an http(s) URL.
    target: str = ".datahandles"
    runs_dir: str = ".uniselector/runs"

    # The three principals. With a URL target, each one needs a bearer token in the named environment variable.
    planner: str = "planner"
    orchestrator: str = "uniselector"
    human: str = "human"
    token_env: dict = field(default_factory=lambda: {
        "planner": "UNISELECTOR_PLANNER_TOKEN", "orchestrator": "UNISELECTOR_TOKEN", "human": "UNISELECTOR_HUMAN_TOKEN"})

    # The decision model, behind a Decisions API (docs/DECISIONS.md, D17). The API key is in the named environment variable.
    decision_url: str = "https://openrouter.ai/api/alpha/decisions"
    decision_model: str = ""
    decision_key_env: str = "OPENROUTER_API_KEY"

    inside: float = 0.5   # a variable is inside its limits when the probability that its sentence is true is this or more (D31)
    human_only: list = field(default_factory=list)   # capabilities that only the human can confirm

    wait_seconds: float = 2.0   # the time of one tick: the run observes each object one time (D35)
    value_chars: int = 600      # the size of one output in the state and in a summary (D36)

    # The runners: one `[runners.NAME]` table for each (D43, D53). An object names its runners. The main agent and the human need no table.
    runners: dict = field(default_factory=dict)
    # Can a plan name a runner in code that the main agent wrote (`FILE.py:NAME`)? That code runs in this process (D53).
    plan_callables: bool = False


def load(path) -> Config:
    data = tomllib.loads(Path(path).read_text())
    limits = data.pop("limits", {})
    missing = [k for k in ("budget_tokens", "wall_seconds") if k not in limits]
    if missing:
        raise ValueError(f"{path}: [limits] must state {' and '.join(missing)}. The user decides the hard limits.")
    runners = data.pop("runners", {})
    for name, runner in runners.items():
        # `wake_tokens` has no default: it is a limit of the spend, so the user states it. A table with `call` names code of the user.
        model = {"url", "model", "wake_tokens"} <= set(runner) <= {"url", "model", "wake_tokens", "key_env", "body"}
        if name in ("main", "human") or ":" in name or not ("call" in runner or model):
            raise ValueError(f"{path}: [runners.{name}] must state url, model and wake_tokens, and it can state key_env and body. Or it "
                             "states call. The names main and human are taken, and a name has no colon.")
    flat = {**limits, **{k: v for section in data.values() for k, v in section.items()}}
    unknown = sorted(set(flat) - {f.name for f in fields(Config)})
    if unknown:
        raise ValueError(f"{path}: unknown settings {unknown}")
    return Config(**flat, runners=runners)


def clients(cfg: Config) -> dict:
    """One DataHandles client for each principal: planner, orchestrator, human."""
    names = {role: getattr(cfg, role) for role in ("planner", "orchestrator", "human")}
    if cfg.target.startswith(("http://", "https://")):
        # The server takes the principal from the token. A role with no token in the environment has no client.
        return {role: connect(cfg.target, name, token=os.environ[cfg.token_env[role]])
                for role, name in names.items() if cfg.token_env[role] in os.environ}
    first = connect(cfg.target, names["planner"])
    return {role: first if role == "planner" else Client(first.runtime, name) for role, name in names.items()}
