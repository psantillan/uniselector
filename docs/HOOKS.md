# Hooks

A hook shows a pending human request to the user. The hook is one command, `uniselector-hook`
(`uniselector/hooks/pending.py`). It runs after each `plan_status` call of the main agent. It
reads the hook input on stdin and finds the summary of the run in the tool response. When the
summary has the state `waiting_human`, the hook prints one JSON object:

```json
{"systemMessage": "Uniselector: an action needs YOUR decision. room.heater on room with input {\"on\": true}. To decide, run: uniselector-confirm RUN_DIRECTORY req_1 approve   (or the same with deny)"}
```

In each other state the hook prints nothing.

The hook does not decide. The human decides with the command in the message. With a DataHandles
server behind a URL, that command needs the token of the human principal
(`UNISELECTOR_HUMAN_TOKEN`). The main agent and the hook do not have that token.

## Claude Code

Tested with two real sessions: `docs/evidence/stream-human_20261115_231150` and
`docs/evidence/stream-human_20261115_232558`.

Put this in a settings file (`.claude/settings.json` of the project, `~/.claude/settings.json`,
or a file that you give with `--settings`). The MCP server must have the name `uniselector`.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "mcp__uniselector__plan_status",
        "hooks": [{"type": "command", "command": "uniselector-hook"}]
      }
    ]
  }
}
```

### What the hook gets

Observed in `hook-input.jsonl` of each evidence directory. The input has these keys:

| Key | Value |
| --- | --- |
| `hook_event_name` | `PostToolUse` |
| `tool_name` | `mcp__uniselector__plan_status` |
| `tool_input` | The arguments of the call: `run` and `wait_seconds` |
| `tool_response` | A string: the JSON text of the full summary |
| `tool_use_id`, `duration_ms` | The id and the time of the call |
| `mcp_server` | `{"name": "uniselector", "source": "dynamic"}` |
| `session_id`, `transcript_path`, `cwd`, `prompt_id` | The session of the main agent |
| `permission_mode`, `effort` | The settings of the session |

### What the hook can see

- The full summary that the main agent got: the state, the plan, the pending request with its
  action, its input and its target, and the command for the human.
- The run id, so it can find the run directory.

### What the hook cannot see

- A request between two `plan_status` calls. The hook runs only when the main agent calls the
  tool. A request that goes to the human shows at the next call.
- The response of `plan_confirm`. The matcher names `plan_status` only, and the response of
  `plan_confirm` has no pending request in it.
- The looks of the decision model and the history of the operations. They are not in the summary.
- The token of the human.

### Where the message goes

In a session with `-p`, `--output-format stream-json` and `--include-hook-events`, the output of
the hook is in the event stream: a `system` event with the subtype `hook_response`, the hook name
`PostToolUse:mcp__uniselector__plan_status`, the exit code 0, and the JSON object in `stdout`.
The first evidence run has two such events. The first has an empty `stdout` (the state was
`waiting_confirm`). The second has the message (the state was `waiting_human`). The second
evidence run has 12 such events, and 5 have the message.

The Claude Code documentation says that `systemMessage` is a message for the user. This was
observed in a headless session only. It was not observed in the terminal interface.

The main agent also gets the command in the summary (`pending.command`), and it told the user in
its reply. So the user gets the request two times: one time from the hook, one time from the main
agent.

## Codex

Not tested. `codex exec` on this machine fails with HTTP 402 `deactivated_workspace`, so no Codex
session ran. The facts below are from the Codex hook documentation
(https://learn.chatgpt.com/docs/hooks), read on 2026-11-15 with `codex-cli 0.147.0`, where the
feature `hooks` is stable and on.

Put this in `~/.codex/hooks.json`, or in `.codex/hooks.json` of a trusted project. The same
structure can go in `[hooks]` tables of `config.toml`.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "mcp__uniselector__plan_status",
        "hooks": [{"type": "command", "command": "uniselector-hook"}]
      }
    ]
  }
}
```

A hook that is not managed must be reviewed and trusted one time with `/hooks`.

### What the documentation says the hook gets

- `PostToolUse` runs for MCP tools, with the tool name `mcp__uniselector__plan_status`. The
  `matcher` is a regular expression on the tool name.
- The input has `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `model`,
  `permission_mode`, `turn_id`, `tool_name`, `tool_use_id`, `tool_input` and `tool_response`.
  For an MCP tool, `tool_response` is the MCP call result. `uniselector-hook` looks for the
  summary in each shape: a string, an object, or a list of content blocks.
- `systemMessage` in the output shows as a warning in the interface or in the event stream.

### What the hook cannot do, from the documentation

- It cannot give the model more than about 2,500 tokens of output. The message of
  `uniselector-hook` is one sentence, so this limit has no effect.
- It does not run in a project that is not trusted.
- The limits of Claude Code above apply too: the hook runs only after a `plan_status` call.

The shape of `tool_response` for an MCP tool in Codex is the one fact that needs a live session.
If the shape is not one of the three above, `find` in `uniselector/hooks/pending.py` returns
nothing and the hook prints nothing.
