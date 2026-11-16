"""The world of one task run: a DataHandles HTTP server, three principals, and the files for the main agent.

Both MCP servers of the main agent (DataHandles and Uniselector) reach one runtime through the
HTTP transport. The human's token is not in the environment of the Uniselector server.
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

from datahandles.sdk import connect

from uniselector.config import Config

ROLES = {"planner": "UNISELECTOR_PLANNER_TOKEN", "uniselector": "UNISELECTOR_TOKEN", "human": "UNISELECTOR_HUMAN_TOKEN"}
BIN = Path(sys.executable).parent
KEY_ENV = Config.decision_key_env   # the environment variable with the API key of the decision model


def toml(value) -> str:
    """A value of a runner table as TOML. A table goes on one line."""
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {toml(v)}" for k, v in value.items()) + " }"
    return json.dumps(value)   # a text, a number and a truth value have the same form in JSON and in TOML


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class World:
    def __init__(self, directory: Path, decision_model: str, budget_tokens: int, wall_seconds: int, human_only=(), runners=None):
        """`runners` has the `[runners.NAME]` tables of the configuration: the model runners that an object can name."""
        self.dir = directory.resolve()
        (self.dir / "agent").mkdir(parents=True)
        self.processes, self.facts = [], {}
        self.tokens = {role: secrets.token_hex(16) for role in ROLES}
        tokens = self.dir / "tokens.json"
        tokens.write_text(json.dumps({t: {"principal": role, "create": role != "uniselector"} for role, t in self.tokens.items()}))
        port = free_port()
        self.start([sys.executable, "-m", "datahandles.protocols.http", "--state", str(self.dir / "state"), "--tokens", str(tokens), "--port", str(port)])
        self.url = f"http://127.0.0.1:{port}"
        self.planner = self._connect("planner")
        self.runs = self.dir / "runs"
        (self.dir / "uniselector.toml").write_text(
            f"[limits]\nbudget_tokens = {budget_tokens}\nwall_seconds = {wall_seconds}\n\n"
            f'[runtime]\ntarget = "{self.url}"\nruns_dir = "{self.runs}"\n\n'
            f'[decision]\ndecision_model = "{decision_model}"\n\n'
            f"[actions]\nhuman_only = {json.dumps(list(human_only))}\n" + "".join(
                f"\n[runners.{name}]\n" + "".join(f"{k} = {toml(v)}\n" for k, v in table.items()) for name, table in (runners or {}).items()))
        # The client fills in the API key of the decision model and of each runner from its environment. A key is in no file of the world.
        keys = {KEY_ENV, *(table["key_env"] for table in (runners or {}).values() if "key_env" in table)}
        env = {**{name: self.tokens[role] for role, name in ROLES.items() if role != "human"}, **{name: f"${{{name}}}" for name in sorted(keys)}}
        (self.dir / "mcp.json").write_text(json.dumps({"mcpServers": {
            "datahandles": {"command": sys.executable, "args": ["-m", "datahandles.protocols.mcp", "--url", self.url],
                            "env": {"DATAHANDLES_TOKEN": self.tokens["planner"]}},
            "uniselector": {"command": str(BIN / "uniselector-mcp"), "args": ["--config", str(self.dir / "uniselector.toml")], "env": env}}}))
        # The hook of Step 6. `tee` keeps what the hook got, to record what a hook can see.
        hook = f"tee -a {self.dir / 'hook-input.jsonl'} | {BIN / 'uniselector-hook'}"
        (self.dir / "settings.json").write_text(json.dumps({"hooks": {"PostToolUse": [
            {"matcher": "mcp__uniselector__plan_status", "hooks": [{"type": "command", "command": hook}]}]}}))

    def start(self, command: list) -> subprocess.Popen:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(process)
        return process

    def _connect(self, role: str):
        end = time.time() + 20
        while True:
            try:
                client = connect(self.url, role, token=self.tokens[role])
                client.handles()
                return client
            except OSError:
                if time.time() > end:
                    raise
                time.sleep(0.2)

    def human(self):
        """A client of the human's principal. The driver uses it to make a resource that only the human holds."""
        return self._connect("human")

    def human_env(self) -> dict:
        return {**os.environ, "UNISELECTOR_HUMAN_TOKEN": self.tokens["human"]}

    def close(self) -> None:
        for process in self.processes:
            process.terminate()
        for process in self.processes:
            process.wait(10)
