"""The time from the schema break to the first right total again, for each session of the schema task.

  python -m uniselector_tasks.react DIRECTORY

DIRECTORY holds the records of sessions (`drive schema --evidence DIRECTORY`). The schema task grades
only the last answer of the main agent, so a main agent that sleeps through the break passes. This
module reads how long the total was wrong. It prints one line of JSON for each session:

  notice_s   from the break to the first sign of it. For a run: its first event that is not a reading.
             For a main agent alone: the first result of a tool with an error or with the new name.
  repair_s   from the break to the first total that uses the new name and has no error
  feed_s     from the break to the end of the feed. A repair after this time came after the feed ended.

The times of a run are from its event log. The times of a main agent alone are from the time of each
event in its stream, and its schedule starts at its first operation (drive.py, FIRST_OPERATION). Each
time is exact to about 1 s.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

NEW = "amount_usd"   # the name of the column after the break (drive.py, change_the_world)
READINGS = ("object", "observation", "look", "answer", "end")


def alone(stream: list, after: float) -> tuple:
    """The two times of a main agent alone. `stream` is its events. `after` is the time from the start of the schedule to the break."""
    start, calls, notice, repair = None, {}, None, None
    for event in stream:
        content = event.get("message", {}).get("content")
        if "timestamp" not in event or not isinstance(content, list):
            continue
        t = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).timestamp()
        for part in content:
            if part.get("type") == "tool_use":
                calls[part["id"]] = part.get("input", {})
                if start is None and part["name"].endswith("handle_invoke"):
                    start = t
            if part.get("type") != "tool_result" or start is None or t <= start + after:
                continue
            text, made = json.dumps(part.get("content")), calls.get(part.get("tool_use_id"), {})
            wrong = bool(part.get("is_error")) or "ADAPTER_ERROR" in text
            if notice is None and (wrong or NEW in text):
                notice = t - start - after
            if repair is None and not wrong and made.get("capability") == "dataset.aggregate" and NEW in json.dumps(made):
                repair = t - start - after
    return notice, repair


def run(events: list, after: float) -> tuple:
    """The two times of a session with a run. `events` is the event log of the run."""
    start, notice, repair = events[0]["t"], None, None
    for event in events:
        if event["t"] <= start + after:
            continue
        if notice is None and event["event"] not in READINGS:
            notice = event["t"] - start - after
        if repair is None and event["event"] == "observation" and NEW in event.get("made", "") and "output" in event:
            repair = event["t"] - start - after
    return notice, repair


def session(directory: Path) -> dict | None:
    """The times of one session, or None for a record with no schema break."""
    record = json.loads((directory / "record.json").read_text())
    at = {change["what"]: change["agent_s"] for change in record.get("facts", {}).get("changes", [])}
    if "schema" not in at:
        return None
    after = at["schema"] - at["the schedule started"]
    if record["direct"]:
        notice, repair = alone([json.loads(line) for line in (directory / "agent-stream.jsonl").read_text().splitlines() if line.startswith("{")], after)
    else:
        notice, repair = run([json.loads(line) for file in sorted(directory.glob("runs/*/events.jsonl")) for line in file.read_text().splitlines()], after)
    return {"session": directory.name, "direct": record["direct"], "clock": record.get("clock", False), "runners": sorted(record.get("runners", {})),
            "notice_s": None if notice is None else round(notice, 1), "repair_s": None if repair is None else round(repair, 1),
            "feed_s": round(at["the feed ended"] - at["schema"], 1)}


def main() -> None:
    p = argparse.ArgumentParser(description="The time from the schema break to the first right total again, for each session.")
    p.add_argument("directory", help="a directory with the records of sessions of the schema task")
    a = p.parse_args()
    for directory in sorted(Path(a.directory).iterdir()):
        if (directory / "record.json").exists() and (times := session(directory)):
            print(json.dumps(times))


if __name__ == "__main__":
    main()
