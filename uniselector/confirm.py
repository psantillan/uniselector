"""The human's decision on a request that the main agent passed on.

  uniselector-confirm RUN_DIRECTORY REQUEST approve
  uniselector-confirm RUN_DIRECTORY REQUEST deny

The run reads the decision from a file in its directory. With `--url`, the command first approves
the action in DataHandles as the human's principal (the token is in UNISELECTOR_HUMAN_TOKEN), so the
approval does not pass through the process of the orchestrator.
"""
import argparse
import json
import os
from pathlib import Path

from datahandles.sdk import connect


def main() -> None:
    p = argparse.ArgumentParser(description="Approve or deny an action that a Uniselector run passed to the human.")
    p.add_argument("run", help="the directory of the run: the `logs` value of plan_status")
    p.add_argument("request", help="the id of the request, for example req_1")
    p.add_argument("decision", choices=["approve", "deny"])
    p.add_argument("--url", help="the URL of the DataHandles runtime, when the human has a token of their own")
    a = p.parse_args()
    directory = Path(a.run)
    pending = json.loads((directory / "events.jsonl").read_text().splitlines()[-1]).get("pending") or {}
    if pending.get("id") != a.request:
        p.error(f"the run has no pending request {a.request}")
    print(f"{pending['capability']} on {pending['target'].get('name', pending['target']['id'])} with input {json.dumps(pending['input'])}")
    if a.url and a.decision == "approve":
        connect(a.url, token=os.environ["UNISELECTOR_HUMAN_TOKEN"]).resolve(pending["graph"]).invoke("graph.approve", {"node": "action"})
    (directory / f"human-{a.request}.json").write_text(json.dumps({"decision": a.decision}))
    print(f"{a.decision}: recorded")


if __name__ == "__main__":
    main()
