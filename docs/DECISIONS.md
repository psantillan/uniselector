# Decisions

Each entry is a design question that came up, the answer, and the reason. The reason comes from
Ashby's theory or from an experiment. An entry that starts with ASSUMED has a number that no
measurement gave.

The words used here:

- **Decision**: one call of `decide(state, options)`. It gives a distribution over the options.
- **Walk**: the decisions that choose one operation and fill its input.
- **Main agent**: the agent that submits the plan. Its DataHandles principal is `planner`.
- **Sub-agent**: an agent with a fresh context that finds one value.

## D1. The gate: the orchestrator never holds an action

The brief asks for this: the orchestrator holds `graph.run` and not `graph.approve`. A test
showed that this does not gate an action in DataHandles 0.4 (`docs/GAPS.md`, G1 and G2).

Decision: the orchestrator's principal holds no action capability at all. The main agent's
principal grants it each capability that is not an action. An approved action runs as a one-node
graph of the main agent's principal: `graph.approve`, then `graph.run`. When a capability is
human-only and the human has a DataHandles principal, the human's principal gives the approval.

Reason: the gate must hold when the orchestrator code is wrong. With this shape the runtime
refuses the action, not the orchestrator. `tests/test_gate.py` shows it.

## D2. The budget is model tokens. The wall time is the time that the run works

Decision: the hard budget counts prompt and output tokens of the decision model and of the
sub-agents. The wall clock stops while the run waits for the main agent or for the human.

Reason: tokens are the one cost that each part of this system has, and the server reports them.
The clock stops for a human because the run cannot control how long a human takes; the limit is
there to stop a run that works without an end.

The burn rate in the state is the share of the budget spent against the share of intents done.

## D3. Each operation runs as a job

Decision: the orchestrator starts each operation with `background=True` and waits for it for one
wait interval (D10). If the job ends in that time, the result goes to the state. If not, the job
handle goes to the state, and the decision model chooses `wait`, `job.status`, `job.result` or
`job.cancel` at its next look.

Reason: a caller cannot cancel a synchronous operation (`docs/GAPS.md`, G5), so a synchronous
call can pass the wall-time limit. With one path for each operation, "waiting is a choice that the
decision model makes again each time it looks" holds for code, an API, and a sub-agent in the same
way. A stream gives its first event and stops; the decision model reads more with a new operation.

## D4. One letter for each option, and 20 options at most

Decision: an option gets a letter from A to T. The model gives one token. The distribution is the
probability of each letter in that token, made to sum to 1.

Reason: the Ollama server gives at most 20 `top_logprobs`. With 20 options or fewer, one call
sees the probability of each option. A set with more options is cut into pages: the last option of
a page is "none of these: show more options".

A root decision with more than 20 options is two decisions: the handle (or a control option),
then the operation on that handle.

## D5. When a decision abstains

Decision: a decision abstains when one of these is true:

- the top option has less than 0.5 of the probability (`abstain_top`)
- the second option has more than 0.5 of the probability of the top option (`abstain_ratio`):
  this is the rule for "the top two options are too close"
- less than 0.5 of the probability was on a letter (`abstain_mass`): the model did not answer
  the question

ASSUMED: the three numbers. 0.5 for the top is the point where the other options together are
more likely than the choice. The decision logs give the data to set them from outcomes later.

A decision about the reason for `wake_agent` or `abort` does not abstain: the run stops in any
case.

## D6. The walk

The walk follows the JSON input schema of the chosen capability. One schema node is one decision.

| Schema node | Decision |
| --- | --- |
| `const`, or an enum with one value | none: the value is fixed |
| enum | choose a value |
| boolean | choose true or false |
| `anyOf` / `oneOf` / a list of types that gives different shapes | choose the shape, then fill it |
| `{"$handle": ...}` | choose a live handle of the state |
| object | each required property in order; for each optional property, "include it" or "leave it out" |
| object with no properties | none: the value is `{}` |
| array | fill an item, then "add one more" or "stop" (no decision under `minItems`, and it stops at `maxItems`) |
| any other | an open value: choose from the candidate pool (D8) |

Each decision sees the input chosen so far. The finished input is checked against the schema
before the call.

An `anyOf` branch is shown as the input that it gives, for example
`{"column": "event_type", "op": "eq", "value": x} where x is a string or a number or a boolean`.
Reason: an experiment. With the branch shown as a type name ("one value" / "a list of values") the
model chose a list for `eq` with p 0.68 to 0.77. With the container shown, it chose one value for
`eq` with p 0.98, and a list for `in` with p 0.98.

## D7. Capabilities that are not options

`log.own` and `log.audit` are on each handle. They are not offered as operations
(`hidden_capabilities`).

Reason: they read the operation log of DataHandles, not the resource. They doubled the number of
root options and no intent of a plan needs them. A sub-agent can still use them.

## D8. The candidate pool: sources, order, and two strengths

The pool for an open value has these sources, in this order:

1. an answer that a sub-agent or the main agent gave for this field on this handle version
2. the schema default
3. a literal (a quoted string or a number) in the current intent
4. values observed on the target handle: its descriptor, and one bounded probe for each
   capability that reads, is synchronous, is idempotent, and needs no input
5. values observed on the other handles
6. words of the current intent, other answers, values in earlier results
7. literals and words in the other intents and in the notes

A value is **strong** when it is from source 1 to 3, or when it is observed at a place that the
field name or an earlier choice names. For the field `value` after `column = event_type`, the
values at `event_type` are strong. Each other value of the right type is **weak**.

The first page of the decision shows the strong values. "none of these: show more options" opens
the weak values. `none fits` is on the last page.

Reason: Ashby's law. The regulator needs enough variety to match the task, and each added option
is variety that it must reject. A constraint from the model of the system (which place a value
comes from) removes variety at no cost, so it applies first. An experiment showed the need: with
each candidate on one page, the model chose the word `"purchase"` of the intent as the column name
with p 0.66 to 0.97 (`docs/FAILURES.md`, F1). With the weak values on a later page it chose
`event_type` with p 0.99.

A candidate that is the name of a place shows up to four values at that place
(`Values at event_type: "purchase", "view", "cart"`). A probe output that is a table (`columns`
and `rows`) gives each cell with its column name as the place.

Probes are cached by (handle id, observed version). An answer is cached by (handle id, version,
capability, field path with the array index removed).

The pool has at most 19 candidates: 20 labels less `none fits`. ASSUMED: a value shows at most two
places, and a place shows at most four values.

Rejected by experiment: a decision in two stages (first the place, then the value). The model
gave 0.40 / 0.35 / 0.25 on the place.

## D9. The question, and its cap

A decision that abstains, or that chooses `none fits`, becomes a question with the fields of the
brief: plan, intent, capability, field, the input chosen so far, and the candidates.

The order of escalation: the decision model, a sub-agent, the main agent. A sub-agent that hits a
cap returns `needs_planner`.

ASSUMED: one question can use at most 40,000 tokens (`question_tokens`), and never more than the
budget that the run has left. 40,000 is about five full contexts of the sub-agent model (8,192
tokens). The failure log has the cost of each question, to set this number from data.

## D10. The wait interval

ASSUMED: one `wait` is 2 seconds (`wait_seconds`). It is also the time that the orchestrator waits
for a new job before it gives the job handle to the decision model (D3).

Reason: one decision of the local model takes about 1 second. A shorter wait than one decision
gives nothing.

## D11. The bounds of the state

ASSUMED: the state shows the last 5 results (`results_kept`), 600 characters of one value
(`value_chars`), and 8 handles: the start handles and the newest ones (`handles_kept`).

Reason: the state must fit in the context of a small model (8,192 tokens) with the options. The
brief says "the last few results, bounded".

The stall signal is the seconds since the last change and the number of looks with no change. A
change is a new result, or a new observed version of a shown handle.

## D12. The sub-agent is a small agent loop on the local model

Decision: `subagent.ModelAsker` runs a tool loop on the Ollama server with the DataHandles tools
(`handle_find`, `handle_describe`, `handle_invoke`) and two final tools: `answer` and
`needs_planner`. It runs as the principal `subagent`. The gate gives that principal only the
capabilities that do not write.

Reason: the run then needs one model server and no second product. The interface is one callable:
`asker(question, max_tokens, seconds)` gives `{"value", "reason", "calls", "tokens"}` or
`{"needs_planner", "calls", "tokens"}`. A headless coding agent with the DataHandles MCP server
fits the same interface.

## D13. The human channel

Decision: the main agent passes a request to the human with `plan_confirm(run, request, "human")`.
An approve from the main agent for a human-only capability also goes to the human. The run then
has the state `waiting_human`. The human decides with a command:

    uniselector-confirm RUN_DIRECTORY REQUEST approve

The command writes a decision file in the run directory. With `--url` it also gives
`graph.approve` as the human's principal.

Reason: MCP has no way for a server to reach the user. A hook shows the request
(`docs/HOOKS.md`), and a command outside the agent gives the decision, so the main agent cannot
give it.

## D14. The states of a run

`running`, `waiting_answer`, `waiting_confirm`, `waiting_human`, then one end state: `done`,
`woke`, `aborted`, `limit`, or `failed`.

`wake_agent` ends the run. The main agent makes a new plan and submits it with the handles of the
summary. Reason: this is the slow loop of Ashby's ultrastable system. A step-function changes the
configuration, and the fast loop starts again with the new one. The run does not keep a part of
the old plan.

When a run ends, the orchestrator cancels the jobs that still run, unless the state is `done`.

## D15. A value that failed is marked, not removed

When an operation fails with an error that is not retryable, each option that would give the same
value at the same field of the same capability and target shows
`(it was in an input that FAILED: ...)`.

Reason: the Homeostat's uniselector steps to a new configuration when the essential variable
leaves its limits. It does not step to the same one. The model repeated a failed input with p 0.98
before this mark (`docs/FAILURES.md`, F3). It is information for the decision model and not a
hard stop: the harness enforces only the two limits and the gate.

## D16. The logs

A run directory has:

- `decisions.jsonl`: one line for each decision node (the state, the partial input, the option
  keys and texts, the distribution, the choice, tokens, time). The last line is the outcome.
- `questions.jsonl`: one line for each question with its answer and the sub-agent's calls.
- `events.jsonl`: each change of state, each request, each wake.
- `summary.json` and `lineage.json`, written at the end.

## D17. The decision model

The first decision model is `ornith-1.5:9b` on the Ollama server that the DataHandles benchmarks
use. One decision is one output token with `temperature` 0, `seed` 0 and `think` off, which is
"a single pass". One decision takes 0.6 to 1.5 seconds and 400 to 1,000 tokens.

## D18. Lineage

The lineage of a run is the union of `Handle.graph()` of each handle that the run made, less the
job handles. It is a DataHandles graph: `Client.submit` runs it again. An approved action is in
the operation log of DataHandles and in `events.jsonl`, and it is not in the lineage graph: a
replay must not repeat an action.
