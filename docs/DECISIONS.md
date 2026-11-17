# Decisions

Each entry is a design question that came up, the answer, and the reason. The reason comes from
Ashby's theory or from an experiment. An entry that starts with ASSUMED has a number that no
measurement gave.

The design changed one time, on 2026-11-16 (D29). D1 to D28 are from the first design: a decision
model that chose each operation of a plan. Each entry of the first design has a status line. An
entry with the status "Superseded" stays as the record of that design, and its code is gone.

The words used here:

- **Main agent**: the generative agent that builds the objects and submits the plan. Its
  DataHandles principal is `planner`.
- **Object**: a name, a handle, and one operation that reads a small value from the handle. The
  main agent builds it. It runs with no model.
- **Essential variable** (short: variable): a sentence that is true while the work is viable.
- **Decision model**: a model that reads a state and typed questions and gives probabilities. It
  writes no text (D17).
- **Look**: one call of `decide(state, statements)`. It gives one probability for each variable.
- **Wake**: the run gives the main agent the variables that are outside their limits, and waits
  for its answer.
- **Tick**: one pass of the loop over the objects (`wait_seconds`).
- **The brief**: the text of the author of the project that started the work. It names the theory
  and the task. It is not in the repository.
- **Ruling**: a later direction of the author that changed the design. "The user" in an entry is
  the author.

Words of the first design, in D1 to D28 only: a **decision** was one call of `decide(state,
options)`; the **walk** was the decisions that chose one operation and filled its input; a
**sub-agent** was an agent with a fresh context that found one value.

## D1. The gate: the orchestrator never holds an action

Status after D29: Holds.

The brief asks for this: the orchestrator holds `graph.run` and not `graph.approve`. A test
showed that this does not gate an action in DataHandles 0.4 (`docs/GAPS.md`, G1 and G2).

Decision: the orchestrator's principal holds no action capability at all. The main agent's
principal grants it each capability that is not an action. An approved action runs as a one-node
graph of the main agent's principal: `graph.approve`, then `graph.run`. When a capability is
human-only and the human has a DataHandles principal, the human's principal gives the approval.

Reason: the gate must hold when the orchestrator code is wrong. With this shape the runtime
refuses the action, not the orchestrator. `tests/test_gate.py` shows it.

## D2. The budget is model tokens. The wall time is the time that the run works

Status after D29: Changed by D36: the budget counts the decision model only, and the state has no burn rate.

Decision: the hard budget counts the input tokens of the decision model, and the prompt and output
tokens of the sub-agents. A decision model has no output tokens. The wall clock stops while the
run waits for the main agent or for the human.

Reason: tokens are the one cost that each part of this system has, and the server reports them.
The clock stops for a human because the run cannot control how long a human takes; the limit is
there to stop a run that works without an end.

The burn rate in the state is the share of the budget spent against the share of intents done.

## D3. Each operation runs as a job

Status after D29: Changed by D35: the operation of each object runs as a job. The decision model does not choose `wait`.

Decision: the orchestrator starts each operation with `background=True` and waits for it for one
wait interval (D10). If the job ends in that time, the result goes to the state. If not, the job
handle goes to the state, and the decision model chooses `wait`, `job.status`, `job.result` or
`job.cancel` at its next look.

Reason: a caller cannot cancel a synchronous operation (`docs/GAPS.md`, G5), so a synchronous
call can pass the wall-time limit. With one path for each operation, "waiting is a choice that the
decision model makes again each time it looks" holds for code, an API, and a sub-agent in the same
way. A stream gives its first event and stops; the decision model reads more with a new operation.

## D4. One `choice` question for each decision, and 255 options at most

Status after D29: Superseded by D29.

Decision: one decision is one `choice` question to the decision model. The request has the state
as text, the rules and the decision as the instructions of the question, and the options as the
`criteria` of the question. The name of an option is its number ("1", "2", and so on). The
description of the option is its text. The distribution is the probability that the model gives
for each name.

Reason: a decision model gives one probability for each allowed option (D17). So one call sees
the probability of each option, and the orchestrator reads no text back. The API takes the options
as a map from a name to a description. The text of an option is long, so it is the description,
and the number keeps each name short and different. The API can give the probabilities in a
different order from the request, so `Decider` reads them by name. A test shows this.

A `choice` question has 255 options at most. This is the limit of the Decisions API. A set with
more options is cut into pages: the last option of a page is "none of these: show more options".
A root decision with more than 255 options is two decisions: the handle (or a control option),
then the operation on that handle.

History: the name of this entry was "One letter for each option, and 20 options at most". The
first build read the log probabilities of one letter from a chat model, and 20 was the limit of
`top_logprobs` on that server. That model was not a decision model (`docs/FAILURES.md`, F39).

## D5. When a decision abstains

Status after D29: Superseded by D29 and D31.

Decision: a decision abstains when one of these is true:

- the top option has less than 0.5 of the probability (`abstain_top`)
- the second option has more than 0.5 of the probability of the top option (`abstain_ratio`):
  this is the rule for "the top two options are too close"
- the decision model refused the question. The answer then has the type `refusal` and no
  probabilities, and each option gets 0.

ASSUMED: the two numbers. A decision model is trained to give calibrated probabilities: of the
answers that it gives with 0.7, about 70% are right. So below 0.5 for the top, the other options
together are more likely than the choice. The guide of the Jev model says: "set thresholds from
your own labeled data". The decision logs give that data later.

A decision about the reason for `wake_agent` or `abort` does not abstain: the run stops in any
case. A decision on the structure of the input does not abstain (D23). A refusal is different:
it has no top option, so it becomes a question for each kind of decision.

Removed: the rule `abstain_mass`. It measured how much probability a chat model put on a letter.
A decision model puts its probability on the options only.

## D6. The walk

Status after D29: Superseded by D29.

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

Status after D29: Superseded by D29.

`log.own` and `log.audit` are on each handle. They are not offered as operations
(`hidden_capabilities`).

Reason: they read the operation log of DataHandles, not the resource. They doubled the number of
root options and no intent of a plan needs them. A sub-agent can still use them.

## D8. The candidate pool: sources, order, and two strengths

Status after D29: Superseded by D29.

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

The pool has at most 254 candidates: 255 options less `none fits`. ASSUMED: a value shows at
most two places, and a place shows at most four values.

Rejected by experiment: a decision in two stages (first the place, then the value). The model
gave 0.40 / 0.35 / 0.25 on the place. That model was the chat model of the first build (F39). The
experiment was not done again with a decision model.

## D9. The question, and its cap

Status after D29: Superseded by D29.

A decision that abstains, or that chooses `none fits`, becomes a question with the fields of the
brief: plan, intent, capability, field, the input chosen so far, and the candidates.

The order of escalation: the decision model, a sub-agent, the main agent. A sub-agent that hits a
cap returns `needs_planner`.

ASSUMED: one question can use at most 40,000 tokens (`question_tokens`), and never more than the
budget that the run has left. 40,000 is about five full contexts of the sub-agent model (8,192
tokens). The failure log has the cost of each question, to set this number from data.

## D10. The wait interval

Status after D29: Changed by D35: `wait_seconds` is the time of one tick.

ASSUMED: one `wait` is 2 seconds (`wait_seconds`). It is also the time that the orchestrator waits
for a new job before it gives the job handle to the decision model (D3).

Reason: each look at a running job costs one decision. A shorter wait gives more looks for the
same job. The first reason for this number was the time of one decision of the first model (about
1 second). A decision of the decision model takes about 0.4 seconds, so that reason is void.

## D11. The bounds of the state

Status after D29: Changed by D36: `value_chars` is the one bound.

ASSUMED: the state shows the last 5 results (`results_kept`), 600 characters of one value
(`value_chars`), and 8 handles: the start handles and the newest ones (`handles_kept`).

Reason: the brief says "the last few results, bounded". The state is the regulator's model of the
run, and each decision reads all of it, so its size is the cost of each decision. The first build
set these numbers for a context of 8,192 tokens. The decision model has a larger context, and the
numbers stay until a failure shows that the state is too small.

The stall signal is the seconds since the last change and the number of looks with no change. A
change is a new result, or a new observed version of a shown handle.

## D12. The sub-agent is a small agent loop on the local model

Status after D29: Superseded by D29.

Decision: `subagent.ModelAsker` runs a tool loop on a chat model on an Ollama server, with the
DataHandles tools (`handle_find`, `handle_describe`, `handle_invoke`) and two final tools:
`answer` and `needs_planner`. It runs as the principal `subagent`. The gate gives that principal
only the capabilities that do not write.

Reason: a sub-agent explores with open variety. It must write tool calls, and a decision model
writes no text. So the sub-agent is a different model from the decision model, and it is a model
that generates. A local model has no price for each call. The interface is one callable:
`asker(question, max_tokens, seconds)` gives `{"value", "reason", "calls", "tokens"}` or
`{"needs_planner", "calls", "tokens"}`. A headless coding agent with the DataHandles MCP server
fits the same interface.

The settings `model_url`, `model` and `num_ctx` are the settings of the sub-agent model. They keep
their names (ground rule 3). The decision model has its own settings (D17).

## D13. The human channel

Status after D29: Holds.

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

Status after D29: Changed by D33.

`running`, `waiting_answer`, `waiting_confirm`, `waiting_human`, then one end state: `done`,
`woke`, `aborted`, `limit`, or `failed`.

`wake_agent` ends the run. The main agent makes a new plan and submits it with the handles of the
summary. Reason: this is the slow loop of Ashby's ultrastable system. A step-function changes the
configuration, and the fast loop starts again with the new one. The run does not keep a part of
the old plan.

When a run ends, the orchestrator cancels the jobs that still run, unless the state is `done`.

## D15. A value that failed is marked, not removed

Status after D29: Superseded by D29.

When an operation fails with an error that is not retryable, each option that would give the same
value at the same field of the same capability and target shows
`(it was in an input that FAILED: ...)`.

Reason: the Homeostat's uniselector steps to a new configuration when the essential variable
leaves its limits. It does not step to the same one. The model repeated a failed input with p 0.98
before this mark (`docs/FAILURES.md`, F3). It is information for the decision model and not a
hard stop: the harness enforces only the two limits and the gate.

## D16. The logs

Status after D29: Changed by D36.

A run directory has:

- `decisions.jsonl`: one line for each decision node (the state, the partial input, the option
  keys and texts, the distribution, the choice, `refused`, the input tokens, the cost in USD
  as the API states it, and the time). The last line is the outcome.
- `questions.jsonl`: one line for each question with its answer and the sub-agent's calls.
- `events.jsonl`: each change of state, each request, each wake.
- `summary.json` and `lineage.json`, written at the end.

## D17. The decision model

Status after D29: Holds for the model and the API. The question type changed: D31.

A decision model is a class of model. It reads a state and typed questions. It gives one
probability for each allowed answer of each question, and it writes no text. The makers call the
class "System One" models. Examples are TypeSafe Jev, Perplexity pplx-decider and Cloudflare Clef.
The card of Clef states the mechanism: a head reads the final hidden states and gives one logit
for each allowed option, with no generation. TypeSafe states that it trains Jev for calibrated
probabilities.

Decision: `decide.Decider` sends one decision as one `choice` question to a Decisions API (D4).
The settings are `decision_url`, `decision_model` and `decision_key_env`. The default URL is the
Decisions API of OpenRouter (`https://openrouter.ai/api/alpha/decisions`). `decision_model` has no
default: the configuration must name the model. The API key comes from the environment variable
that `decision_key_env` names. The key is in no file of a run.

Reason: this is the regulator of the brief. `decide(state, options)` is one pass that gives a
distribution, and that is what a decision model does. A chat model that gives one letter is a
different thing (`docs/FAILURES.md`, F39).

The task runs use `perplexity/pplx-decider-v1.2`. The reasons:

- Its endpoint has a price ($0.02 for each million input tokens), so it has no free-tier limit of
  requests. A run makes up to two decisions each second. `pplx-decider-v1.3` is on OpenRouter only
  as a free variant.
- In the replay of 20 logged decisions, five decision models gave near the same choices
  (`docs/evidence/replay_20261115_235716/replay.txt`). The replay gave no reason for a model with
  a higher price.
- The model list of OpenRouter names a Hugging Face repository for it
  (`perplexity-ai/pplx-decider-v1.2`). A run with local weights was not tried.
- A comparison on another project of the owner (2026-10-22) put it ahead of `typesafe/jev-1.14`,
  `openai/gpt-6-luna-decisions` and `cloudflare/clef` on that project's decisions.

Measured in the six task runs of the README (145 decisions): the median time of one decision
was 402 ms, and one decision read 496 to 3,252 input tokens (median 1,105). The API stated
$0.0033 for the 145 decisions. The 20 replayed decisions cost $0.000568.

Another decision model replaces it with one setting. A model that does not speak this API (a
local model with open weights, or a trained model) replaces `Decider` with a callable of the same
shape: `decide(state, options)` gives a `Distribution`. The walk does not change.

The rules of the regulator and the text of the decision are the `instructions` of the question.
The state is the `state` of the request. The option names are numbers (D4).

## D18. Lineage

Status after D29: Holds. The handles of the lineage are the handles of the objects (D34).

The lineage of a run is the union of `Handle.graph()` of each handle that the run made, less the
job handles. It is a DataHandles graph: `Client.submit` runs it again. An approved action is in
the operation log of DataHandles and in `events.jsonl`, and it is not in the lineage graph: a
replay must not repeat an action.

## D19. `plan_status` can wait

Status after D29: Holds.

`plan_status(run, wait_seconds)` returns when the run is not `running` any more, or when the time
is over. The default is 0: the call returns at once.

Reason: attenuation. The main agent must see nothing until the run needs it. Without the wait, the
main agent polls, and each poll puts one more summary in its context. The server sets no maximum:
the limit is the tool-call timeout of the client, and a timeout loses nothing because the run
continues.

## D20. What the regulator sees: results by intent, and the look at a stream

Status after D29: Superseded by D29.

Three changes to the state came from the first runs on a stream (`docs/FAILURES.md`, F7 to F9).
Each one is information for the decision model. None is a check in code.

1. The decision text gives the results of the current intent apart from the results of earlier
   intents. With no result, it says "none yet: no operation ran for this intent". Before, one list
   held each result, and the model chose `step_done` for an intent that had no operation.
2. A look at a continuous handle (a stream) adds the outputs of its bounded probes to the status
   of the handle. The probes are the same ones that the candidate pool uses: each capability that
   reads, gives a value, and needs no input. The pool caches them by handle version. For the room,
   the probe is `room.latest`. Before, the status had only what `observe` gives, and the regulator
   could not see the variable that the intent names.
3. The result of a `wait` is what the look after the wait shows: the status of each job and each
   stream. The last few waits then show how the values move.

Reason: Ashby's regulator acts on what it observes. The brief says "Waiting is a choice that the
decision model makes again each time it looks", so a look must show the thing that the run waits
for. Replay of the logged decisions shows the effect: with change 3, the model chose `step_done`
with p 0.92 to 1.00 on each of four decisions where it chose `wait` with p 0.90 to 1.00 before, and
it chose `wait` with p 0.98 on the one decision where the temperature was below the limit.

A handle that is not continuous gets no probe output in its status: its values do not change, and
the candidate pool reads them when a field needs them.

## D21. A sub-agent that fails returns the question to the main agent

Status after D29: Superseded by D29.

A model call of the sub-agent can fail. The first case was an HTTP 500 of the model server:
"llama-server returned invalid tool call arguments" (`docs/FAILURES.md`, F10). The run then
failed. Now the question goes to the main agent with the reason "the sub-agent failed: ...".

Reason: this is the order of escalation. What a sub-agent cannot settle goes to the main agent.
A failed call of the decision model still ends the run with the state `failed`: no regulator is
left to continue.

## D22. A field with the schema of a chosen sibling is the same kind of thing

Status after D29: Superseded by D29.

`dataset.join` takes `on: [{left, right}]`. The two fields have one schema, and the schema does not
say that each one is a column. With `left` = `customer_id` chosen, the rule of D8 ("a value from a
place that an earlier choice names comes first") filled the first page of `right` with customer
ids. The join failed three times (`docs/FAILURES.md`, F14).

The rule now: when a sibling field has the same schema as the field, the sibling is a peer. A peer
is the same kind of thing, so each place where the pool observed the value of the peer is a place
with evidence for the field. A peer does not name a place of values. For `right`, the first page
is then the column names of the two datasets.

Reason: the pool knows no resource type, and the schema is the one statement of what a field is.
Two fields with one schema are interchangeable for the schema, so the evidence for one is evidence
for the other.

## D23. A decision on the structure of the input is never a question

Status after D29: Superseded by D29.

Two kinds of decision choose structure and not a value: "does the input have this optional
field" and "does the list get one more item". When one of these is below the abstain threshold,
the top option stands. The decision log marks it with `below: true`.

Reason: the brief says "When the pool is not enough, a sub-agent finds the value". A structure
decision has no value to find. The sub-agent finds facts, and "include the field" is not a fact
of the data. The first task run shows the cost: six structure decisions abstained, each one became
a question of 23,255 to 44,421 tokens, three reached the cap, and one answer was `true` for the
integer field `n` (`docs/FAILURES.md`, F15). In each of the six, the top option was the right one.
A wrong structure shows in the result of the operation, and the regulator reacts to the result.

The second task run shows the cost of this rule: 6 of 16 structure decisions below the threshold
took a wrong option, and two lists grew to six items (`docs/FAILURES.md`, F21). The operations
failed with errors, and the fourth try was right. The rule stays: a failed operation is cheaper
than a question, and the error is information for the next try.

## D24. The control options are on each level of the root

Status after D29: Superseded by D29.

With more options than one question can hold, the root is two decisions: the handle, then its
operation (D4).
The control options are now in the two of them. Before, a decision model that chose a handle
could not choose `step_done` any more (`docs/FAILURES.md`, F13).

## D25. A run ends when its process ends

Status after D29: Holds.

A run is a thread in the process of the MCP server. That process ends when the main agent ends
its session. Each run that works at that time ends with the state `left`, and its logs and its
summary say so. `plan_status` of a later process reads that summary from the run directory.

A client can end the server in two ways: it closes the input of the server, or it sends a signal.
The server ends its runs in each case (SIGTERM and SIGHUP). The end line is the last line of a
run's log: the thread of a run can still work for a moment, and the log takes no line after the
end (`docs/FAILURES.md`, F32).

The agent surface has four tools, and none stops a run. A main agent that wants to stop a run can
only leave. A fifth tool is a change of the surface in the brief, so it is not added
(`docs/FAILURES.md`, F17).

## D26. A pending question in the summary is its text and its schema

Status after D29: Superseded by D29.

The summary of a waiting run had the question two times: as fields (the plan, the candidates, the
handles) and as text. It has the id, the field, the schema, the reason and the text now. In the
first task run, the `pending` part was 3,254 of 9,876 characters of one summary.

## D27. A token that is not a plain word is a stated value

Status after D29: Superseded by D29.

The pool took two kinds of literal from an intent: a quoted string and a number. The intent "Get
the report at the path /reports/quarterly" has no quote characters, so the pool did not have the
path, and `api.get` got the base address (`docs/FAILURES.md`, F26).

The rule now: a token of the intent that is not a plain word and not a number is a stated value
too. Examples: a path, a dotted name, a date. The edge punctuation of the sentence is not part of
the token. A digit inside such a token is not a number of the sentence.

Reason: the brief names "literals in the plan's intents and notes" as a source. A main agent
writes a path as it is. A plain word stays a weak candidate ("a word of the current intent").

## D28. The wall time bounds each look at a handle

Status after D29: Holds (`Run._bounded`).

A look at a handle (`observe`) can wait as long as a job on the same resource runs
(`docs/GAPS.md`, G11). The wall time is a hard limit, so the run makes each look in a second
thread and stops at the limit, the same way as for an approved action (G5). The run does not skip
the look at a busy resource: the protocol does not say which handles share a lock, so the run
cannot know which looks will wait.

## D29. The decision model is the detector of the second loop. It is not in the first loop

The first design put the decision model in the path of each operation: it chose the capability,
then each field of the input, for each step of the plan. The measurements showed the cost
(`docs/FAILURES.md`, F46). On a bounded sequence of 6 operations the run took six times as long as
the main agent alone. On a long wait the run helped, and it helped because the decision model
took the repeated observation away from the main agent, not because it chose operations.

Decision: the system is Ashby's ultrastable system (*Design for a Brain*, 2nd ed. 1960, chapter 7).

| Ashby | In this system |
| --- | --- |
| Environment | The resources behind the handles: a feed, a room, a service, a script |
| Reacting part R | The objects that the main agent built. DataHandles runs them. No model is in this loop |
| First loop | An object reads its handle again when the handle has a new version |
| Essential variables | The two hard limits, and the sentences that the main agent wrote |
| Second loop | The decision model looks at what the objects show, and judges each sentence |
| Step-function | A wake: the main agent replaces the object that is broken, and nothing else |
| Uniselector | The main agent at a wake: it selects the next configuration |
| Good regulator is a model of the system | The state: each object, its operation, its newest output |

The generative agent does not monitor. It builds the thing that monitors, and it leaves. The
decision model does not build and does not route. It judges one thing: is each essential variable
inside its limits? When each one is inside, the run does nothing.

Reason: in the Homeostat, the uniselector does not move while the essential variable is inside
its limits. The unit regulates with its current settings, and the second loop is silent. A
uniselector that steps at each moment is not a second loop: it is a part of the first one, and the
system pays for it at each step.

What left the code: the walk (`walk.py`), the candidate pool (`pool.py`), the sub-agent
(`subagent.py`), the questions, and the control options `step_done`, `wait`, `wake_agent`, `abort`.
Steps 3 and 4 of the brief described them. The user's ruling of 2026-11-16 replaced those steps.

## D30. A plan is objects and variables

A plan has `objects`, `variables`, optional `notes`, and optional lower `limits`. An object is
`{name, handle, operation: {capability, input}}`. A variable is one sentence.

- The plan has no `intents` and no start `handles`. An intent was an order to the decision model
  to choose operations. The main agent chooses the operations now, with the DataHandles tools,
  before it submits the plan.
- An object needs an operation. A `handle_describe` of a derived handle is not a fresh read: after
  a schema break of its source it still shows the old schema (`docs/GAPS.md`, G14). Only a read
  shows the break.
- The operation must give a small value. The state keeps `value_chars` characters of it (D36).
- `prepare` refuses an operation whose capability is a stream. A stream gives its first event and
  stops, so one run of it shows nothing about the feed. The error text tells the main agent to
  read the newest value with a plain operation.
- An object whose operation is an action runs one time for one approval, and not again at a new
  version of its handle (the action gate, D1).

Reason: the brief says that steps, states and expectations are information and not code. A
variable is a sentence for that reason: the harness has no code for "a schema break" or "a silent
sensor". The main agent states what viable means for its own work.

## D31. A variable is inside its limits when p is 0.5 or more

One look is one call to the Decisions API with one `noul` question for each variable. A `noul`
answer is one probability that the statement is true. A variable is outside its limits when its
probability is less than `inside` (0.5). A refusal counts as 0.

Origin of the number: 0.5 is the point where the model holds "true" and "not true" as equally
likely. A check with 24 statements of known truth on 12 states (`tasks/probe.py`, output in
`docs/evidence/probe_20261116`) gave 0.99 to 1.00 for each true statement and 0.00 to 0.04 for
each false one. Each threshold between 0.05 and 0.98 gives the same 24 judgments, so the data
gives no reason for a value other than the middle. The first design had two thresholds for a
choice among many options (D5). A `noul` answer has no "top two", so those rules have no meaning
here.

Each run has one more variable that the harness adds: "No object shows an error." The harness does
not stop on an error and does not wake on one by code. An error can be a fault of one read, or a
break of the structure, and that is a judgment. The decision model makes it.

## D32. When the decision model looks

The decision model looks in two cases:

1. **A change**: the output or the error of an object is not the same as before. The look comes at
   the end of that tick. Many changes in one tick give one look.
2. **Silence**: no output changed for a time. The first silent look comes after
   `2 * max(gap, wait_seconds)`, where `gap` is the longest time between two changes that the run
   saw. The time doubles after each silent look. A change resets it.

A new object gets one tick to show its first output before a look.

Reason for 1: the essential variables can only leave their limits when what the objects show
changes, or when time passes. A look at a state that did not change, a moment later, gives the same
answer.

Reason for 2: a variable can be about time ("the feed got new rows in the last 30 seconds"), and a
missing sensor shows as no change at all. The harness has no number for "too long": a model would
guess that number, and a guess is not a limit. So the looks start at two times the longest gap of
this run, which is a measured value, and each later one doubles. A silent run of T seconds gets
about log2(T / gap) looks. The decision model reads "last change of the output: N s ago" and
judges the sentence of the main agent.

The cost of a look is small and it is not zero: 569 to 598 input tokens in the first task run,
about $0.000012. A feed that changes at each tick gets one look at each tick. That is the price of
a detector that reads sentences. It is not in the path of the operations: an object runs again
without the look.

## D33. A wake parks the run. The answer replaces objects

The states of a run are `running`, `woke`, `waiting_confirm`, `waiting_human`, then one end state:
`done`, `limit`, `failed` or `left`. This changes D14: `woke` is not an end state now, and
`aborted` and `waiting_answer` are gone.

At a wake the run stops its clock and waits. The summary has `pending.outside` (each variable
that is outside, with its probability), each object with what it shows now, the plan, and the last
wake with its answer. The main agent answers with `plan_answer(run, question, value)`; `question`
is the id of the wake. The value has one or more of:

- `objects`: new versions. An object with the name of a current object replaces it. A new name
  adds an object.
- `remove`: names of objects that the run must stop.
- `variables`: a new list of variables.
- `end: true`: the work is complete. The run ends with the state `done`.

The run checks the answer the same way as a plan. A value that is not valid returns as an error
value, and the run stays in `woke`.

Reason: the step-function changes the parameters of the reacting part, and the system goes on
with the same essential variables. The first design ended the run at `wake_agent` and the main
agent submitted a new plan (D14). That discarded each object that was still good. Now the main
agent "rewrites only what is broken". The tool names of the brief stay: `plan_answer` had the
purpose "the main agent gives what the regulator could not find", and it keeps that purpose.

A goal that is met is a wake too. The main agent writes the goal as a variable that is true while
the goal is not met ("the feed has fewer than 600 rows"). The decision model cannot end a run, and
the harness cannot: the end of the work is the main agent's word.

## D34. The stable interface is the name of the object

The ruling says: the interface (the data handle) stays stable, and the object behind it is
replaced. DataHandles cannot put a new computation behind an existing handle id
(`docs/GAPS.md`, G13). So the name of the object in the run is the stable interface. The summary,
the state of the decision model and the logs use the name. The handle id behind the name changes
at a wake, and `events.jsonl` has each version with the wake that wrote it (`written`).

The lineage of a run is the graph of each handle that was an object, the replaced ones too (D18).

## D35. The first loop: observe, and run again at a new version

At each tick (`wait_seconds`, 2 s), for each object with no running job, the run asks
`observe()` for the version of the handle. When the version is new, the run starts the operation
again as a job. This is the whole first loop. It has no model call.

- A derived handle (a filter, an aggregate) reads the newest data of its source at each read, and
  its `observe` gives the version of its source. So one object on a derived handle is a live view.
- The loop does not observe an object while its own job runs (G11).
- `wait_seconds` is ASSUMED: 2 s. It bounds how old an output can be. No measurement gave it.
- The job handle of an operation belongs to the orchestrator (G7). The first design granted it to
  the main agent. The main agent reads the handle of the object now, so the grant is gone.

## D36. The state, the budget and the logs

- The state has the notes, each object (name, type, operation, output or error, the number of
  runs, the seconds since the last change, the seconds that a job runs), and the two limits with
  the spend. It has no history: the decision model judges what is true now. This changes D11 and
  D20: `results_kept` and `handles_kept` are gone.
- `value_chars` (600) is the size of one output or one error message in the state. ASSUMED. A
  longer value keeps its first and its last part (D38).
- The hard budget counts the input tokens of the decision model. Nothing else in a run uses a
  model. The spend of the main agent is not in the budget: the run cannot measure it.
- `decisions.jsonl` has one line for each look: the reason (`change` or `silence`), the state, the
  statements, the probabilities, the variables outside, the tokens, the cost and the time. The
  last line is the outcome of the run. `questions.jsonl` is gone.
- `plan_status` gives a brief summary (the state, the limits, the counts, what to do next) when a
  wait ends and the run still has the state `running`. Reason: the main agent must add little to
  its context when nothing needs it. Each other status gives the full summary.

## D37. The order of escalation

The brief had four levels: the decision model, a sub-agent, the main agent, the human. Now there
are three: the objects (no model), the main agent at a wake, and the human at the action gate.
The decision model is not a level of the escalation. It is the detector that decides when the
escalation happens.

The sub-agent of the brief found one input value for the decision tree. With no decision tree,
it has no question to answer. The main agent explores with the DataHandles tools when it is awake,
with its own judgment about what the user meant.

## D38. A long value keeps its first and its last part

`cut` keeps the first half and the last half of `value_chars`, with the number of characters that
it removed between them.

Reason: the end of an output often has the result (the last line of a script, the newest rows),
and the start has its form (the keys, the columns). The first build of this design kept the start
only. The task runs of 2026-11-16 used that build. One output in those runs was longer than the
bound (`docs/FAILURES.md`, F50).

## D39. A mutation that no test stops

The tests were checked with 17 mutations of `run.py`, `decide.py`, `state.py` and `plan.py`. A
test failed for 16 of them. The one that lives removes the wall-time test of `Run._check`.
`Run._bounded` enforces the same limit at the next `observe` or the next look, so the run stops at
the same tick. The two checks are kept: `_check` gives the plain message.


## D40. An object carries its runners

The ruling of 2026-11-16 (the third one): "Extend that same object so it can carry the identity of
its current runner and, if useful, a short ordered list of alternative runners. When the existing
decision check reports that the current binding is inadequate, the same reconfiguration path that
already repairs objects simply rebinds the next runner (or wakes the generative agent if the list
is exhausted). No second decision model, no new router, no parallel escalation graph."

A **runner** is the agent that answers for an object when a variable about that object is outside
its limits. An object now has five parts: its name, its handle, its operation, its `variables`
(the check of the object, D41) and its `runners` (a list of names, in order). The names are:

- `main`: the main agent.
- `human`: only for an action (D44).
- a model runner: a `[runners.NAME]` table of the configuration (D43).

An object with no `runners` has the main agent as its runner. This is the design before the ruling.
A new version of an object keeps the variables, the runners and the current binding of its name.

The run has one path for a wake, `Run._wake`. It gives the wake to the current runner of the
object. Each runner answers in one form (`ANSWER_SCHEMA`), and `Harness.prepare` checks each answer
the same way. The binding (`Entry.bound`) is an index into the list of the object. `Run._rebind`
is the only code that changes it, and each change is one `rebind` event in `events.jsonl`.

A model runner can write one thing: a new handle and a new operation for its own object. It cannot
change the variables, remove an object, end the run or write an action. Those need a decision about
what the user wants, so they stay with the main agent.

This changes D37. The order of escalation is not a set of levels in the harness. It is the list
inside each object, and the main agent wrote that list.

## D41. The check of an object, and the owner of a statement

An object can have `variables` of its own: sentences that are true while the object does its
work. A look asks them in the same request as the variables of the plan, in the form
"About the object `NAME`: SENTENCE". A look asks them only after the current version of the object
showed its first output or error.

A statement that is outside its limits has **owners**: the objects that it is about.

| Statement | Owners |
| --- | --- |
| A variable of an object | That object |
| "No object shows an error." | Each object that shows an error |
| A variable of the plan | None |

The wake goes to the runner of the first owner, in the order of the plan. With no owner, it goes to
the main agent.

Reason: the decision check that exists already says which object is inadequate. No second model
call routes the wake. A variable of the plan is about the work as a whole (a goal that is met, a
rule about more than one object), and only the main agent knows what the user wants there.

Limit: when two objects are owners in one look, only the first one gets a wake. The second one is
still outside at the next look, and it gets its wake then.

## D42. The trial of a repair

After an answer to a wake, the object of the wake is on trial. The next look that sees an output
or an error of the new version judges the repair:

- The object is an owner of a statement that is outside: the repair did not hold. The object
  binds its next runner, and that runner gets the wake.
- If not: the repair held. The object binds its first runner again.

A runner that gives no repair is also a binding that is not adequate. That is: the runner gives up,
its model fails, it reaches `wake_tokens`, or it reaches the limits of the run. The same wake goes
to the next runner, and `pending.tried` has each runner that gave no repair, with the reason. After
the last runner of the list, the wake goes to the main agent.

So an object with the runners `["local", "top"]` goes: `local` repairs. If its repair does not
hold, `top` repairs one time. If that holds, the object is with `local` again. The handle of the
object and its check do not change in that sequence. Only the binding changes.

Reason: this is the Homeostat's rule. The uniselector steps when the essential variable is still
outside its limits after a trial, and it rests when the variable is inside. A trial must last long
enough to show its result (*Design for a Brain*, chapter 7), so the judgment waits for the first output
of the new version. No number decides how many times a runner can try: one trial for each binding.

Limit: a repair that holds at its first look and fails at a later one starts again at the first
runner. The run has no memory of which runner made which version, other than the logs.

## D43. A model runner

A model runner is a chat model behind a chat completions API in the OpenAI form, with tool calls.
The configuration names each one:

    [runners.local]
    url = "http://127.0.0.1:8000/v1/chat/completions"
    model = "a-local-model"
    wake_tokens = 2000000

`key_env` names the environment variable with the API key, when the API needs one. D50 has the
origin of the number, and the key `body`.

- At a wake, the runner gets one message: its object with what it shows now, `sources` (each
  handle that the handle of the object was made from), the statements that are not true now, the
  names of the other objects, the limits, the notes of the plan, and `tried`. It gets no history.
- It has five tools: `handle_find`, `handle_describe` and `handle_invoke` (the tools of the
  DataHandles tool surface), `repair` (a handle and an operation) and `give_up` (a reason).
- `repair` goes through the same check as an answer of the main agent. A value that is not valid
  returns to the runner as the result of the tool, and the runner can try again.
- It works as the orchestrator's principal. That principal holds no action (D1), so the runtime
  refuses an action from a model runner. `Gate.delegate(handle, sources=True)` gives the
  orchestrator each capability that is not an action on the handle of the object and on each of
  its sources, at the time when the main agent names a model runner for the object.
- Its tokens (input and output) count against the hard budget of the run. `wake_tokens` is the
  most that one wake of this runner can spend. It has no default: it is a limit of the spend, so
  the user states it.
- `runners.jsonl` has one line for each wake of a model runner: the question, each tool call with
  its result, the repair, the tokens, the cost and the time.

This brings back the sub-agent of the brief, which D37 removed. Its interface is the one that the
brief gave: a question in; a value, or "this needs the planner", out. Its question is now "repair
this object". The brief gave the sub-agent a principal of its own with read grants. It works as
the orchestrator's principal here, because that principal has those grants already and a fourth
principal needs a fourth token. So a model runner can invoke a capability that writes and is not
an action, the same as the first loop can.

D50 changes how a wake ends, and it removes the bound on one result of a tool.

## D44. A confirmation is the same binding

An object whose operation is an action has the runners `["main", "human"]`. A capability in
`human_only`, or an object that states `runners: ["human"]`, has `["human"]`.

- The request goes to the current runner: `waiting_confirm` for the main agent, `waiting_human`
  for the human.
- `plan_confirm(run, request, "human")` binds the next runner of the object for this one decision.
- After the decision, the object binds its first runner again. The next request of the same object
  starts with the main agent.

This is the gate of D1 with no new path: `human_only` and "pass to the human" were two special
cases in `Run._action`, and they are now the list of the object.

**The holder of the action runs the graph.** F52 showed that a main agent whose principal holds
the action can invoke it with no request. The protocol gives one arrangement that stops this: the
human's principal registers the resource and grants the main agent each capability that is not an
action. Measured on DataHandles v0.4.0:

1. The planner and the orchestrator then get `CAPABILITY_DENIED` on the action.
2. The planner can grant onward what it holds, so `Gate.delegate` still works.
3. A graph runs with the capabilities of the principal that calls `graph.run`. So the human's
   principal must run the graph, not only approve it.
4. A derived handle of the orchestrator resolves for the planner.

An object for such an action states `runners: ["human"]`. `uniselector-confirm ... approve --url
URL` then approves the graph and runs it as the human's principal, and it writes the result for
the run. With no `--url`, nothing approves the graph, and the object shows `NOT_APPROVED`. The main
agent cannot read the descriptor of that capability, so the plan has no schema check for its input
(`docs/GAPS.md`, G16).

## D45. The spend is an essential variable

D48 removed this variable. The text below is the record of what was built.

The hard budget stays a hard limit. In addition, a look can ask one more statement: "The projected
tokens are not more than the token budget."

- `projected_tokens` = the tokens so far + (the mean tokens of one look) x (the rate of changes) x
  (the seconds that are left of the wall time).
- The rate of changes of one object is `(n - sqrt(n)) / t`: `n` changes of its output in the `t`
  seconds since its first output. The rate of the run is the sum over the objects, and it is one
  for each tick at most.
- A look asks the statement only when a look at each tick, for all of the wall time that is left,
  passes the budget. With the limits of `uniselector.example.toml` and 387 tokens for one look, it
  does not: 387 x 900 ticks is 348,300 tokens.
- The owner of the statement is the object with the highest rate. Its runner gets the wake. The
  repair is a version of the object whose output changes only when the work needs attention.

Reason: F49. Each number in the projection is a measurement of this run. No model writes a limit.
The decision model compares two numbers of the state, in the same request as each other variable.

`n - sqrt(n)` is ASSUMED: a count of `n` events has a standard deviation of about `sqrt(n)`, so
this is one standard deviation below the count. One change gives a rate of zero, so a run with one
change cannot wake for its spend.

## D46. An object shows when its operation last ran

The repair for F49 is an object whose output changes only when the work needs attention: a
highest value, a count of the rows that break a rule. Such an object is quiet while its source is
live, and it is also quiet when its source stopped. The output alone does not tell the two apart.

The first loop runs the operation of an object again when its handle has a new version (D35). So
the time of the last run is a measurement of the source. An object now shows it: `ran_s`, the
seconds since the last run of its operation started. The state of a look has the line
"runs: 14, the last one started 2.1 s ago. last change of the output: 31.4 s ago." A variable can
then state "Its operation ran in the last 15 seconds."

An action has no `ran_s`: its operation runs one time, after a confirmation.

Reason: the measurement exists already, and no model makes it. Without it, a main agent that
watches for a stop must build an object that changes at each reading, and that is F49 again.

Limit: with no change of an output, the looks come after a silence that doubles (D32). A look
that finds the stop can then come later than the time that the variable states, by the length of
the silence so far at most.

## D47. The mutation check of the runner code

The tests were checked with 27 mutations of the code of D40 to D45, in `run.py`, `state.py`,
`runner.py`, `gate.py`, `plan.py` and `config.py`. A test failed for 24 of them. The three that
lived, and the test that now stops each one:

| Mutation | Test |
| --- | --- |
| An action goes on trial after its answer | `test_a_denied_action_does_not_run_and_the_denial_wakes_the_main_agent` |
| A runner name that the configuration does not have gets no park with the main agent | `test_a_denial_by_the_human_wakes_the_main_agent` |
| `State.projected` gives a number when a look at each tick stays in the budget | `test_a_look_asks_about_the_spend_only_when_a_look_at_each_tick_passes_the_budget` |

A test now fails for each of the 27. D48 removed the code of the third row and its test.

**The second check.** The code of D48 to D51 was checked with 31 mutations in `run.py`,
`runner.py`, `state.py`, `config.py` and `decide.py`. A test failed for 26 of them. The five that
lived, and the test that now stops each one:

| Mutation | Test |
| --- | --- |
| A look asks the check of an object while its runner works | `test_a_variable_with_no_object_wakes_the_main_agent_while_a_runner_works_and_waits_after_the_answer` (its name after D52) |
| An object stays `in_repair` after its runner ended | `test_the_wake_comes_to_the_main_agent_after_the_last_runner` |
| The log of a runner does not count its tokens | `test_a_runner_that_raises_gave_no_repair_and_the_next_runner_gets_the_wake` |
| The harness does not give `body` to a model runner | `test_the_configuration_names_each_model_runner` |
| One request of a model runner gets 1 ms, not the time that the run has left | `test_a_model_runner_reads_with_the_tools_and_writes_one_repair` |

A test now fails for each of the 31.

**The third check.** D52 removed one mutation with the rule that it changed, and its code and the
two small fixes of D51 got 7 more: 37 mutations. A test failed for 36 of them. The one that lived:
the run kept the statements of an answer of the main agent also when no runner worked, so a
statement that was still not true never woke the main agent again. The last lines of
`test_a_variable_with_no_object_wakes_the_main_agent_while_a_runner_works_and_waits_after_the_answer`
stop it now. A test fails for each of the 37.

**The fourth check.** The code of D53 and D54 was checked with 33 mutations in `run.py`,
`runner.py`, `state.py` and `config.py`. A test failed for 32 of them. The one that lived: the run
took a repair that is not an object, and the runner got no fault text.
`test_a_runner_is_any_function_of_the_users_code_or_of_the_agents_code` stops it now: its runner
first submits a text and reads the fault. A test fails for each of the 33.

**The fifth check.** The code of D55 to D60 was checked with 56 mutations. A test failed for 55 of
them. The one that lived was a clause of the line that makes a look due after a form: no case
needed the clause, so it is removed. A test fails for each of the 55 that are left.

**The sixth check.** The code of D61 was checked two times. The first check had 13 mutations, and
5 of them lived. Each one showed a fault of the code or of the tests:

| The mutation that lived | What it showed | The change |
|---|---|---|
| The next look after a version is back is not due at once | No test read when that look came | `test_the_main_agent_reads_each_replacement_that_left` reads the reason of that look: `change` |
| The version that is back does not take the spend, or the forms, from the replacement (2 mutations) | The line did no work: the two versions have the same list of runners, and the spend is equal before the replacement is in place | The line is removed |
| The kept version is the version before the trial, also when a runner wrote it | No case differs: a look judges a trial before a second runner can replace the object | The code keeps the version before the trial, with no second case |
| The wake does not clear what the object knows about the replacements that left | The clear made one rule false: after two runners, the main agent read only the last one | The clear is removed, and the same test reads the two runners in `tried` |

One more mutation removed the whole line of the second row, and a test failed for it one time. On a clean copy each test
passed: the first result came from the timing of one test, not from a guard. The script now prints the
test that fails for each mutation.

The second check had 15 mutations of the code after these changes. A test failed for 14. The one
that lived cleared the version from before when the main agent answers a wake. A look clears it
before a wake can go to the main agent, so the line is removed. A test fails for each of the 14.

## D48. The spend is not a variable. The hard budget is the limit

D45 is removed: the statement about the projected tokens, the projection, and the rate of changes
of an object. The token budget stays a hard limit, as the brief states it.

Reason: the first task run with the variable gave a wrong wake (`docs/FAILURES.md`, F53). The
projection took the rate of the first 4 s of a run and held it for the 1,796 s that were left. A
new object shows its first outputs in those seconds, so the rate was high, and the feed of that
task was 60 s long. The wake then went to a model runner, and that runner cannot repair a spend:
which output of an object the user can do without is a question about intent, and intent is with
the main agent.

The brief has the rule that D45 broke: "Do not add per-node checkers, expected durations, expected
row counts or other numeric limits written by a model." The harness wrote the projection, not a
model, but it was the same thing: a guess about the future as a limit of the present.

What stays for F49: the tool text tells the main agent to prefer an object whose output changes
only when the work needs attention, and an object shows when its operation last ran (D46).

Removed code: `IN_BUDGET`, `State.projected`, `Entry.rate`, and the fields `tick`, `looks` and
`look_tokens` of `State`.

## D49. A model runner works beside the loop

A wake of a model runner does not stop the run. The run starts the runner, and the loop goes on:
each other object runs its operation again, and the decision model looks at each change.

Reason: Ashby's Homeostat has four units, and each unit has a uniselector of its own. While one
unit steps through its uniselector, the other three units go on. In the first task run with
runners, one wake of a model runner took 517 s, and in that time no object ran and no look was
made (`docs/FAILURES.md`, F55).

- `Run._start` starts the runner in a thread of its own. `Run._collect` takes its answer at the
  start of a tick. The object shows `in_repair` with the id of the wake until then.
- A look does not ask the check of an object that is in repair: its runner knows that it is
  broken.
- A wake goes to the first owner that is not in repair. When each owner is in repair, or when a
  statement has no owner and a runner works, the look writes the event `held` and no wake. The
  repair in progress can be the cause. The look after the repair asks the statement again.
- With no repair from a runner, the same wake goes to the next runner of the object, as before
  (D42). A runner that raises an exception gave no repair.
- The main agent can write an object while the runner of that object works: a wake about a second
  object came to it. Then the version of the main agent stays, and the run drops what the runner
  gives (the event `dropped`). The log keeps what the runner did.
- A runner reports each request to the run (`spend`), and it gets the tokens that the run has
  left. So the hard budget holds while a runner works: a runner stops at the request that passes
  the budget. After the end of a run it gets 0, and the run takes no repair.
- A wake of the main agent, and a request to the human, still park the run and stop its clock
  (D33). A runner that works at that time goes on, and the run takes its answer after the park.

Limit: a variable of the plan that leaves its limits while a runner works waits for the end of
that wake. The decision model cannot say if the object in repair is the cause. A wake of the main
agent at each break would remove what the binding is for.

D52 replaces this limit and the rule for a statement with no owner. A task run measured the wait:
432 s (`docs/FAILURES.md`, F62).

## D50. One wake of a model runner is one turn of an agent

This changes D43 in four places.

**How a wake ends.** The model calls `repair` and the run takes the value; or the model calls
`give_up`; or the model answers with no call of a tool. The last one is new: before, the runner
sent "Use a tool." and went on. In the first task run with runners, a local model answered 14
times with no call and no text in each of two wakes (F54). An answer with no call is the end of
the turn of an agent, and it is a result: this binding is not adequate, and the next runner of
the object gets the wake with the text.

A wake ends also at a limit: `wake_tokens`, the tokens that the run has left, or the wall time
that the run has left.

**The form of a runner.** `runner(question, submit, spend, seconds) -> Outcome(calls, reason)`.
D51 adds `main` to the outcome.
`submit(value)` gives the repair to the run and returns the fault of the value, or None.
`spend(tokens, cost)` reports one request and returns the tokens that the run has left.

**No bound on one result of a tool.** `result_chars` is removed. DataHandles bounds each value
(64 KiB), so a result goes to the model in full, and the log has it in full.

**`wake_tokens` counts what the API counts.** Each request sends the whole conversation again. So
the sum of the tokens of a wake grows with the square of the count of the requests. In F56 the
largest result of a tool was 10,236 characters, and a wake of 18 requests still spent 137,145
tokens. The values of the example configuration and of the task runs are ASSUMED:

| Runner | `wake_tokens` | Origin |
| --- | --- | --- |
| `local` | 2,000,000 | 30 requests in a conversation that grows to 100,000 tokens. The local model of the task runs serves 245,760 tokens of context, and its tokens have no price |
| `top` | 1,000,000 | 0.15 USD for one wake at most, at 0.15 USD for each million input tokens |
| The run (`budget_tokens`) | 5,000,000 | 5 wakes of `top` at its limit: 0.75 USD at most |

**`body`.** A `[runners.NAME]` table can state `body`: more keys for each request. The server of
a model can need them. The local model of the task runs is `qwen3.8-27b` behind vLLM, and it gets
`body = { chat_template_kwargs = { enable_thinking = false } }`: with thinking on, this model can
spend all the tokens of one answer on thinking and give no content.

**Read the context that a server gives a model.** The first local model ran behind Ollama, and
Ollama gives a model 4,096 tokens of context when no one sets `num_ctx`. The mean request of its
first wake counted 3,541 tokens, and 14 of its 18 answers were empty (F54). The same wake path
with a model that has its full context gave a right repair in 10.1 s
(`docs/evidence/repair_20261116_184658`).

## D51. A runner can give a wake to the main agent, and a busy decision model makes no look

Two faults of the task runs with the larger limits blocked useful runs (`docs/FAILURES.md`, F57
and F58). Each change is in an object that exists. No path is new.

**`to_main`.** A model runner has a third tool that ends a wake: `to_main(reason)`. The outcome is
`Outcome(calls, reason, main=True)`.

| The runner ends with | It says | Who gets the wake next |
| --- | --- | --- |
| `repair` | "This is the new version." | No one. The repair is on trial until the next look (D42) |
| `give_up`, an answer with no call, or a limit | "I did not find the repair." | The next runner of the list |
| `to_main` | "The object has no break", or "the repair needs a decision about what the user wants." | The main agent |

After `to_main`, the object binds the end of its list for this wake. A larger model cannot repair
an object that has no break, so the wake does not go to it. The wake has `tried` with the reason
of the runner, as each wake after the last runner has (D40).

Reason: F57. The runner `local` found in 88 s that the object worked, and it had no way to say
so. The wake went to `top`, and `top` spent 676,367 tokens and 580 s on an object with no break.
This is the `needs_planner` of the brief: "It finds facts. It does not decide what the user
meant."

**The check of an object says only that the object works.** A statement that tells the main agent
that the work is done, or that needs its decision, is a variable of the plan. A variable of the
plan has no object, so its wake goes to the main agent (D41). The tool text of the MCP server says
this now. The harness does not enforce it: no code can tell a statement about a goal from a
statement about a break. `to_main` is the path for a plan that has it wrong.

**`Busy`.** A decider raises `Busy` when it made no look now and it can make one later. `Decider`
raises it for HTTP 429 and for HTTP 500 and above. The run then writes the event `no_look`. It
counts no look and it spends no tokens. The look stays due, and the next tick asks again. Each
object goes on in that time. A look after a silence doubles the silent time of the next look
(D32). When the decision model is busy, the run puts that time back, so the look is not lost.

Each other fault of the decision model ends the run as `failed`, as before: a wrong key (HTTP
401) or a reply with a wrong form does not get better at the next tick.

The run has no count of the requests that failed, and no time between them that grows. The tick
(2 s) is the time between two requests, and the wall time is the limit. A number for either one
is a guess, and no run showed a need for it.

Reason: F58. One HTTP 429 at look 51 ended a run whose objects worked.

**A change of the binding that changes nothing is not a rebind.** An object with the runners
`["main"]` has the runner `main` at each place of its list. `_rebind` writes an event and counts a
rebind only when the name of the runner changes (F59).

## D52. A variable of the plan does not wait for a runner

A statement with no object wakes the main agent at the look that finds it outside its limits. It
does so also while a model runner works on an object.

Reason: in the Homeostat, the essential variable of a unit moves the uniselector of that unit
only. A variable of the plan has no object (D41), so no model runner answers for it. The main
agent is its only runner. D49 held such a statement while a runner worked, because the repair in
progress can be the cause. A task run measured that rule: the feed ended after 60 s, the goal of
the plan was not true from 62.3 s, and the main agent got the wake at 494.2 s, when the runner
`top` stopped with no repair (F62). A wake of the main agent must not wait for the slowest
runner.

| At a look, a statement is outside its limits | What the run does |
| --- | --- |
| It has an owner that is not in repair | The wake goes to the runner of that object (D40). A model runner works beside the loop (D49), and the same look goes on |
| Each owner is in repair | Nothing. The runner has the object |
| It has no owner, and the main agent did not answer a wake with it | A wake of the main agent, with no object. The summary shows `in_repair` for each object that is with a runner |
| It has no owner, the main agent answered a wake with it, and a runner still works | The event `held`. The main agent knows the statement |

- One look can give two wakes: one to the model runner of an object, and one to the main agent.
- The main agent can answer `objects: []`. This says: "Let the runner work." The run keeps the
  statements of that wake (`Run.told`), and a look holds them while a runner works.
- The end of each wake of a runner empties `Run.told`: the result of a repair is new. A statement
  that is still not true at the next look wakes the main agent again.
- A hold has no time limit. The main agent chose it with the summary in hand, and the end of a
  wake of a runner ends it.

Cost: a plan that states the check of an object again as a variable of the plan wakes the main
agent one time at each break of that object. The tool text of the MCP server now says not to do
this. D49 gave this cost as the reason for the hold. The measured hold cost more: 432 s of a run
whose work was complete.

**A model runner cannot change the data.** The tool text says this too: a model runner can replace
the handle and the operation of an object, so a check that only a change of the data can make true
again is a variable of the plan. Reason: F60. As in D51, no code enforces it.

Live check: `schema_20261116_195950`. One look gave an object to `local` and woke the main agent.
The main agent answered one wake with `objects: []`, and 3 looks held its statements. The goal woke
the main agent 0.4 s after its object showed the last rows. The session took 101.2 s, and 533.8 s before this rule.
The run showed the cost that this decision names (`docs/FAILURES.md`, F63), and a repair that held
with wrong totals (F64).

Not built: after a repair that does not hold, the object keeps the version of that repair, and the
next runner gets a new wake with no `tried`. The next runner does not see the version that the
main agent wrote (F60). The change belongs in the object (the object keeps its version from before
a trial), and no run showed yet that it removes the cost of F61.

Changed by later decisions: the next runner gets `tried`, and `made_before` shows how the handle
was made before each runner (D56, D59). One request for each wake removed the cost of F61 (D57).
An object keeps its version from before a trial (D61).
The cost that this decision names came again in `schema_20261116_233410` (F69).

## D53. A runner is any callable

A runner is a function `runner(question, submit, spend, seconds)`. The run does not know what is
behind it: a model, a script, or code that the main agent wrote one minute ago.

Reason: the Homeostat's uniselector does not know what its positions connect. It steps to the next
position, and the essential variables show if that position is adequate. The run judges a runner
the same way: by the next look at the object, never by its kind (D40, D42). So the code of the run
has one path for each runner, and a model runner (D43, D50) is one such function.

| Argument | What it is |
| --- | --- |
| `question` | The wake: the object, what is outside its limits, what each runner before this one tried, the notes, and the limits |
| `submit(value)` | Gives the result to the run. It returns `None`, or the fault as a text, so the runner can correct the value and call it again |
| `spend(tokens, cost)` | Counts a spend against the hard budget of the run. It returns the tokens that the run has left |
| `seconds` | The wall time that the run has left |

**Where a runner comes from.**

- A table `[runners.NAME]` with `url` is a model runner, as before.
- A table `[runners.NAME]` with `call` names a function of the user's code: `FILE.py:NAME` or
  `MODULE:NAME`. Uniselector calls `NAME(client, ...)` one time at the start, with the DataHandles
  client of the orchestrator's principal and each other key of the table as an argument. `NAME`
  returns the runner.
- A plan, or an answer to a wake, can name `FILE.py:NAME` in the runners of an object. This is the
  code of the main agent. The harness loads the file each time an object names it, so the newest
  text of the file runs. The object keeps the name, so a version of the object states its runner.

**The user decides if a plan can name code** (`plan_callables`, default: false). Reason: that code
runs in the process of Uniselector. With a state directory as the target, the process holds the
client of each principal, so the code can do what the human's principal can do. The action gate is
a rule of the protocol against a fault of the orchestrator (the brief, "Actions"). It is not a
sandbox for code. The brief gives the user the hard limits and the actions that come to a human,
and this setting is of the same kind: it decides what can act with no confirmation. A table with
`call` needs no such setting, because the user wrote the table.

- A fault at the load (no file, no function, an error in the code, a result that is not a
  function) rejects the plan or the answer with one message. The main agent reads it and corrects
  the code.
- A runner that raises gave no repair, as in D49: the next runner of the object gets the wake.
- A runner can return nothing. Then the run reads what it took: a repair, a form, or "the runner
  returned, and the run took nothing from it".
- The name of a runner in a table has no colon, so the two kinds of names cannot be the same.
- A function that calls a model counts its tokens with `spend`. The run cannot count the work of
  code that does not call `spend`.

Known limits. The run cannot stop a function that does not return: its thread stays, and the
object stays `in_repair` until the hard limits end the run. A function works with the principal of
the orchestrator, as a model runner does (D43), so the runtime gives it no action.

Tests: `test_a_runner_is_any_function_of_the_users_code_or_of_the_agents_code`,
`test_the_configuration_names_a_function_as_a_runner`.

## D54. A form of an output is the same binding

An object can carry forms of its output: for the name of each form, an ordered list of runners and
an optional check. A summary, an embedding and a table of keys are forms.

```
{"name": "totals", "handle": "hdl_...", "operation": {"capability": "dataset.head", "input": {"n": 50}},
 "forms": {"summary":   {"runners": ["local", "top"], "variables": ["The summary names one status or more."]},
           "embedding": {"runners": ["embedder"]}}}
```

Reason: ruling 4 of the user. An optional behavior of an object is a runner that is bound to an
object that exists already. The core has no engine for it and stores no vectors. This is also the
first fix for F50: the state keeps the first part and the last part of a long output (D38), so a
new value in the middle did not reach the decision model. A form is small, and the state shows it
beside the output.

| Rule | Reason |
| --- | --- |
| A form has no object of its own. It is a part of an object, and it uses `Run._rebind`, the runner log and the events of that object | Ruling 3: update the objects, do not add paths |
| With no `forms`, the object makes no form and no runner starts | Ruling 4: when the binding is absent, the object offers no such behavior |
| The runner of a form starts at each new output of the object, and it works beside the loop (D49) | A form of an old output is not a model of the object now (Conant and Ashby) |
| The state shows each form with `changes_behind`: the count of the changes of the output after the output that the form is of | The decision model and the main agent must know how old a form is |
| The value of a form is a bounded JSON value (D36), or `{"handle": ID}` | The output of a runner stays an ordinary data handle. The run keeps the id only |
| For a form in a handle, the run gives the main agent's principal each capability of the handle that is not an action, and it adds the handle to the lineage | The form is for a reader. The runner works as the orchestrator's principal, so the main agent cannot read its handle with no grant |
| The check of a form is sentences, and the decision model judges them at a look while the form is of the newest output | The same check as for an object (D41). A look does not judge a form of an older output: the runner already makes a new one |
| A form that does not pass its check gives no wake. The run removes the value, records what was not true in `tried`, and binds the next runner | The existing path of a rebind. A form is not the work of the plan, so its fault does not stop the main agent |
| A runner that gives no form binds the next runner | Same |
| A form with no check is good when a runner gives it, and the first runner is bound again | With no stated check, nothing can reject the form |
| After the last runner, the main agent gets one wake with `object`, `form` and `tried`. Then the form stays empty until the next output | The order of escalation of the brief. One wake for each output, so a form that no runner can make does not wake the main agent in a cycle |
| A form that passes its check binds the first runner again | A later runner works for one output, as for a repair (D42) |
| A new version of an object keeps the forms of its name, with no values. `forms: {}` removes them | The binding is a part of the object, as `runners` is. A runner that repairs an object does not know its forms |
| The end of a run waits for a form of the newest output | The last summary shows the forms, and the main agent reads them |
| An action has no forms | An action runs one time, and its result is in the lineage |
| A runner list of a form cannot name `main` or `human` | The main agent gets the wake after the last runner. A human does not write a summary at each output |

A model runner makes a form with the same loop and the same reading tools as a repair. It has
another instruction text and the final tool `form` in place of `repair` and `to_main`.

Later decisions replace four rules of this decision. The runner of a form works only when a wake
is due, and the end of a run does not wait for a form (D55, D58). A model runner makes a form with
one request (D57). A runner that fails leaves the list, and no rule binds the first runner again
(D59). The live check below is the check of the rules before those decisions.

**Where the vectors are.** Not in Uniselector. An embedding runner is a function of the user (D53).
It writes its vectors where it chooses, registers a DataHandles handle for them, and gives the id
to `submit`. The task `tasks/forms.py` has one such function. Its vectors come from a hash of
words, so the task checks the binding and not an engine.

Known limits.

- Each form that a run takes makes one more look, so forms add to the looks (F49).
- An object whose output changes at each reading starts the runner of each form again each time.
  The runner of a slow form can be behind its output all the time (`changes_behind` shows it).
- The decision model cannot compare a summary with the part of the output that the state cut. A
  check of a form rejects only what the decision model can see. So the check of a form states what
  the form shows ("The summary names one status or more."), not a relation to the full output
  ("The summary names each status that the table has.").
- A form that waits for its check is in the state already, so the decision model reads it at the
  same look that judges it.

Tests: `tests/test_forms.py`. The test of a form by a model runner is now
`test_a_model_runner_makes_a_form_from_the_whole_output_with_one_request` (D57).

**The live check** (`tasks/forms.py`, `forms_20261116_214113`). Two runs watched one table of 18
rows with the variable "The table of the object `totals` has only the statuses paid, pending and
refunded." The table had 782 characters, and the state keeps 600. One run had the object with no
forms. The other run had a summary form (the runner `local`, `qwen3.8-27b`) and an embedding form
(a function of the task). Then five orders with the status `disputed` came to the region north, in
the part of the table that the state does not keep.

| | No forms | Two forms |
| --- | --- | --- |
| The probability of the variable at the look after the new status | 0.998 | 0.257 |
| Wakes | 0 | 1, 8.3 s after the new output |
| Looks | 3 | 7 |
| Tokens of the decision model | 2,106 | 5,616 |
| Tokens of the runner `local`, for two summaries | 0 | 7,590 (4.4 s and 5.0 s) |

The new summary said: "Statuses: paid, pending, refunded, and disputed (disputed appears only in
north)." The function wrote 18 vectors to a file for the first output, in 0.1 s, and the run got
the handle. Each number is from one run. The first three runs of this task found two faults (F65,
F66).

## D55. A binding is silent while each variable is inside its limits

A runner of an object and a runner of a form do nothing until a look finds a statement that is
not true. On the path where each essential variable is inside its limits, an object with bindings
costs the same as the same object with none.

Reason: ruling 5 of the user. In the Homeostat, the uniselector of a unit does not move while the
essential variable of the unit is inside its limits. A position of the switch that is not in use
has no effect on the unit. The measurements before this decision showed bindings that were not
optional: the session with model runners took 22% more time and cost 42% more, and the run with
two forms made 7 looks where the run with none made 3 (D54, the live check).

| Rule | Reason |
| --- | --- |
| No runner starts while each statement is true | The second loop acts by a step, and a step comes only when a variable leaves its limits |
| The runner of a form does not start at a new output of the object (this replaces a rule of D54) | A form at each output made one more look and one more request for each output, also when nobody read the form |
| A form with no value adds no line to the text of a look | The decision model must get the same text for an object with a binding and with none |
| The check of a form is asked only while the form has a value | With no value there is nothing to judge |
| When a look finds each statement true, the run removes the value of each form | The failure is over. A form of an old output is not a model of the object now (Conant and Ashby) |

The test `test_a_binding_adds_nothing_while_each_variable_is_inside_its_limits` states the rule as
a comparison. The same object is in two runs: one with no binding, and one with a runner and a
form. The output changes two times. The decision model gets the same text and the same questions
in the two runs. No runner starts. The run with the bindings spends only the tokens of its looks,
and its events are only `object` and `observation`.

What a binding still costs with no failure: one request for the graph of the handle when the
object is built (D56), and one grant of the sources of the handle to the orchestrator's principal
(D43). The two are at the submit of the plan. The loop has none of them.

## D56. The state shows how a handle is made, and a replacement by a runner gets one more sentence

The state of each object has `made`: the operations of the graph that made its handle, in their
order, as one bounded line of text. The text of a look shows it:

```
- totals (computation): dataset.head {"n": 50}
  output: {"columns": ["region", "total", "n"], "rows": [["north", 21515.21, 109], ...]}
  the handle is made by: dataset.filter {"where": [{"column": "status", "op": "eq", "value": "paid"}]} → dataset.aggregate {"group_by": ["region"], ...}
```

Reason: ruling 5 of the user, "the viability check must name the conditions that matter." In F64 a
local model left out the filter on `status`, and the repair held. The first cause was the check:
no sentence stated the filter. But a sentence about the filter was also not possible to judge.
The state showed the output of the object, and a table of totals looks the same with the filter
and with none. The decision model judges from the state only, so the state must show what a
sentence about a filter is about.

- The run reads the graph one time when a version of an object is built (`lineage.graph_of`). The
  loop makes no request for it.
- The line has the bound of each value of the state (D36).
- An object on a resource has no line: no operation made its handle.
- The main agent writes the check. The tool text of `plan_submit` says that a check rejects only
  what it states, and that a check of an object with runners must state each condition that a
  replacement must keep: "It sums only the paid orders."

**One more sentence for a replacement by a runner.** When a runner replaced an object, the state
shows also `made_before`: how the handle was made before the replacement. At the one look that
judges the replacement, the run adds one sentence to the check of the object (`decide.KEEPS`):

> The handle of this version is made by the same operations as the version before it: the same
> filters, the same groups and the same metrics. Only the name of a column can differ.

Reason: the main agent cannot know each condition before a break. This sentence is the default
condition of each replacement: a repair changes a name or a value, and it does not change what the
object computes. A look asks it one time for each replacement, so it adds nothing to the quiet
path. It is a sentence for the same decision model at the same look. It is not a second check.

| Rule | Reason |
| --- | --- |
| `made_before` is the `made` text of the version before the trial: the last version that a look found good, or that the main agent wrote | A replacement must be like a version that held, not like a bad replacement. A bad replacement leaves before the next runner works (D61) |
| A replacement by the main agent has no `made_before` and no such sentence | The main agent decides what the object computes. It can change a filter with intent |
| An answer of the main agent to a wake about an object removes `made_before` | The main agent read what the runner changed. The version that it leaves or writes is its own |
| A look that finds the replacement good removes `made_before` | The trial is over |

A probe of the decision model before the build (8 looks, 4,848 tokens): the sentence "It sums only
the paid orders." got 0.9989 with the filter in the line, 0.064 with no filter, and 0.0006 with a
filter on another value. `tasks/probe.py` has these cases now.

**The words of the sentence.** The first form of the sentence was "... made by the same operations
as the version before it, and no filter, group or metric is gone or new." It did not separate a
replacement with another group: the model reads `group_by: ["status"]` in the place of
`group_by: ["region"]` as a group that is still there. The form above states what can differ.
One look for each case and each form, with the sentence as the only statement
(`docs/evidence/probe_20261116_v6/keeps_wording.json`, 4,675 tokens):

| Case | True | First form | Form above |
| --- | --- | --- | --- |
| A replacement changes only the name of a column | yes | 0.9891 | 0.9972 |
| A replacement changes the names of two columns | yes | 0.9761 | 0.9662 |
| A replacement drops the filter | no | 0.0048 | 0.0044 |
| A replacement has another group | no | 0.4839 | 0.0218 |
| A replacement drops a metric | no | 0.0182 | 0.0164 |

Known limits.

- The `made` line is in each look of each object with a derived handle, with a binding or with
  none. In one look of the decision model, a line of 217 characters (one filter and one aggregate)
  added 78 tokens: 477 with the line and 399 with none
  (`docs/evidence/probe_20261116_v6/made_tokens.json`).
- The line is bounded. A graph that is longer than the bound shows its first and its last
  operations (D38), so a filter in the middle of a long graph is not in the text.
- The sentence `KEEPS` compares two texts. It does not run the two versions on the same data.

Tests: `test_the_runner_of_an_object_repairs_it_and_the_main_agent_sleeps`,
`test_a_replacement_that_drops_a_condition_does_not_pass_the_check_that_names_it`,
`test_only_the_look_that_judges_a_replacement_has_the_sentence_about_it`,
`test_one_look_is_one_call_to_the_decisions_api_with_one_question_for_each_variable`.

## D57. A model runner makes one request for one replacement

A model runner gets one question and gives one answer. It has no tools to read with, and it has no
second turn. This replaces the loop of D43 and D50, and the setting `wake_tokens`.

Reason: ruling 5 of the user. "Rebinding is a single replacement of the runner on the existing
object, not a new repair loop that accumulates context." In two wakes, the runner `top` read with
its tools until it spent 1,090,476 tokens, and it gave no result (F57, F61). A loop of a model
with tools is a second generative path beside the main agent. The framing has one generative
agent, and the parts of an object.

**What the one request has.** The run builds it from what the object carries already:

| Key | What it is |
| --- | --- |
| `object` | The state of the object: its operation, how its handle is made, its error or output, its check |
| `graph` | The DataHandles graph that made the handle: each operation node with its id and its input |
| `faults_now` | The fault of each node of that graph that fails now, with the columns that the source has now |
| `sources` | The ids of the handles that the handle was made from |
| `not_true_now` | The statements that the look found not true |
| `tried` | What each runner before this one tried |
| `other_objects`, `limits`, `notes` | The rest of the state |

- For `faults_now`, the runner runs the graph one time before its request. Reason: a describe of
  a source can be old (`docs/GAPS.md`, G18), and the error of the object says which column is
  gone but not which columns the source has now. The fault of the failed node has them.
- An object on a resource has no graph, and the runner runs nothing before its request.

**What the one answer is.** One tool call:

- `repair`: the operation nodes of the graph that change, each with its id and its new operation,
  and optionally a new operation of the object. The runner changes those nodes in a copy of the
  graph, runs the graph one time, and gives the run the new handle. The model cannot add a node or
  remove one: a replacement keeps the structure of the object.
- `give_up`: the next runner of the object gets the wake.
- `to_main`: the wake is for the main agent (D51).

The run takes a fault as the result of the runner: a node id that the graph does not have, a
changed graph that fails, an answer that changes nothing, a text with no tool call. The model gets
no second request to correct it. The next look sees an object that is still broken, the runner
leaves the list (D59), and the next runner gets the fault text in `tried`.

**A form** is the same: one request with the whole output of the object, and one tool call `form`
or `give_up`. DataHandles bounds the size of the whole output.

**The cost of a replacement has a bound now.** One request holds the state and one graph, and the
answer is one tool call. The runner asks the run for the tokens that are left before its request,
and it makes no request when the budget is used up. The hard limits are the only limits (the
brief), and one request cannot grow as a loop can.

Removed with the loop: the tool loop of `ModelRunner`, the setting `wake_tokens`, and the use of
the DataHandles MCP tools by a model runner. A model table of the configuration has `url`,
`model`, and optionally `key_env` and `body`. A table with another key is refused.

Known limits.

- A model runner repairs what a change of the nodes of one graph can repair: a column with a new
  name, a value with a new form, another limit. A break that needs a new step is not in its reach,
  and it goes to the next runner or to the main agent. A function runner (D53) has no such limit.
- The runner runs the graph two times for a replacement: one time for the faults, one time for the
  new handle. DataHandles computes each run.

Live check: `docs/evidence/repair_20261116_232124`. A column of the feed got a new name, and the
local model `qwen3.8-27b` replaced the object with one request: 2,164 tokens and 3.3 s. The new
handle has the filter on the new column name, and the count is right. The look that judged the
replacement gave each statement 0.998 or more, the sentence of D56 too. From the break to that
look: 6.5 s, with no wake of the main agent. Before the break, the run made 4 looks, and the
runner made no request.

Tests: `test_a_model_runner_makes_one_request_and_changes_the_nodes_that_the_model_names` (a real
runtime, and a server of the test in place of the model),
`test_a_model_runner_makes_a_form_from_the_whole_output_with_one_request`,
`test_the_configuration_names_each_model_runner`.

## D58. A form is made only when a wake is due

The runner of a form works at one time only: a look found a statement that is not true, and a
wake is about to go. The wake waits for the forms of the objects that it shows. This replaces the
rule of D54 that a runner makes each form again at each new output.

Reason: D55. A form is for a reader, and the readers of an object are the ones that get a wake:
the runner of the object and the main agent. With no wake, nobody reads the form.

| Rule | Reason |
| --- | --- |
| A wake about one object waits for the forms of that object. A wake about a variable of the plan waits for the forms of each object | The reader gets the forms of what the wake shows |
| The look that found the failure records a `held` event with the names of the forms, and it sends no wake | The log shows why the wake is later than the look |
| When no runner of a form works, a look is due at once. That look judges the check of each form and sends the wake | The check of a form is a sentence for the same decision model (D54). A look at a part of the forms can send no wake, so it is not made |
| A runner that gave no form makes a look due at once | That look starts the next runner of the form |
| An object with no output has no form, and its wake does not wait | A form is of an output. An error shows none, and a version whose first run is not done shows none |
| A form that does not pass its check, and a runner that gives no form: the runner leaves the list (D59), and the next runner works. The wake still waits | The existing path of a rebind |
| After the last runner of a form, the wake goes with no value for the form, and with `tried` | A form that no runner can make does not stop the wake |
| When the failure is over before the form comes, no wake goes, and the run removes the form | The second look found each statement true. The first loop corrected the failure, or one reading was bad |
| A form is of the output at the wake. `changes_behind` counts the changes after it | Same as D54 |
| A look judges the check of a form while the form has a value, also when the output changed after it | The wake takes this form, and no runner makes a newer one. D54 had the other rule, because a runner made a form again at each output |
| The end of a run does not wait for a form | With no wake there is no reader |

**A form does not help the detector.** This follows from the rule, and it replaces the claim of
D54 and of its live check. Before this decision, a summary form at each output let the decision
model find a status in the part of a long output that the state does not keep. That use of a form
made the runner a part of the first loop: one model request at each output, 7,590 tokens of a
local model and 7 looks where 3 were enough.

The design rule is now: **an object shows its essential variable in a small output.** When the
variable is "the table has only the statuses paid, pending and refunded", the main agent builds
one more object that counts the rows for each status. That object has three rows, the state keeps
all of it, and the decision model reads it with no runner. DataHandles computes it, so it costs no
model. The long table stays an object of its own, and a form of it serves the reader of a wake.
The tool text of `plan_submit` says this. `tasks/forms.py` has the two objects.

Known limits.

- A wake with forms is later than a wake with none, by the time of the runners of the forms.
  In the live check, the wake with two forms came 3.0 s later than the wake with none: 6.0 s and
  3.0 s after the new status. The request of the local model for the summary took 2.6 s. The run
  with the forms made 1 look more (7 and 6) and spent 3,137 tokens more: 2,015 of the local model
  and 1,122 of the look. While nothing failed, the two runs spent the same 4,597 tokens in 5 looks
  (`docs/evidence/forms_20261116_233244`).
- A value that is only in the cut part of a long output, with no small object that shows it, does
  not reach the decision model (F50). This is a limit of a plan, and the main agent can remove it.

Tests: `tests/test_forms.py` (8 tests).

## D59. A runner that fails leaves the list

The same look that judges an object removes a runner that failed. A runner fails when its
replacement does not hold, when it gives none, when it raises, or when its form does not pass its
check. It leaves the ordered list of the object (or of the form), and the next runner has its
place. After the last one, the runner of the object is the main agent. This replaces the rule of
D42 that an object binds its first runner again.

Reason: ruling 5 of the user. "Wrong or expensive runners are removed by the same viability check
that already exists. They are not left in place to be measured as overhead." The Homeostat does
not go back to a position that it left: a position that did not keep the variables inside their
limits is not the next choice.

| Rule | Reason |
| --- | --- |
| Only the one that wrote the replacement leaves the list when the replacement does not hold | The look judges a version. A runner that did not write it did not fail |
| A replacement that holds keeps its runner bound | That position keeps the variables inside their limits |
| A runner that gives the wake to the main agent (`to_main`) stays | It found no break of the object. It did not fail (D51) |
| A new version of an object keeps the shortened lists of its name, until the main agent states `runners` or `forms` again | A removal that the next version undoes is no removal. The main agent can bind a runner again with intent |
| A replacement that does not hold leaves with its runner: the object gets back the version from before the trial | The next runner must read the break that the first one read (D61) |
| The list of an object can hold `main` at any place. When the replacement of the main agent does not hold, `main` leaves the list, and the next runner gets the wake | The main agent is a runner like each other one. The check of the object judges its version too |
| The confirmation of an action keeps its two runners | `["main", "human"]` is the order of escalation of the brief. A denial is a decision, not a failure of a runner |

**A check can state what the runners can spend.** The state of an object shows `runner_tokens`:
the tokens that the runners of the object spent, in each version of it. The line is there only
when a runner spent tokens. So the main agent can write one more sentence in the check of an
object:

> The runners of this object spent less than 20000 tokens.

When the sentence is not true, the look treats it as each other sentence of the check: the
replacement did not hold, and the runner leaves the list. So an expensive runner is removed by the
existing check, and the limit is a sentence of the main agent. The harness has no numeric limit
for a runner (the brief: a guess of a model is not a hard limit; the two hard limits stay).

Known limits.

- The spend sentence is judged after the runner worked. It removes a runner that spent too much.
  It does not stop a request that runs. One request of a model runner has a bound (D57). A
  function runner has none (D53).
- A function runner that calls no `spend` shows no tokens.
- A replacement that does not hold shows until the look that judges it. The check of the object
  is not true in that time, and a variable of the plan that reads the object wakes the main agent
  (D52). After that look, the object has its version from before the trial (D61).

Tests: `test_a_runner_whose_replacement_does_not_hold_leaves_the_list`,
`test_a_check_can_state_what_the_runners_of_an_object_can_spend`,
`test_the_wake_comes_to_the_main_agent_after_the_last_runner`,
`test_the_main_agent_leaves_the_list_when_its_replacement_does_not_hold`,
`test_a_runner_whose_form_does_not_pass_its_check_leaves_the_list`,
`test_the_wake_goes_with_no_form_after_the_last_runner_of_the_form`.

## D60. The tool text asks for the meaning of a check, and for a goal that is not met yet

The tool text of the MCP server and of `plan_submit` gives the main agent two rules for the
sentences that it writes.

- **The check of an object with runners states meaning.** "It sums only the paid orders." The
  check does not name a column whose name can change: a sentence with the old name rejects the
  right replacement. The state shows how the handle is made (D56), so the decision model can judge
  a sentence about meaning.
- **A variable for a goal states that the goal is not met yet.** "The object `rows` shows fewer
  than 600 rows." A variable is true while the main agent can sleep, so the wake comes when the
  goal is met.

Reason: the first task run with a real main agent and the bindings of D55 to D59
(`schema_20261116_233410`). The bindings did what D55 to D59 say. No runner made a request before
the break, each wake of a model runner was one request, and `local` kept the filter. But the main
agent got 5 wakes, and its session cost 132% of the base. The log gave three causes, and each
cause was a sentence of the main agent.

| Cause | Wakes for the main agent | Failure |
| --- | --- | --- |
| The check named the column `amount`. It rejected the right replacement, `local` left the list, and `top` gave the wake to the main agent | 1, and 21.2 s from the look that rejected the replacement to the answer of the main agent | F67 |
| The goal was a sentence that is true only at the end | 2, in the first 6 s of the run | F68 |
| A variable of the plan stated the check of the object again | 1 | F69 |

The first cause came from the tool text of D56. It said: "state each condition that a replacement
must keep (the filter, the group, the columns)". The main agent did that. The decision model was
right at each of these looks: each sentence was not true of the state.

No code enforces the two rules, as for the rules of D51 and D52. The harness does not read a
sentence to find a column name or a goal: the model judges the sentences (the brief), and the
main agent writes them.

**Live check** (`schema_20261116_233942`): the same task, the same models, the new text. The base
(100%) is the cost of the main agent alone on this task. Each column is one run.

| | Before this decision | With the new text |
| --- | --- | --- |
| Answer | right | right |
| Wakes, and those for the main agent | 6, 5 | 2, 1 |
| Requests of a model runner | 2: `local` 2,813 tokens in 4.2 s, `top` 3,957 tokens in 17.4 s | 1: `local` 2,726 tokens in 4.2 s |
| Rebinds | 1 | 0 |
| From the look that found the break to the look that found the check of the object true | 27.8 s, with 2 wakes of the main agent | 6.0 s, with none |
| Turns of the main agent | 18 | 11 |
| Context of the main agent, first turn to peak | 10,148 to 23,567 | 10,237 to 15,360 |
| Session time | 87.5 s | 87.7 s |
| Cost of the main agent, of the base | 132% | 79% |
| Looks | 19 | 21 |
| Run tokens | 31,458 | 24,623 |

The check of the main agent in the second run: "The object shows one row for each region with a
total.", "It sums the amount only for the orders whose status is paid.", "It groups the orders by
region.", "The object runs with no error." The look that judged the replacement gave each of them
0.998 or more, and the sentence of D56 too (D 9).

Known limits.

- One run shows the gain. A main agent can write a sentence with a column name again, and the
  cost is then the cost of F67.
- The tool text is longer: the instructions of the server have 3,006 characters, and 2,245 before
  D55. The first turn of the main agent had 10,237 tokens of context in this run, and 8,611 in the
  run with model runners before D55 (`schema_20261116_195950`). The longer text gives about 500
  tokens of the difference. The cause of the rest is not known.
- The session is 60 s of feed and about 28 s of the main agent. A shorter repair does not make
  this session shorter, because the session waits for the end of the feed.

## D61. A replacement that does not hold leaves with its runner

When a look finds that the replacement of a runner does not hold, the object gets back the version
from before the trial. The runner leaves the list (D59), and its replacement leaves with it. That
look gives no wake, because its statements are about a version that left. The next look is due at
the next tick (D35), not after a silence. It reads the version that is back. The wake of the next runner is about that version.

Reason: a position of the uniselector that did not keep the variables inside their limits has no
effect on the next position. Ruling 3 of the user says the same about the object: "Only the binding
inside an existing object changes." D52 named the change as not built: "the object keeps its
version from before a trial". A live run then showed the cost (`docs/FAILURES.md`, F71): after a
wrong replacement left its runner, the next runner read the wrong version. It did not get the
error that the first runner got, and it gave no replacement.

| Rule | Reason |
| --- | --- |
| An object keeps the version that a runner replaced, until a look judges the replacement | One earlier version for each object in a trial. The run keeps no history of versions |
| The version that is back has the list of runners as it is now, and the tokens that each runner spent | A removal that the next version undoes is no removal (D59). The spend sentence counts each runner |
| The look that rejects a replacement gives no wake. The event `held` names the object in `back`, and the next look is due at the next tick | The statements of that look are about a version that left. The next runner must get the break of the version that it reads |
| Each wake about that version has `tried`: for each replacement that left, the runner, the `made` text, and the statements that were not true | The next runner does not make the same replacement. After the last runner, the main agent reads what each runner tried |
| A replacement that is right and spent too much leaves too | The spend sentence is a sentence of the check (D59). The look does not judge one sentence in a different way |
| A replacement by the main agent keeps no earlier version | The main agent decides what the object computes (D56). When its replacement does not hold, `main` leaves the list (D59), and its version stays for the next runner |

**Live check** (`repair_20261117_002143`): the repair task of D57 with two runners in order. The
first one is `careless`: a script with no model that replaces the broken filter with a filter that
cannot fail (`amount > 0`). The second one is `qwen3.8-27b`. The check of the object names the
condition: "It counts only the paid orders." The run has no main agent. `repair_20261117_000349`
is the same task on the code before this decision.

| | Before this decision | With this decision |
| --- | --- | --- |
| The look that judged the replacement of `careless` | "It counts only the paid orders." got 0.057 (D 7): outside | 0.054 (D 7): outside |
| `careless` | left the list (E 19) | left the list (E 19) |
| What the next runner read | the version of `careless`: the count 204, and no error | the version of the plan and its error (E 20), and `tried` with the filter of `careless` |
| The one request of `qwen3.8-27b` | 2,244 tokens in 5.5 s. A filter on the old column name `status`: "no such column: ['status']" (E 21) | 2,380 tokens in 4.2 s. A filter on the new column name `state` (E 24) |
| The look that judged that replacement | none | each statement got 0.998 or more (D 9) |
| The count of the object | 204. The right count is 139 | 139 |
| Wakes for a main agent | 1 (E 23) | 0 |
| Looks, rebinds, run tokens | 7, 2, 7,005 | 9, 1, 8,690 |
| From the break to the look that found the object good | no such look | 12.5 s |

The run cost 0.11% of the base (the base is the cost of the main agent alone on the 60 s schema
task). While nothing failed, the run made 4 looks with 2,582 tokens, and no runner made a request.
Each column is one run.

Known limits.

- A replacement that does not hold costs one look more and one tick more: the look of the version
  that is back (715 tokens, and 1.9 s to the wake of the next runner, in this run).
- A right replacement that spent too much is not kept. The next runner, or the main agent, makes
  it again.
- A wrong replacement shows from its write to the look that judges it: 0.5 s in this run. A
  variable of the plan that reads the object can wake the main agent in that time (D52).
- The sentence of D56 did not reject the replacement of `careless`: it got 0.949 (F72). The
  sentence of the check that names the condition rejected it.

Tests: `test_a_runner_whose_replacement_does_not_hold_leaves_the_list`,
`test_the_main_agent_reads_each_replacement_that_left`,
`test_a_check_can_state_what_the_runners_of_an_object_can_spend`,
`test_only_the_look_that_judges_a_replacement_has_the_sentence_about_it`.

## D62. The grade of a binding is the work that it moves off the main agent

A runner binding is graded by three questions. The cost of the main agent is one signal of the
second question. It is not the grade.

1. Can the main agent define the runners of an object, and bind them again?
2. Does the main agent sleep while a runner works?
3. Does the check still find a real failure, also when a runner makes it?

Reason: the ruling of the user of 2026-11-17. "The ultrastable split is working when work is moved
off the expensive coding agent onto a cheaper or free runner without losing viability." "Grade the
change by whether the coding agent can define and rebind those runners, whether the expensive
agent stays asleep while they work, and whether the viability check still catches real failures.
Token savings on the long wait remain useful evidence; they are no longer the only measure." A
free local model that takes a repair is a gain when the tokens of all runners do not fall, and
when a short task is not faster. In Ashby's terms: the step-function must bring the essential
variables back inside their limits. Which part makes the step is a matter of cost.

So the run tokens of a run with runners are not a failure when they are more than the run tokens
of a run with none. The token budget still counts each token (D48).

**Question 1: the main agent defines and binds the runners.**

| Evidence | Run |
| --- | --- |
| The main agent gave the object `totals` the runners `["local", "top"]` and a check of four sentences. The text of the tools was its only guide | `schema_20261116_233942`, `plan.json` |
| After `local` left the list of an object, the main agent wrote the object again at a wake, with a new check and the list `["local", "top"]` | `schema_20261116_233410`, E: the answer to `wake_5` |
| The main agent bound the human as the runner of one confirmation | `goal-pass_20261116_190342` |
| A function with no model was the first runner of an object. The configuration of the task named it | `repair_20261117_002143` |
| A plan names a function in a file that the main agent wrote (`FILE.py:NAME`) | Only a test: `test_a_runner_is_any_function_of_the_users_code_or_of_the_agents_code`. No live run |

**Question 2: the main agent sleeps while the runner works.** The schema task with the 60 s feed,
on the same text of the tools, with a real main agent (`claude-sonnet-5-5`). The base (100%) is
the cost of the main agent alone on this task. Each column is one run.

| | No runners (`schema_20261117_004251`) | `["local", "top"]` (`schema_20261116_233942`) |
| --- | --- | --- |
| Answer | right | right |
| Who repaired the object after the new column name | the main agent: 3 turns | `local` (`qwen3.8-27b`, no cost): one request, 2,726 tokens, 4.2 s |
| Wakes for the main agent | 2: the break, and the end | 1: the end |
| From the look that found the break to the look that found the object good | 8.1 s | 6.0 s |
| Turns of the main agent | 14 | 11 |
| Context of the main agent, first turn to peak | 10,166 to 16,947 | 10,237 to 15,360 |
| Cost of the main agent, of the base | 90% | 79% |
| Session time | 84.2 s | 87.7 s |
| Looks | 19 | 21 |
| Run tokens | 12,461 | 24,623 |
| Cost of the run, of the base | 0.2% | 0.4% |

The repair moved off the main agent: one wake, 3 turns, and 11% of the base. The run tokens are two
times as many, and the cost of the run grew by 0.2% of the base. In the repair task with two
runners and no main agent (`repair_20261117_002143`), the first runner failed, the second one
repaired the object, and no wake went to a main agent (D61).

The first task run with runners did not pass this question: the main agent got 5 wakes and cost
132% of the base (`schema_20261116_233410`). Each cause was a sentence of the main agent (F67 to
F69), and the text of the tools changed (D60).

**Question 3: the check finds a real failure.**

| Evidence | Run |
| --- | --- |
| In each task run, the first look after the break found it | the tables of `docs/MEASUREMENTS.md` |
| A runner wrote a wrong replacement that showed a count and no error. The sentence of the check that names the condition got 0.054. The runner left, its replacement left with it, and the next runner repaired the object | `repair_20261117_002143`, D 7 to D 9 |
| A check with a limit on the tokens of the runners removes a runner that spent more | Only a test: `test_a_check_can_state_what_the_runners_of_an_object_can_spend` |
| The detector check: 63 of 64 statements | `probe_20261117` |

The weak points of question 3:

- A check rejects only what it states. With no sentence about the filter, a replacement with no
  filter held (F64).
- The sentence that a look adds for a replacement by a runner is not a guard alone: it got 0.949
  for a replacement with another filter (F72).
- A check that names a column rejects the right replacement (F67).

Known limits.

- Each column and each row is one run. The difference of 11% of the base between the two sessions
  is one measurement, and no second run shows how much two equal sessions differ.
- The two task runs with runners ran before D61. In `schema_20261116_233942` no replacement was
  rejected, so the code of D61 did no work in that run.
- No live run has a main agent that defines a function runner. `plan_callables` is false by
  default, and the user decides (D53).
- The main agent is fast on this task: its repair took 6.1 s. A runner moves more work when the
  repair is frequent, or when the main agent must read much to make it. No run measured that.
