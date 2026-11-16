# Roadmap

Each version has a tag and a release. A row is "built" only when a test covers it and the mutation
check passes (`docs/DECISIONS.md`, D39 and D47). A row is "measured" only when a real run shows
it, and the row names the evidence directory in `docs/evidence`.

The order of the versions follows the rulings on the design. `docs/DECISIONS.md` has each ruling
in the user's words: D29 (0.2) and D40 (0.3). Version 0.4 follows the fourth ruling.

## v0.1: A decision tree in the path of each operation (built, then removed)

Goal: carry out a plan of intents ("X, then Y, then Z") with a decision model that chooses each
operation and each field of its input.

| Step | State | Evidence at the tag `v0.1.0` |
|---|---|---|
| 0. DataHandles ships its schemas as package data | Built | DataHandles pull request 1, and the pinned commit in `pyproject.toml` |
| 1. Repository, one CI job, the boundary test | Built | `tests/test_boundary.py` |
| 2. A plan is intents and handles | Built | `tests/test_parts.py` |
| 3. The loop, the control options, the hard limits | Built | `tests/test_run.py` |
| 4. The decision tree, the candidate pool, the sub-agent | Built | `tests/test_run.py`, `tests/test_parts.py` |
| The action gate in the protocol | Built | `tests/test_gate.py` |
| 5. The four MCP tools, the wake summary, the lineage export | Built | `tests/test_run.py`, `tests/test_parts.py` |
| 6. The hook for a pending human request | Built | `tests/test_parts.py`, `docs/HOOKS.md` |
| 7. Real task runs with a failure log | Measured | `docs/FAILURES.md`, F1 to F45 |

This design was wrong: the decision model was a second control layer on a sequence that needed no
reconfiguration (`docs/FAILURES.md`, F46). Version 0.2 removed the decision tree, the candidate
pool and the sub-agent. D1 to D28 and F1 to F45 are the record of this version.

## v0.2: The ultrastable system (built)

Goal: the main agent builds objects and states the essential variables. The objects run with no
model. The decision model only detects that a variable left its limits, and a wake is the
step-function.

| Item | State | Evidence |
|---|---|---|
| A plan is objects and variables | Built | `tests/test_parts.py`: a plan with one more key is refused |
| The first loop: an object runs again at a new version of its handle, with no model | Built | `tests/test_run.py`: an object follows its source with no model and no main agent |
| One look is one request with one question for each variable | Built | `tests/test_parts.py`: a server of the test stands in for the Decisions API |
| A look comes at a change, or after a silence that doubles | Built | `tests/test_run.py`: the silent feed |
| A wake parks the run, and the answer replaces only the object that it names | Built | `tests/test_run.py`: the schema break |
| The hard limits stop a run, and a plan can only lower them | Built | `tests/test_run.py`: the token budget, the wall time, the plan limits |
| An action waits for a confirmation and runs one time | Built | `tests/test_run.py`, `tests/test_gate.py` |
| A long value keeps its first and its last part | Built | `tests/test_parts.py` |
| Eight task runs with a real main agent, each answer right | Measured | `README.md`, the first table of "Measurements" |
| Two baselines of the main agent alone | Measured | `schema-direct_20261116_163205`, `schema-direct_20261116_163736` |
| The detector check: 24 of 24 statements | Measured | `probe_20261116` |

## v0.3: An object carries its runners (built)

Goal: the object that is broken says who repairs it. An object carries its check and an ordered
list of runners. The decision check that exists already triggers a change of the binding, on the
one wake path. No second decision model, no router.

| Item | State | Evidence |
|---|---|---|
| An object has `variables` (its check) and `runners` | Built | `tests/test_runners.py`: the runner of an object repairs it, and the main agent gets nothing |
| A statement has owners, and the runner of the first owner gets the wake | Built | same |
| A repair is on trial until the next look. A repair that does not hold binds the next runner for one wake | Built | `tests/test_runners.py`: local, then top, then local again |
| After the last runner, the wake comes to the main agent with what each runner tried | Built | `tests/test_runners.py` |
| A model runner reads with the DataHandles tools and writes one repair | Built | `tests/test_parts.py`: a server of the test stands in for the chat API |
| A model runner cannot write an action | Built | `tests/test_runners.py` |
| A confirmation is the same binding: `["main", "human"]`, or `["human"]` | Built | `tests/test_run.py`: the main agent binds the human for one decision |
| The principal that holds an action runs its graph | Built | `tests/test_gate.py`, and `tests/test_run.py`: the human runs an action with a token that the run does not have |
| A model runner works beside the loop: each other object runs, and the looks go on | Built | `tests/test_runners.py`: a variable with no object wakes the main agent while a runner works |
| A variable of the plan wakes the main agent at once, also while a runner works. After the answer it waits for the runner | Built | same, and: one look wakes the runner of the object and the main agent |
| A runner that finds no break gives the wake to the main agent (`to_main`) | Built | `tests/test_runners.py`, `tests/test_parts.py` |
| A busy decision model (HTTP 429, or 500 and more) makes no look, and the look stays due | Built | `tests/test_run.py`: a busy decision model; a look at a silence |
| A step that keeps the runner writes no rebind | Built | `tests/test_runners.py` |
| The version of the main agent stays when a runner ends after it | Built | `tests/test_runners.py` |
| The token budget holds while a runner works | Built | `tests/test_runners.py` |
| A wake of a model runner ends at a repair, at an answer with no call of a tool, or at a limit | Built | `tests/test_parts.py` |
| An object shows when its operation last ran | Built | `tests/test_run.py`: a quiet object shows that its source is live |
| The mutation check: 27 of 27, then 37 of 37 for the code of D48 to D52 | Built | `docs/DECISIONS.md`, D47 |
| A local model repairs a schema break while the main agent sleeps | Measured | `schema_20261116_184734`, `schema_20261116_192536` |
| A variable of the plan wakes the main agent while a runner works: a session of 101.2 s, and 533.8 s before | Measured | `schema_20261116_195950` |
| One runner alone repairs one broken object: two local models | Measured | `repair_20261116_184658`, `repair_20261116_181108` |
| The human holds an action alone, and the main agent cannot invoke it | Measured | `goal-held_20261116_190023` |
| The main agent passes one decision to the human | Measured | `goal-pass_20261116_190342` |
| The detector check: 32 of 32 statements | Measured | `probe_20261116_v4` |

## v0.4 and later

- **0.4: a runner is any callable.** A `[runners.NAME]` table can name a callable of the user's
  code, and a plan can name a callable that the main agent wrote. The core calls it with the wake
  and takes one repair. A model runner is one such callable.
- **0.4: a form of an output is the same binding.** An object can bind a runner that makes a form
  of its output: a summary, an embedding, a table. The form is a value or an ordinary handle. The
  core stores no vectors and has no engine. With no binding, the object has no such form. The
  check that exists judges the form, and a form that fails binds the next runner.
- An object keeps its version from before a trial, so that the next runner starts from the version
  that the main agent wrote (`docs/DECISIONS.md`, D52).
- A second human principal as a runner (a contractor).
- A task run that makes the main agent compact its context, to test a wake into a fresh context.
- A baseline of the main agent alone for each task.

## Known limits of the 0.3 code

- A model runner can replace an object that works, when its check is not true for another reason
  (`docs/FAILURES.md`, F60).
- A repair that the check of its object accepts can be wrong: a check rejects only what it states.
  One of three repairs of a local model left out a filter, and it held (F64).
- A wake of a model runner has no limit but `wake_tokens`, the token budget and the wall time. In
  two wakes, the runner `top` spent 1,090,476 tokens and gave no result (F57, F61).
- The decision model looks at each change of an output, so the looks grow with the feed (F49).
- A new value in the middle of a long output does not reach the decision model (F50).
- A run that the main agent leaves at a wake ends `left`, also when the answer was right (F51).
- The gate is in the path of the orchestrator. The main agent's principal can invoke an action
  that it holds (F52, and `docs/GAPS.md`, G1).
- The main agent decides what is a check of an object and what is a variable of the plan. Only a
  tool text guides it (D51, D52).
- Each measurement is a single run.
