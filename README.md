# Uniselector

Uniselector carries out a long task for an agent while the agent's context stays small. The agent
states its intent as a short plan: "X, then Y, then Z". A decision model carries out each step with
operations on [DataHandles](https://github.com/psantillan/datahandles) handles. The agent sees
nothing until the work is done, an action needs its confirmation, or the decision model gives up.

It is an implementation of W. Ross Ashby's regulator (*Design for a Brain*, 1952; *An Introduction
to Cybernetics*, 1956). The name is the Homeostat's uniselector: the stepping switch that chose a
new configuration when a unit's essential variable left its limits.

## How it works

A plan is a list of intents (sentences), the handle ids to start from, optional notes, and
optional lower limits. It has no operations.

    for each intent:
        loop:
            walk one tree of single-pass decisions: the operation, then each field of its input
            a control option (step_done, wait, wake_agent, abort): handle it
            an action: request a confirmation, and continue when it is given
            invoke the operation, and add a bounded summary of the result to the state

There is one primitive: `decide(state, options)` gives a distribution over a set of options that
differs at each call. The first decision of a walk chooses among each capability of the current
handles and the control options. The walk then follows the JSON input schema of the capability: one
schema node is one decision. An open value is a choice from a candidate pool that is collected
from the state. When the pool is not enough, a sub-agent with a fresh context finds the value.
What the sub-agent cannot settle goes to the main agent.

The harness enforces two things, because they must hold when the model is wrong:

1. **Hard limits**: a token budget and a wall time, from the configuration.
2. **The action gate**: an action (a capability that writes, reaches outside, and is not
   idempotent) needs a confirmation. The DataHandles runtime enforces it: the orchestrator's
   principal never holds an action.

Each other rule is information in the decision model's state.

| Ashby | In this system |
| --- | --- |
| Regulator | The decision model |
| Essential variables | The hard limits, and whether the current intent is met |
| Requisite variety | The options at each decision. A sub-agent, then the main agent, then the human add variety at a higher cost |
| Fast loop | Walk, invoke, observe |
| Slow loop (step-function) | `wake_agent`: the main agent makes a new plan |
| Amplification | One plan drives many operations |
| Attenuation | Handles and bounded summaries |
| The good regulator is a model of the system | The state of the decision model is the model of the run |

## Use

    pip install "uniselector @ git+https://github.com/psantillan/uniselector"
    cp uniselector.example.toml uniselector.toml     # state the two hard limits and the model server
    uniselector-mcp --config uniselector.toml

The MCP server has four tools:

- `plan_submit(plan)` starts a run and returns a run id.
- `plan_status(run)` returns a bounded summary: the state, the plan with the current intent, a
  pending question or confirmation, the handles, the last results, and what to do next.
- `plan_answer(run, question, value)` answers a question that a sub-agent could not.
- `plan_confirm(run, request, decision)` approves an action, denies it, or passes it to the human.

A human decides with `uniselector-confirm`. `docs/HOOKS.md` has the hook that shows a pending
request to the user of Claude Code or Codex.

The decision model is a model on an Ollama server that gives log probabilities. A trained decision
model can replace it: it is one callable, `decide(state, options)`.

## Measurements

Each row is one run of one task with a real main agent, on 2026-11-15. The main agent was Claude
Code in headless mode (`claude-sonnet-5-5`) with only the DataHandles tools and the four tools
above. The decision model and the sub-agent were `ornith-1.5:9b` on an Ollama server. The hard
limits were 400,000 tokens and 1,800 s. `tasks/drive.py` runs a task and checks the answer against
a truth that it computes without a model. The rows are single runs, not a benchmark.

| Task | Answer | Run time | Run tokens | Decisions | Operations | Questions to the sub-agent | Confirmations | Main agent context, first turn to peak |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Data pipeline: filter, join, aggregate, top row | right | 436.0 s | 324,769 | 124 | 12 | 6 | 0 | 7,016 to 17,339 |
| Stream: heater on, wait for 26 degrees, heater off | right | 181.7 s | 57,301 | 57 | 3 | 0 | 2 | 7,003 to 12,795 |
| The same stream task, with the action marked human-only | right | 163.4 s | 53,838 | 56 | 6 | 0 | 6, each one by the human | 7,063 to 25,067 |
| Slow API: a request that takes 150 s | right | 277.7 s | 32,351 | 44 | 2 | 0 | 0 | 7,001 to 9,392 |
| Slow code: a script that takes 150 s | right | 181.3 s | 12,905 | 5 | 2 | 1 | 0 | 6,986 to 9,443 |
| Sub-agent: a helper agent answers a question | right | 105.2 s | 9,797 | 3 | 1 | 1 | 0 | 7,016 to 8,568 |

The run time and the run tokens are the spend against the two hard limits. The clock of a run
stops while a request waits for a confirmation. No run chose `wake_agent`, and no question went
to the main agent. The evidence of each row is a directory in `docs/evidence`, in the order of the
table: `pipeline_20261115_223625`, `stream_20261115_230519`, `stream-human_20261115_232558`,
`api_20261115_232915`, `code_20261115_225936`, `helper_20261115_231211`.

What the rows do not show:

- Three earlier runs did not end this way: `pipeline_20261115_222110` (no answer),
  `api_20261115_230837` (the main agent left the run) and `stream-human_20261115_231150` (the
  session ended at the first human request). The fixes are in `docs/FAILURES.md`.
- The pipeline run used 81% of the token budget. The 6 questions to the sub-agent used 150,359
  of its 324,769 tokens.
- In the stream run, 87% of the tokens went to `wait` decisions (F30).
- In the human-only run, the human got the same "heater on" request five times (F37).
- `docs/FAILURES.md` has 38 failures. Each one has its evidence and says if it is open.

## Documents

- `docs/DECISIONS.md`: each design decision and its reason.
- `docs/GAPS.md`: what the DataHandles protocol lacks, and the workaround for each gap.
- `docs/FAILURES.md`: each failure of a real run, the part that failed, and the evidence.
- `docs/HOOKS.md`: the hooks, and what each tool's hooks can and cannot see.

## Development

    python -m venv .venv
    .venv/bin/pip install -e ".[dev]"
    .venv/bin/pip install -e ../datahandles     # optional: a local DataHandles checkout over the pinned commit
    .venv/bin/pytest tests -q

The tests need no model: a decider that follows a script stands in for it. `tests/test_boundary.py`
fails if `uniselector` imports `datahandles.runtime` or `datahandles.core`.

MIT license.
