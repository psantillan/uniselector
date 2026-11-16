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
    cp uniselector.example.toml uniselector.toml     # state the two hard limits and the two models
    uniselector-mcp --config uniselector.toml

The server reads the key of the Decisions API from the environment variable `OPENROUTER_API_KEY`.
The key is in no file.

The MCP server has four tools:

- `plan_submit(plan)` starts a run and returns a run id.
- `plan_status(run)` returns a bounded summary: the state, the plan with the current intent, a
  pending question or confirmation, the handles, the last results, and what to do next.
- `plan_answer(run, question, value)` answers a question that a sub-agent could not.
- `plan_confirm(run, request, decision)` approves an action, denies it, or passes it to the human.

A human decides with `uniselector-confirm`. `docs/HOOKS.md` has the hook that shows a pending
request to the user of Claude Code or Codex.

The decision model is a model of its own class. It reads a state and typed questions, it gives one
probability for each allowed answer, and it writes no text. Examples are TypeSafe Jev, Perplexity
pplx-decider and Cloudflare Clef. One decision is one `choice` question to a Decisions API: the
options of the decision are the options of the question. `docs/DECISIONS.md` (D17) has the model
and the reasons. Another decision model replaces it with one setting, or as one callable:
`decide(state, options)`.

The sub-agent is a different model: a chat model with tool calls on an Ollama server. A decision
model cannot explore, because it writes nothing.

## Measurements

Each row is one run of one task with a real main agent, on 2026-11-15 and 2026-11-16 (UTC). The
main agent was Claude Code in headless mode (`claude-sonnet-5-5`) with only the DataHandles tools
and the four tools above. The decision model was `perplexity/pplx-decider-v1.2` on the Decisions
API of OpenRouter. The sub-agent model was `ornith-1.5:9b` on an Ollama server. The hard limits
were 400,000 tokens and 1,800 s. `tasks/drive.py` runs a task and checks the answer against a
truth that it computes without a model. The rows are single runs, not a benchmark.

| Task | Answer | Run time | Run tokens | Of these, decision model | Decisions | Operations | Questions to the sub-agent | Confirmations | Main agent context, first turn to peak |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Data pipeline: filter, join, aggregate, top 3 rows | right | 116.3 s | 116,159 | 58,930 | 38 | 6 | 2 | 0 | 7,016 to 11,993 |
| Stream: heater on, wait for 26 degrees, heater off | right | 218.1 s | 141,633 | 51,375 | 49 | 2 | 2, and the 2 went on to the main agent | 2 | 7,003 to 14,987 |
| The same stream task, with the action marked human-only | right | 262.6 s | 121,265 | 44,636 | 44 | 3 | 2, and 1 went on to the main agent | 2, each one by the human | 7,063 to 16,823 |
| Slow API: a request that takes 150 s | right | 154.6 s | 4,831 | 4,831 | 6 | 2 | 0 | 0 | 7,001 to 9,382 |
| Slow code: a script that takes 150 s | right | 153.8 s | 3,014 | 3,014 | 4 | 2 | 0 | 0 | 6,986 to 9,415 |
| Sub-agent: a helper agent answers a question | right | 39.4 s | 2,760 | 2,760 | 4 | 2 | 0 | 0 | 7,016 to 8,403 |

The run time and the run tokens are the spend against the two hard limits. The clock of a run
stops while a request waits for a confirmation. No run chose `wake_agent`. The evidence of each
row is a directory in `docs/evidence`, in the order of the table: `pipeline_20261115_235850`,
`stream_20261116_000054`, `stream-human_20261116_000825`, `api_20261116_000457`,
`code_20261115_235541`, `helper_20261116_000738`.

The decision model made 145 decisions in the six runs. The median time of one decision was 402 ms
(the longest: 2,055 ms). One decision read 496 to 3,252 input tokens (median 1,105). The API
stated a price of $0.0033 for the 145 decisions together.

### Baseline: the main agent alone

`tasks/drive.py --direct` gives the main agent the same task and only the DataHandles tools. It
does each operation itself. Each row is one run.

| Task | Mode | Answer | Time | Turns of the main agent | Main agent context, first turn to peak | Cost of the main agent |
| --- | --- | --- | --- | --- | --- | --- |
| Data pipeline | Uniselector | right | 123.9 s | 5 | 7,016 to 11,993 | $0.047 |
| Data pipeline | alone | right | 19.9 s | 13 | 6,971 to 11,849 | $0.063 |
| Stream | Uniselector | right | 242.0 s | 12 | 7,003 to 14,987 | $0.075 |
| Stream | alone | right | 183.3 s | 86 | 5,796 to 22,010 | $0.323 |

The time is the time of the session of the main agent. The cost is the cost that Claude Code
states for the session. It does not have the spend of the run (the table above).

- The pipeline task has 6 operations. Uniselector gives no benefit there: the context of the main
  agent grows by the same 4,900 tokens, and the task takes six times as long.
- The stream task is a wait. Alone, the main agent asked for the newest reading 81 times, and each
  answer stayed in its context. With Uniselector it used 12 turns, half of the added context, and
  less than a quarter of the cost. Alone, the context grows with the length of the wait.
- The evidence: `pipeline-direct_20261116_002722` and `stream-direct_20261116_003123`.
  `stream-direct_20261116_002748` is not a baseline: the main agent still had the Uniselector
  tools, and after three readings it handed the wait off (F45).

What the rows do not show:

- A question to the sub-agent is the largest cost. In the three runs with questions, the sub-agent
  used 57,229, 90,258 and 76,629 tokens: more than the decision model in two of the three. Three
  of the six questions reached the cap of a question and went to the main agent (F42, F43, F44).
- The decision model abstains when two options fit the intent (F42) and when an intent needs no
  operation (F43). The rule for two close options cannot tell these cases from a real doubt.
- In the stream run, 40 of the 49 decisions chose `wait` (F30).
- In the three runs with a slow job, the run made no decision while the job ran (F31).
- The first build had a chat model behind `decide`, not a decision model (F39). Its six runs are
  in `docs/evidence` too. With the same tasks, the pipeline run of that build took 436.0 s,
  324,769 tokens and 124 decisions. The plans were not the same: the main agent writes its plan
  in each run.
- `docs/FAILURES.md` has 45 failures. Each one has its evidence and says if it is open.

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

The tests need no model and no key. A decider that follows a script stands in for the decision
model, and a server of the test stands in for the Decisions API. `tests/test_boundary.py`
fails if `uniselector` imports `datahandles.runtime` or `datahandles.core`.

MIT license.
