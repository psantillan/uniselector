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
| A runner is silent while each variable is inside its limits: the same text and the same questions for the decision model, and no request of a runner | `tests/test_forms.py`: a binding adds nothing while each variable is inside its limits |
| The state shows how the handle of an object is made, so a check can state a filter | `tests/test_runners.py`: a replacement that drops a condition does not pass the check that names it |
| The look that judges a replacement by a runner has one more sentence: the handle is made by the same operations as before | `tests/test_runners.py`: only the look that judges a replacement has the sentence about it |
| A runner whose replacement does not hold leaves the list of the object. A replacement that holds keeps its runner | `tests/test_runners.py`: a runner whose replacement does not hold leaves the list; the main agent leaves the list |
| A check can state what the runners of an object can spend | `tests/test_runners.py` |
| After the last runner, the wake comes to the main agent with what each runner tried | `tests/test_runners.py` |
| A runner works beside the loop: each other object runs, and the looks go on | `tests/test_runners.py`: a variable with no object wakes the main agent while a runner works |
| A variable of the plan wakes the main agent at once, also while a runner works | same, and: one look wakes the runner of the object and the main agent |
| A runner that finds no break gives the wake to the main agent (`to_main`) | `tests/test_runners.py`, `tests/test_parts.py` |
| A model runner makes one request for one replacement. Its answer changes operations of the graph that made the handle | `tests/test_parts.py`: a real runtime, and a server of the test stands in for the chat API |
| A runner is any callable: a function of the configuration, or of a file that a plan names | `tests/test_runners.py`: a runner is any function of the user's code or of the agent's code |
| A plan can name code only when the configuration allows it | same |
| An object carries forms of its output. A runner makes a form only when a wake is due, and the wake has it | `tests/test_forms.py` |
| A form is a bounded value or a handle. The core stores no vectors | `tests/test_forms.py`: a function gives a handle, and the main agent reads it |
| The runner of a form that does not pass its check leaves the list. After the last one, the wake goes with no form | `tests/test_forms.py` |
| One look reads each form of a wake. A form that comes after the failure is over gives no wake | `tests/test_forms.py` |
| A replacement of a runner that does not hold leaves with its runner, and the object has its version from before. The next runner, or the main agent, reads what each runner tried | `tests/test_runners.py`: a runner whose replacement does not hold leaves the list; the main agent reads each replacement that left |
| The form `compaction`: its runner compacts the main agent when the check of the form is not true. While the check is true, no runner starts. A runner that does not help leaves, and the next one takes the form | `tests/test_forms.py`: a compaction runner works only when the context of the main agent is near its limit |
| No runner can write an action | `tests/test_runners.py` |
| An action waits for a confirmation and runs one time | `tests/test_run.py`, `tests/test_gate.py` |
| A confirmation is the same binding: `["main", "human"]`, or `["human"]` | `tests/test_run.py`: the main agent binds the human for one decision |
| The principal that holds an action runs its graph | `tests/test_gate.py`, and `tests/test_run.py`: the human runs an action with a token that the run does not have |
| The token budget holds while a runner works | `tests/test_runners.py` |
| An object shows when its operation last ran | `tests/test_run.py`: a quiet object shows that its source is live |
| The four MCP tools, the wake summary, the lineage export | `tests/test_run.py`, `tests/test_parts.py` |
| The hook for a pending human request | `tests/test_parts.py`, `docs/HOOKS.md` |
| `uniselector` imports only the SDK of DataHandles | `tests/test_boundary.py` |
| The mutation check: a test fails for each guard that a check removes (the newest check: 14 of 14) | `docs/DECISIONS.md`, D47 |

## Measured

| Item | Evidence |
|---|---|
| Eight task runs with a real main agent, each answer right | `docs/MEASUREMENTS.md`, the first table |
| The main agent alone on the schema task, with two lengths of the feed | `schema-direct_20261116_163205`, `schema-direct_20261116_163736` |
| A local model repairs a schema break with one request while the main agent sleeps: 6.0 s from the break to a good object. The main agent gets 1 wake and uses 11 turns, at 79% of the base | `schema_20261116_233942` |
| The same task with no runners: the main agent gets the wake of the break and repairs the object in 3 turns. It gets 2 wakes and uses 14 turns, at 90% of the base | `schema_20261117_004251` |
| One runner alone repairs one broken object with one request of 2,164 tokens, and keeps the filter. Before the break the runner makes no request | `repair_20261116_232124` |
| The check of an object judges each replacement by a runner, and the next runner in the list takes a wake that is still open | `repair_20261117_002143` (`docs/DECISIONS.md`, D61) |
| The human holds an action alone, and the main agent cannot invoke it | `goal-held_20261116_190023` |
| The main agent passes one decision to the human | `goal-pass_20261116_190342` |
| The detector check: 63 of 64 statements | `probe_20261117` |
| Two forms cost nothing while nothing fails: the same 4,597 tokens in 5 looks with the forms and with none. The wake with the forms comes 3.0 s later | `forms_20261116_233244` |
| A compaction runner compacts the main agent while it sleeps, before the break: the repair takes 8.0 s, against 23.6 s when the main agent compacts by itself in the middle of the repair | `compaction-binding/schema_20261117_035002` |
| With a normal window the compaction runner never starts, and the repair takes 8.8 s | `compaction-binding/schema_20261117_035217` |

## Next

- A live run where the main agent writes its own function runner (`plan_callables`).
- A task where the repair is frequent, or where the main agent must read much to make it.
- Each task run more than one time on the same code. Each row of the measurements is one run.
- A second human principal as a runner (a contractor). This changes the action gate.
- A task where the context of the main agent grows large, so that a compaction removes much of it.
- A baseline of the main agent alone for each task.
- A form from an embedding engine, and a search over it. The task of the forms uses a hash of words.

The known limits are in `docs/MEASUREMENTS.md`, and the failure log is `docs/FAILURES.md`.
