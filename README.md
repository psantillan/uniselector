# Uniselector

Uniselector watches the work of an agent, so that the agent does not monitor. The agent builds
objects on [DataHandles](https://github.com/psantillan/datahandles) handles: a filter, an
aggregate, a reading of a stream, a job. It states the essential variables: sentences that are true
while it can sleep. The objects then run with no model. A decision model judges only one thing: is
each essential variable inside its limits? When one is not, Uniselector wakes the agent with the
reason and the objects. The agent replaces the object that is broken, and it sleeps again.

It is an implementation of W. Ross Ashby's ultrastable system (*Design for a Brain*, 2nd ed. 1960,
chapter 7; *An Introduction to Cybernetics*, 1956). The name is the Homeostat's uniselector: the
stepping switch that chose a new configuration when a unit's essential variable left its limits.

## How it works

| Ashby | In this system |
| --- | --- |
| Environment | The resources behind the handles: a feed, a room, a service, a script |
| Reacting part R | The objects that the main agent built. DataHandles runs them. No model is in this loop |
| First loop | An object reads its handle again when the handle has a new version |
| Essential variables | The two hard limits, and the sentences that the main agent wrote |
| Second loop | The decision model looks at what the objects show, and judges each sentence |
| Step-function | A wake: the main agent replaces the object that is broken, and nothing else |
| Uniselector | The main agent at a wake: it selects the next configuration |
| The good regulator is a model of the system | The state: each object, its operation, its newest output |

A plan is the objects, the variables, optional notes, and optional lower limits:

    {"objects": [{"name": "totals", "handle": "h_...",
                  "operation": {"capability": "dataset.head", "input": {"n": 20}}}],
     "variables": ["The totals object shows a row for each region, with a number in total."]}

The main agent builds each handle with the DataHandles tools before it submits the plan. The plan
has no steps. The object name is the stable interface: at a wake, the main agent writes a new
handle or a new operation behind the same name.

The decision model looks when the output of an object changes. While nothing changes, it looks
after a silence, and the silence doubles after each look. One look is one request: the state, and
one yes-or-no question for each variable. A variable is outside its limits when the probability of
"yes" is below 0.5 (`docs/DECISIONS.md`, D31 and D32).

The harness enforces two things, because they must hold when a model is wrong:

1. **Hard limits**: a token budget and a wall time, from the configuration. A plan can ask for
   lower limits, never for higher ones.
2. **The action gate**: an action (a capability that writes, reaches outside, and is not
   idempotent) needs a confirmation. The DataHandles runtime enforces it: the orchestrator's
   principal never holds an action.

The first design of this repository put the decision model in the path of each operation. That was
wrong (`docs/FAILURES.md`, F46; `docs/DECISIONS.md`, D29). D1 to D28 and F1 to F45 are the record
of that design.

## Use

    pip install "uniselector @ git+https://github.com/psantillan/uniselector"
    cp uniselector.example.toml uniselector.toml     # state the two hard limits
    uniselector-mcp --config uniselector.toml

The MCP server has four tools:

- `plan_submit(plan)` starts a run and returns a run id.
- `plan_status(run, wait_seconds)` returns a bounded summary: the state, the variables, what each
  object shows now, a pending wake or confirmation, and what to do next. With `wait_seconds`, the
  call waits until the run needs the main agent.
- `plan_answer(run, question, value)` answers a wake. The value has the new version of an object,
  the names of objects to remove, a new list of variables, or `end`. Only what it names changes.
- `plan_confirm(run, request, decision)` approves an action, denies it, or passes it to the human.

A human decides with `uniselector-confirm`. `docs/HOOKS.md` has the hook that shows a pending
request to the user of Claude Code or Codex.

### The two models

- **The main agent** is the MCP client. Its model is a setting of that client, not of Uniselector.
- **The decision model** is in the `[decision]` section of the configuration: `decision_url`,
  `decision_model`, and `decision_key_env` (the name of the environment variable that has the API
  key; the key is in no file). A decision model is a model of its own class: it reads a state and
  typed questions, it gives one probability for each allowed answer, and it writes no text.
  Examples are TypeSafe Jev, Perplexity pplx-decider and Cloudflare Clef. A model behind another
  API replaces it as one callable: `Harness(cfg, decide=...)`.

The threshold 0.5 belongs to the decision model. Run the detector check (below) for a new model
before you use it.

## Measurements

Each row is one run of one task with a real main agent, on 2026-11-16 (UTC). The main agent was
Claude Code in headless mode (`claude-sonnet-5-5`) with only the DataHandles tools and the four
tools above. The decision model was `perplexity/pplx-decider-v1.2` on the Decisions API of
OpenRouter. The hard limits were 400,000 tokens and 1,800 s. `tasks/drive.py` runs a task, changes
the world on a schedule while the main agent works, and checks the answer against a truth that it
computes without a model. The rows are single runs, not a benchmark.

The tasks:

- **schema**: a feed of orders grows. The main agent keeps totals for each region. After 18 s
  (60 s feed) or 60 s (200 s feed), the column `amount` gets the name `amount_usd`.
- **shift**: the same feed. New orders get the status `settled` in place of `paid`.
- **silent**: two temperature sensors. The sensor in use stops, and the other one goes on.
- **goal**: switch a heater on, wait for 26 degrees, switch it off. The heater is an action.
- **goal-human**: the same, with the heater marked human-only.
- **code**: a script that runs for 90 s and prints a checksum.
- **api**: one request that takes 90 s.

| Task | Answer | Session time | Turns of the main agent | Main agent context, first turn to peak | Cost of the main agent | Looks | Wakes | Operations | Confirmations | Run tokens | Cost of the decision model |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| schema, 60 s feed | right | 83.1 s | 15 | 7,793 to 14,275 | $0.0929 | 19 | 2 | 38 | 0 | 11,105 | $0.000222 |
| schema, 200 s feed | right | 226.7 s | 18 | 7,793 to 16,122 | $0.0960 | 22 | 3 | 42 | 0 | 12,942 | $0.000259 |
| shift | right | 85.6 s | 17 | 7,817 to 16,153 | $0.1012 | 15 | 2 | 38 | 0 | 11,771 | $0.000235 |
| silent | right | 135.1 s | 12 | 7,827 to 14,465 | $0.0792 | 54 | 2 | 65 | 0 | 34,233 | $0.000685 |
| goal | right | 183.5 s | 11 | 7,737 to 10,947 | $0.0504 | 55 | 1 | 56 | 0 | 21,273 | $0.000425 |
| goal-human | right | 197.6 s | 14 | 7,839 to 14,494 | $0.0770 | 58 | 1 | 61 | 2, each one by the human | 25,648 | $0.000513 |
| code | right | 107.2 s | 6 | 7,720 to 9,863 | $0.0371 | 6 | 1 | 1 | 0 | 2,345 | $0.000047 |
| api | right | 103.1 s | 5 | 7,735 to 9,589 | $0.0339 | 6 | 1 | 1 | 0 | 2,143 | $0.000043 |

A look is one request to the decision model. An operation is one run of an object. The run tokens
are the spend against the token budget: the input tokens of the decision model. The two costs are
the costs that Claude Code and the Decisions API state. The evidence of each row is a directory in
`docs/evidence`, in the order of the table: `schema_20261116_162735`, `schema_20261116_163338`,
`shift_20261116_164133`, `silent_20261116_164259`, `goal_20261116_164515`,
`goal-human_20261116_164819`, `code_20261116_165137`, `api_20261116_165326`.

### Baseline: the main agent alone

`tasks/drive.py --direct` gives the main agent the same task and only the DataHandles tools. It
watches the feed itself. Each row is one run.

| Task | Mode | Answer | Session time | Turns of the main agent | Main agent context, first turn to peak | Cost of the main agent |
| --- | --- | --- | --- | --- | --- | --- |
| schema, 60 s feed | Uniselector | right | 83.1 s | 15 | 7,793 to 14,275 | $0.0929 |
| schema, 60 s feed | alone | right | 69.8 s | 31 | 5,852 to 13,204 | $0.1139 |
| schema, 200 s feed | Uniselector | right | 226.7 s | 18 | 7,793 to 16,122 | $0.0960 |
| schema, 200 s feed | alone | right | 225.3 s | 93 | 5,852 to 26,993 | $0.3889 |

- With Uniselector, the feed that is 3.3 times as long added 3 turns and $0.003 to the main agent.
  Alone, it added 62 turns and $0.275: the main agent made 88 `handle_invoke` calls, and each
  answer stayed in its context.
- On the 60 s feed the two modes are close. Alone, the main agent was 13 s faster and its context
  stayed smaller. In that baseline the feed also started 16 s earlier in the session (F48).
- Uniselector starts with about 1,900 more tokens of context: the text of its four tools.
- The evidence: `schema-direct_20261116_163205` and `schema-direct_20261116_163736`.
  `schema-direct_20261116_162916` is void: the driver did not start the feed (F48).
- No baseline was run for the other tasks of this design. A baseline of the first design did the
  goal task alone, with the same prompt and the same room: 183.3 s, 86 turns, context 5,796 to
  22,010, $0.323 (`stream-direct_20261116_003123`).

### Detector check

`tasks/probe.py` gives the decision model 12 small states and 24 sentences with a known answer: a
schema error, a row count over a limit, a new status value, a sensor that stopped, a goal that is
met, a job that ended, and the same states with no fault. The model was right on 24 of 24. It gave 0.9884 to 0.9995 to each
true sentence and 0.0004 to 0.0438 to each sentence that was not true. The check cost 4,244 tokens
($0.000084). The evidence: `docs/evidence/probe_20261116`. The states are small and the author of
the detector wrote them, so the check shows that the threshold separates the two cases. It is not
a measure of accuracy.

### What the rows do not show

- The decision model looks at each change of an output. An object that shows each new reading
  gave 54 to 58 looks in the three stream runs, and all but one or two of the looks in each run
  found nothing. At the 387 tokens for each look of the goal run, the budget of 400,000 tokens ends such a run
  after about 50 minutes (F49). An object whose output changes only when the work needs attention
  removes that cost. No run measured it.
- One wake had no fault: the main agent wrote a variable about columns that its object did not
  show (F47).
- The runs used the code before one change: a long output kept only its first part then. It keeps
  its first and its last part now. A new value in the middle of a long output still does not
  reach the decision model (F50).
- In the goal task with no human-only capability, the main agent switched the heater itself. The
  gate is in the path of the orchestrator only (F52).
- In the api task, the main agent gave its answer at the wake and did not end the run (F51).
- No run made the main agent compact its context, so no run tested a wake into a fresh context.
- `docs/FAILURES.md` has 52 entries. Each one has its evidence and says if it is open.

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
