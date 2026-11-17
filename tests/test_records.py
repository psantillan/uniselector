"""The records of a session of a task: its time to repair, and the stream of the main agent without the items of the machine."""
import json

from uniselector_tasks.drive import public
from uniselector_tasks.react import session

CHANGES = [{"what": "the schedule started", "agent_s": 10.0}, {"what": "schema", "agent_s": 20.0}, {"what": "the feed ended", "agent_s": 60.0}]


def record(directory, direct: bool) -> None:
    directory.mkdir()
    (directory / "record.json").write_text(json.dumps({"direct": direct, "runners": {}, "facts": {"changes": CHANGES}}))


def test_the_time_to_repair_of_a_run_is_from_the_break_to_its_first_good_reading_with_the_new_name(tmp_path):
    record(tmp_path / "s", direct=False)
    events = [{"t": 100.0, "event": "object"},
              {"t": 109.0, "event": "observation", "made": "dataset.aggregate amount", "output": "1"},
              {"t": 111.0, "event": "observation", "made": "dataset.aggregate amount", "error": "no such column"},
              {"t": 112.5, "event": "woke"},
              {"t": 115.0, "event": "observation", "made": "dataset.aggregate amount_usd", "error": "not ready"},
              {"t": 119.0, "event": "observation", "made": "dataset.aggregate amount_usd", "output": "2"}]
    (tmp_path / "s" / "runs" / "r").mkdir(parents=True)
    (tmp_path / "s" / "runs" / "r" / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    assert session(tmp_path / "s") == {"session": "s", "direct": False, "clock": False, "runners": [], "notice_s": 2.5, "repair_s": 9.0, "feed_s": 40.0}


def test_the_time_to_repair_of_a_main_agent_alone_is_from_the_break_to_its_first_good_total_with_the_new_name(tmp_path):
    record(tmp_path / "s", direct=True)

    def call(second: int, id: str, input: dict) -> dict:
        return {"type": "assistant", "timestamp": f"2026-01-01T00:00:{second:02d}.000Z",
                "message": {"content": [{"type": "tool_use", "id": id, "name": "mcp__datahandles__handle_invoke", "input": input}]}}

    def result(second: int, id: str, content: str, error: bool = False) -> dict:
        return {"type": "user", "timestamp": f"2026-01-01T00:00:{second:02d}.000Z",
                "message": {"content": [{"type": "tool_result", "tool_use_id": id, "content": content, "is_error": error}]}}

    old, new = ({"capability": "dataset.aggregate", "input": {"metrics": [{"column": name}]}} for name in ("amount", "amount_usd"))
    stream = [call(0, "a", old), result(1, "a", "total 5"),          # the schedule starts at the first operation; the break comes 10 s later
              call(11, "b", old), result(12, "b", "no such column", error=True),
              call(13, "c", {"capability": "dataset.schema"}), result(14, "c", "columns: amount_usd"),
              call(15, "d", new), result(17, "d", "total 9")]
    (tmp_path / "s" / "agent-stream.jsonl").write_text("\n".join(json.dumps(e) for e in stream))
    assert session(tmp_path / "s") == {"session": "s", "direct": True, "clock": False, "runners": [], "notice_s": 2.0, "repair_s": 7.0, "feed_s": 40.0}


def test_a_record_with_no_schema_break_has_no_times(tmp_path):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "record.json").write_text(json.dumps({"direct": False, "facts": {"truth": []}}))
    assert session(tmp_path / "s") is None


def test_the_stream_of_a_record_has_no_first_event_and_no_text_of_a_compaction_summary(tmp_path):
    stream = [{"type": "system", "subtype": "init", "tools": ["x"]},
              {"type": "user", "message": {"content": [{"type": "text", "text": "the task"}]}},
              {"type": "system", "subtype": "compact_boundary", "compact_metadata": {"pre_tokens": 9}},
              {"type": "user", "isSynthetic": True, "message": {"content": [{"type": "text", "text": "private instructions"}]}},
              {"type": "user", "message": {"content": [{"type": "tool_result", "content": "kept"}]}}]
    (tmp_path / "stream.jsonl").write_text("\n".join(json.dumps(e, separators=(",", ":")) for e in stream))
    kept = public(tmp_path / "stream.jsonl")
    assert "private" not in kept and "init" not in kept
    events = [json.loads(line) for line in kept.splitlines()]
    assert [e.get("subtype") for e in events] == [None, "compact_boundary", None, None]
    assert events[0]["message"]["content"][0]["text"] == "the task" and events[3]["message"]["content"][0]["content"] == "kept"
    assert events[2]["message"]["content"] == "[the summary of the compaction: 20 characters. The text is not in this record]"
