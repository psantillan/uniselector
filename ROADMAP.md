# Roadmap

A row is "built" only when a test covers it and the mutation check passes (`docs/DECISIONS.md`, D39
and D47). A row is "measured" only when a real run shows it, and the row names the evidence
directory in `docs/evidence`. Each measurement is a single run.

## Built

| Item | Evidence |
|---|---|
| A plan is objects and variables | `tests/test_parts.py`: a plan with one more key is refused |
| The first loop: an object runs again at a new version of its handle, with no model | `tests/test_run.py`: an object follows its source with no model and no main agent |
| One look is one request with one question for each variable | `tests/test_parts.py`: a server of the test stands in for the Decisions API |
| A look comes at a change, or after a silence that doubles | `tests/test_run.py`: the silent feed |
| A busy decision model (HTTP 429, or 500 and more) makes no look, and the look stays due | `tests/test_run.py`: a busy decision model; a look at a silence |
| A wake parks the run, and the answer replaces only the object that it names | `tests/test_run.py`: the schema break |
| The hard limits stop a run, and a plan can only lower them | `tests/test_run.py`: the token budget, the wall time, the plan limits |
| A long value keeps its first and its last part | `tests/test_parts.py` |
| An object has `variables` (its check) and `runners` | `tests/test_runners.py`: the runner of an object repairs it, and the main agent gets nothing |
| A statement has owners, and the runner of the first owner gets the wake | same |
| A repair is on trial until the next look. A repair that does not hold binds the next runner for one wake | `tests/test_runners.py`: local, then top, then local again |
| After the last runner, the wake comes to the main agent with what each runner tried | `tests/test_runners.py` |
| A runner works beside the loop: each other object runs, and the looks go on | `tests/test_runners.py`: a variable with no object wakes the main agent while a runner works |
| A variable of the plan wakes the main agent at once, also while a runner works | same, and: one look wakes the runner of the object and the main agent |
| A runner that finds no break gives the wake to the main agent (`to_main`) | `tests/test_runners.py`, `tests/test_parts.py` |
| A model runner reads with the DataHandles tools and writes one repair | `tests/test_parts.py`: a server of the test stands in for the chat API |
| A runner is any callable: a function of the configuration, or of a file that a plan names | `tests/test_runners.py`: a runner is any function of the user's code or of the agent's code |
| A plan can name code only when the configuration allows it | same |
| An object carries forms of its output, and a runner makes each form again at each new output | `tests/test_forms.py` |
| A form is a bounded value or a handle. The core stores no vectors | `tests/test_forms.py`: a function gives a handle, and the main agent reads it |
| A form that does not pass its check binds the next runner. After the last one, the main agent gets one wake | `tests/test_forms.py` |
| The end of a run waits for a form of the newest output | `tests/test_forms.py` |
| No runner can write an action | `tests/test_runners.py` |
| An action waits for a confirmation and runs one time | `tests/test_run.py`, `tests/test_gate.py` |
| A confirmation is the same binding: `["main", "human"]`, or `["human"]` | `tests/test_run.py`: the main agent binds the human for one decision |
| The principal that holds an action runs its graph | `tests/test_gate.py`, and `tests/test_run.py`: the human runs an action with a token that the run does not have |
| The token budget holds while a runner works | `tests/test_runners.py` |
| An object shows when its operation last ran | `tests/test_run.py`: a quiet object shows that its source is live |
| The four MCP tools, the wake summary, the lineage export | `tests/test_run.py`, `tests/test_parts.py` |
| The hook for a pending human request | `tests/test_parts.py`, `docs/HOOKS.md` |
| `uniselector` imports only the SDK of DataHandles | `tests/test_boundary.py` |
| The mutation check: a test fails for each guard that a check removes (the newest check: 33 of 33) | `docs/DECISIONS.md`, D47 |

## Measured

| Item | Evidence |
|---|---|
| Eight task runs with a real main agent, each answer right | `README.md`, the first table of "Measurements" |
| The main agent alone on the schema task, with two lengths of the feed | `schema-direct_20261116_163205`, `schema-direct_20261116_163736` |
| A local model repairs a schema break while the main agent sleeps | `schema_20261116_184734`, `schema_20261116_192536` |
| A variable of the plan wakes the main agent while a runner works | `schema_20261116_195950` |
| One runner alone repairs one broken object: two local models | `repair_20261116_184658`, `repair_20261116_181108` |
| The human holds an action alone, and the main agent cannot invoke it | `goal-held_20261116_190023` |
| The main agent passes one decision to the human | `goal-pass_20261116_190342` |
| The detector check: 40 of 40 statements | `probe_20261116_v5` |
| A summary form brings a new value in the middle of a long output to the decision model: one wake with the form, none with no form | `forms_20261116_214113` |

## Next

- An object keeps its version from before a trial, so that the next runner starts from the version
  that the main agent wrote (`docs/DECISIONS.md`, D52).
- A second human principal as a runner (a contractor). This changes the action gate.
- A task run that makes the main agent compact its context, to test a wake into a fresh context.
- A baseline of the main agent alone for each task.
- A form from an embedding engine, and a search over it. The task of the forms uses a hash of words.

## Known limits

- Model runners are not yet a gain. On the 60 s schema task, a session with model runners took
  22% more time and cost 42% more than a session with none (`README.md`, "Model runners").
- A model runner can replace an object that works, when its check is not true for another reason
  (`docs/FAILURES.md`, F60).
- A repair that the check of its object accepts can be wrong: a check rejects only what it states.
  One of three repairs of a local model left out a filter, and it held (F64).
- A wake of a model runner has no limit but `wake_tokens`, the token budget and the wall time. In
  two wakes, the runner `top` spent 1,090,476 tokens and gave no result (F57, F61).
- The run cannot stop a function runner that does not return (D53).
- Code that a plan names runs in the process of Uniselector. The action gate is a rule of the
  protocol. It is not a sandbox (D53).
- After a repair that does not hold, the next runner gets the version of that repair, not the
  version that the main agent wrote (D52).
- The decision model looks at each change of an output, so the looks grow with the feed (F49).
  Each form that a run takes adds one look (D54).
- A new value in the middle of a long output reaches the decision model only through a form (F50,
  D54).
- The decision model cannot compare a form with the part of a long output that the state does
  not keep. A summary that leaves out a value passes its check (D54).
- A run that the main agent leaves at a wake ends `left`, also when the answer was right (F51).
- The gate is in the path of the orchestrator. The main agent's principal can invoke an action
  that it holds (F52, and `docs/GAPS.md`, G1).
- The main agent decides what is a check of an object and what is a variable of the plan. Only a
  tool text guides it (D51, D52).
- No run made the main agent compact its context.
