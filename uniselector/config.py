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
    budget_tokens: int    # model tokens (input + output) of the decision model and the sub-agents
    wall_seconds: float   # seconds that the run works; the clock stops while the run waits for the main agent or the human

    # Where the DataHandles runtime is: a state directory (a runtime in this process) or an http(s) URL.
    target: str = ".datahandles"
    runs_dir: str = ".uniselector/runs"

    # The four principals. With a URL target, each one needs a bearer token in the named environment variable.
    planner: str = "planner"
    orchestrator: str = "uniselector"
    subagent: str = "subagent"
    human: str = "human"
    token_env: dict = field(default_factory=lambda: {
        "planner": "UNISELECTOR_PLANNER_TOKEN", "orchestrator": "UNISELECTOR_TOKEN",
        "subagent": "UNISELECTOR_SUBAGENT_TOKEN", "human": "UNISELECTOR_HUMAN_TOKEN"})

    # The decision model, behind a Decisions API (docs/DECISIONS.md, D17). The API key is in the named environment variable.
    decision_url: str = "https://openrouter.ai/api/alpha/decisions"
    decision_model: str = ""
    decision_key_env: str = "OPENROUTER_API_KEY"

    # The model of the sub-agent, on an Ollama server (D12). A decision model writes no text, so it cannot explore.
    model_url: str = "http://localhost:11434"
    model: str = ""
    num_ctx: int = 8192

    # When a decision abstains (docs/DECISIONS.md, D5).
    abstain_top: float = 0.5     # the top option has less than this share
    abstain_ratio: float = 0.5   # the second option has more than this share of the top option

    question_tokens: int = 40_000   # the cap of one sub-agent question (D9)
    human_only: list = field(default_factory=list)   # capabilities that only the human can confirm
    hidden_capabilities: list = field(default_factory=lambda: ["log.own", "log.audit"])   # not offered as operations (D7)

    wait_seconds: float = 2.0   # the time of one `wait` (D10)
    results_kept: int = 5       # "the last few results" in the state (D11)
    value_chars: int = 600      # the size of one result value in the state and in a summary (D11)
    handles_kept: int = 8       # the start handles and the newest handles in the state (D11)


def load(path) -> Config:
    data = tomllib.loads(Path(path).read_text())
    limits = data.pop("limits", {})
    missing = [k for k in ("budget_tokens", "wall_seconds") if k not in limits]
    if missing:
        raise ValueError(f"{path}: [limits] must state {' and '.join(missing)}. The user decides the hard limits.")
    flat = {**limits, **{k: v for section in data.values() for k, v in section.items()}}
    unknown = sorted(set(flat) - {f.name for f in fields(Config)})
    if unknown:
        raise ValueError(f"{path}: unknown settings {unknown}")
    return Config(**flat)


def clients(cfg: Config) -> dict:
    """One DataHandles client for each principal: planner, orchestrator, subagent, human."""
    names = {role: getattr(cfg, role) for role in ("planner", "orchestrator", "subagent", "human")}
    if cfg.target.startswith(("http://", "https://")):
        # The server takes the principal from the token. A role with no token in the environment has no client.
        return {role: connect(cfg.target, name, token=os.environ[cfg.token_env[role]])
                for role, name in names.items() if cfg.token_env[role] in os.environ}
    first = connect(cfg.target, names["planner"])
    return {role: first if role == "planner" else Client(first.runtime, name) for role, name in names.items()}
