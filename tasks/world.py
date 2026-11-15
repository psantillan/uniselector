"""The world of one task run: a DataHandles HTTP server, four principals, and the files for the main agent.

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

ROLES = {"planner": "UNISELECTOR_PLANNER_TOKEN", "uniselector": "UNISELECTOR_TOKEN",
         "subagent": "UNISELECTOR_SUBAGENT_TOKEN", "human": "UNISELECTOR_HUMAN_TOKEN"}
BIN = Path(sys.executable).parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class World:
    def __init__(self, directory: Path, model_url: str, model: str, budget_tokens: int, wall_seconds: int, human_only=()):
        self.dir = directory.resolve()
        (self.dir / "agent").mkdir(parents=True)
        self.model_url, self.model, self.processes = model_url, model, []
        self.tokens = {role: secrets.token_hex(16) for role in ROLES}
        tokens = self.dir / "tokens.json"
        tokens.write_text(json.dumps({t: {"principal": role, "create": role == "planner"} for role, t in self.tokens.items()}))
        port = free_port()
        self.start([sys.executable, "-m", "datahandles.protocols.http", "--state", str(self.dir / "state"), "--tokens", str(tokens), "--port", str(port)])
        self.url = f"http://127.0.0.1:{port}"
        self.planner = self._connect("planner")
        self.runs = self.dir / "runs"
        (self.dir / "uniselector.toml").write_text(
            f"[limits]\nbudget_tokens = {budget_tokens}\nwall_seconds = {wall_seconds}\n\n"
            f'[runtime]\ntarget = "{self.url}"\nruns_dir = "{self.runs}"\n\n'
            f'[model]\nmodel_url = "{model_url}"\nmodel = "{model}"\n\n'
            f"[actions]\nhuman_only = {json.dumps(list(human_only))}\n")
        env = {name: self.tokens[role] for role, name in ROLES.items() if role != "human"}
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

    def human_env(self) -> dict:
        return {**os.environ, "UNISELECTOR_HUMAN_TOKEN": self.tokens["human"]}

    def close(self) -> None:
        for process in self.processes:
            process.terminate()
        for process in self.processes:
            process.wait(10)
