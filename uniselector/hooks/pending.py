"""A hook for Claude Code and Codex: after `plan_status`, show a pending human request to the user.

The hook is a PostToolUse command. It reads the hook input on stdin, finds the summary of the run
in the tool response, and prints `systemMessage`, which both tools show to the user. It prints
nothing when no request is with the human. docs/HOOKS.md has the configuration and what each tool's
hooks can see.
"""
import json
import sys


def find(value):
    """Find the summary of a run in a tool response. The shape of the response differs between the tools."""
    if isinstance(value, str):
        try:
            return find(json.loads(value))
        except ValueError:
            return None
    if isinstance(value, dict):
        if value.get("state") == "waiting_human" and isinstance(value.get("pending"), dict):
            return value
        value = list(value.values())
    if isinstance(value, list):
        return next((found for found in map(find, value) if found), None)
    return None


def message(summary: dict) -> str:
    pending = summary["pending"]
    target = pending["target"]
    return (f"Uniselector: an action needs YOUR decision. {pending['capability']} on {target.get('name', target['id'])} "
            f"with input {json.dumps(pending['input'])}. To decide, run: {pending['command']}   (or the same with deny)")


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    summary = find(event.get("tool_response"))
    if summary:
        print(json.dumps({"systemMessage": message(summary)}))


if __name__ == "__main__":
    main()
