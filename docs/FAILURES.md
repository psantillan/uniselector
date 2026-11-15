# Failures

Each entry says what happened, which part failed, and where the evidence is. The parts are: plan,
decision tree, candidate pool, abstain, question, action gate, summary, DataHandles. Two more
parts came up in the runs: the decision model (the model behind `decide` chose wrong with a good
set of options), and the state (the decision model did not get the information).

The evidence is in `docs/evidence/`. `dev/` has the runs from the development of the harness: the
orchestrator alone, with no main agent. The other directories have the task runs with a real main
agent. In a run directory, `decisions.jsonl` has one line for each decision, `questions.jsonl` one
line for each question, and `events.jsonl` each event. "D 5" means line 5 of `decisions.jsonl`,
"Q 2" line 2 of `questions.jsonl`, "E 4" line 4 of `events.jsonl`.

"Fixed" means that a change removed the failure in a later run or in a replay of the logged
decisions. "Open" means that the failure can occur again.

## Development runs

The plan of the first five runs was: "keep only the purchase events", then "count the rows", on a
dataset with the columns `id`, `customer_id`, `event_type`, `amount`. The plan of the other runs
was: "switch the heater on", "wait until the temperature is 18.5 or more", "switch the heater off",
on a simulated room.

### F1. A word of the intent was chosen as a column name

- Part: candidate pool.
- What happened: for the field `where[0].column`, the pool gave the words of the intent together
  with the column names. The decision model chose `purchase` with p 0.89, 0.66 and 0.75. `purchase`
  is a value of the column `event_type`. It is not a column.
- Evidence: `dev/run_20261115_214032_95f3` D 2; `dev/run_20261115_214323_3833` D 2;
  `dev/run_20261115_214543_de95` D 2.
- Fixed (DECISIONS D8): the pool puts the values with evidence for the field on the first page
  and shows some values of each column. Then: `event_type` with p 0.99
  (`dev/run_20261115_214958_1ce3` D 2).

### F2. The list form was chosen for the value of `eq`

- Part: decision tree.
- What happened: the schema of `where[0].value` is an `anyOf` of a scalar and a list. The model
  chose the list form (p 0.81) for the operator `eq`, then the word `keep` as the first item
  (p 0.65). The decision to stop the list abstained (p 0.60).
- Evidence: `dev/run_20261115_214032_95f3` D 4 to D 6.
- Open. The later runs chose the scalar form (p 0.93 to 0.98), but nothing in the schema ties the
  form to the operator (GAPS G10).

### F3. A failed input was chosen again

- Part: decision model.
- What happened: `dataset.filter` failed with `INVALID_INPUT: no such column: ['purchase']`. The
  error was in the state. The model chose the same column two more times, with p 0.98 and 0.99.
  An early wrong choice made each later choice consistent and wrong: the model added a second
  predicate with the same column.
- Evidence: `dev/run_20261115_214543_de95` E 4 to E 6; D 8, D 13, D 19.
- Open. The option text now marks a value that was part of a failed input (DECISIONS D15). No
  live run has tested the mark on this case yet.

### F4. The sub-agent answered a question that came from a wrong earlier choice

- Part: question.
- What happened: the walk asked for `where[1].column` and `where[1].value`: fields of a second
  predicate that the intent does not need. The sub-agent gave a correct fact for each one
  (`event_type`, 24,021 tokens; `"purchase"`, 21,466 tokens). It did not say that the first
  predicate was wrong. The second answer had quote characters inside the string. A third question
  (which operation is next) reached the token cap at 42,931 tokens. The run spent 108,512 tokens
  and made no result.
- Evidence: `dev/run_20261115_214543_de95` Q 1 to Q 3.
- Open. The cost of the questions was more than the cost of a new plan.

### F5. An input schema with no properties became an open value

- Part: decision tree.
- What happened: `dataset.count` takes an empty object. The walk made an open value of it, the
  pool had nothing, and a sub-agent spent 40,347 tokens on the question and reached the cap.
- Evidence: `dev/run_20261115_214958_1ce3` Q 1.
- Fixed: an object schema with no properties needs no decision. Then: the same plan ended `done`
  in 10.9 s with 5,771 tokens (`dev/run_20261115_215103_d316` E 8).

### F6. An operation of the next intent ran during the current intent

- Part: decision model.
- What happened: after the filter, the model did not choose `step_done`. It chose the new handle
  (p 0.78) and `dataset.count` (p 0.82) during the first intent. Then it chose `step_done` two
  times. The result is right, but the record says that the count belongs to the first intent.
- Evidence: `dev/run_20261115_215103_d316` D 7 to D 10.
- Open.

### F7. An action was requested again after it succeeded

- Part: decision model. The action gate worked: each request went to a confirmation.
- What happened: `room.heater {"on": true}` completed. The model chose `room.heater` again with
  p 0.73 and 0.81, and the main agent got a second request for the same action.
- Evidence: `dev/run_20261115_220530_66c0` D 3, D 5; `dev/run_20261115_220746_034f` D 3;
  `dev/run_20261115_221818_9f95` D 3 (p 0.69, after the change of D20).
- Open. A replay with the results by intent gave `step_done` with p 1.00 on two of these
  decisions, but the live run after the change repeated the action one time. The main agent sees
  the earlier result in the request and can deny it.

### F8. `step_done` for an intent with no operation

- Part: state.
- What happened: the model chose `step_done` for "wait until the temperature is 18.5 or more"
  (p 0.995) with no reading of the temperature, and for "switch the heater off" (p 0.70) with the
  heater on. The run ended `done`. The main agent got no wake, and the heater stayed on.
- Evidence: `dev/run_20261115_220746_034f` D 6, D 7, E 12.
- Fixed (DECISIONS D20, change 1). Replay of D 6 and D 7 with the results by intent: `wait`
  p 0.96, and `room.heater` p 0.98. Live: `dev/run_20261115_221818_9f95` D 6 to D 9.

### F9. The regulator could not see the value that it waited for

- Part: state.
- What happened: the status of the room had the version and the heater, and no temperature. After
  one `wait`, the next decision abstained (`wait` 0.54, `step_done` 0.32). With the temperature
  in the status of the handle, the model still chose `wait` with p 0.90 to 1.00 at 21.5, 23.9 and
  25.6 degrees, for a limit of 18.5.
- Evidence: `dev/run_20261115_221105_08c6` D 5; `dev/run_20261115_221430_79bd` D 5 to D 7.
- Fixed (DECISIONS D20, changes 2 and 3): the result of a wait shows the stream after the wait.
  Live: one `wait` at 18.4 degrees, then `step_done` with p 1.00 at 23.1 degrees
  (`dev/run_20261115_221818_9f95` D 6, D 7).

### F10. A failed model call of the sub-agent ended the run

- Part: question.
- What happened: the model server answered HTTP 500 to a call of the sub-agent: "llama-server
  returned invalid tool call arguments for "answer": unexpected end of JSON input". The run ended
  with the state `failed`.
- Evidence: `dev/run_20261115_220530_66c0` E 10; `dev/run_20261115_221430_79bd` E 18.
- Fixed (DECISIONS D21): the question goes to the main agent.

### F11. A question about the next operation costs more than it is worth

- Part: abstain, question.
- What happened: when the root decision abstains, the sub-agent gets the question "which
  operation is next". In three cases it spent 19,461 tokens (a right answer), 44,056 tokens (cap,
  no answer) and 42,931 tokens (cap, no answer). One decision costs about 700 tokens. In the
  second case the sub-agent read the temperature in its second call, which settled the question,
  and then described each capability until the cap.
- Evidence: `dev/run_20261115_220746_034f` Q 1; `dev/run_20261115_221105_08c6` Q 1;
  `dev/run_20261115_214543_de95` Q 3.
- Open.

### F12. The names of column types were strong candidates for a column name

- Part: candidate pool.
- What happened: the probe `dataset.schema` gives `columns > type`. The path has the word
  `columns`, so the pool put `BIGINT`, `VARCHAR` and `DOUBLE` on the first page for the field
  `column`, next to the column names.
- Evidence: `tests/test_parts.py`, the test of the first page, asserts only that the column names
  are on the page. `dev/run_20261115_214958_1ce3` D 2 lists the options.
- Open. The model did not choose a type name in a run.

## Task runs with a real main agent

`tasks/drive.py` starts Claude Code in non-interactive mode as the main agent. The main agent gets
a plain task and writes the plan itself. Each directory has `record.json` (the measurements),
`agent-stream.jsonl` (the events of the main agent), `hook-input.jsonl` (what the hook got), and
`runs/` (the logs of each run).

### Pipeline, first run: `pipeline_20261115_222110`

The task: the three countries with the highest total purchase revenue, from `events` and
`customers`. The main agent made a plan of five intents. The run did one intent and spent 311,008
of 400,000 tokens in 531 s. The main agent then gave up and left. It gave no answer. Its context
went from 7,016 to 25,533 tokens. The run directory is `runs/run_20261115_222119_dac6`.

### F13. No `step_done` after a handle was chosen

- Part: decision tree.
- What happened: after the filter, the root chose the new handle h3 (p 0.89) and not `step_done`
  (p 0.11). The next decision had only the operations of h3. It abstained (`dataset.head` 0.57,
  `dataset.count` 0.31): no option was right. The sub-agent answered `h3 dataset.join` for 43,998
  tokens. The join belongs to the second intent.
- Evidence: D 8, D 12, D 13; Q 2.
- Fixed (DECISIONS D24): the control options are on the two levels.

### F14. The join key got a customer id and not a column name

- Part: candidate pool.
- What happened: for `on[0].right`, the first page had 19 values of the column `customer_id`
  (`c0270`, `c0000`, ...) and no column name. The decision model chose `c0000` (p 0.90), then
  `c0001` (0.58), `c0002` (0.81) and `c0270` (0.59). `dataset.join` failed three times with `no
  such column`. The mark of a failed input (DECISIONS D15) worked: the model did not choose a
  failed value again. It chose the next id.
- Evidence: D 16, D 26, D 36, D 60; E 13, E 15, E 18.
- Fixed (DECISIONS D22).

### F15. Each structure decision that abstained became a question

- Part: abstain, question.
- What happened: six decisions on an optional field or on one more list item were below the
  threshold (top p 0.54 to 0.64). Each one became a question. The six questions cost 202,180
  tokens: 65% of the spend of the run. Three reached the cap. One answer was `true` for the field
  `n` with the reason "true (→1, valid)". Three went to the main agent, which said: "I can only
  answer true or false".
- Evidence: D 10, D 17, D 21, D 27, D 37, D 62; Q 1, Q 3, Q 4, Q 6, Q 10, Q 11.
- Fixed (DECISIONS D23).

### F16. Operations with a result ran again

- Part: decision model.
- What happened: the filter ran a second time on its own output (p 0.98). `dataset.head` ran three
  times in sequence on the same handle with the same input (p 0.80, 0.90, 0.71). The system text
  says "Do not repeat an operation that has a result already".
- Evidence: D 40 to D 54; E 19 to E 22.
- Open. The same kind of failure as F7.

### F17. The main agent left a run that waited, and said that it stopped the run

- Part: summary.
- What happened: with 89% of the budget spent and one of five intents done, the main agent did
  not answer the pending question. It ended its session and told the user "I stopped it". It has
  no tool to stop a run. The run ended because its process ended, with no end event in the logs.
- Evidence: `agent-stream.jsonl`, the last message; `runs/run_20261115_222119_dac6/events.jsonl`
  has no `end` event, and the directory has no `summary.json`.
- Fixed in part (DECISIONS D25): the run now ends with the state `left`, and the logs say so. The
  main agent still has no tool to stop a run.

### F18. Two calls of `plan_status` returned with nothing to do

- Part: summary.
- What happened: the main agent called `plan_status` with `wait_seconds` 60, then 120, then 240.
  The first two calls returned `running`. Each one put a summary in the context of the main agent: the
  hook input of each call is about 4,280 characters.
- Evidence: `hook-input.jsonl`, lines 1 and 2; `agent-stream.jsonl`.
- Open. The tool text does not say how long a run takes.

### F19. A summary of a waiting run is large

- Part: summary.
- What happened: the hook inputs of the six status calls were 4,283 to 10,207 characters. The largest parts of one were
  the pending question (3,254 characters), the last results (2,010) and the handles (1,518). Each
  of the last results was the same output of `dataset.head`.
- Evidence: `hook-input.jsonl`.
- Fixed in part (DECISIONS D26): the pending question has no repeated parts now.

### Pipeline, second run: `pipeline_20261115_223625`

The same task, after the changes of D22 to D26. The main agent made a plan of four intents. The
run ended `done` in 436 s with 324,769 tokens, 124 decisions, 12 operations and 6 questions. The
main agent gave the right answer. Its context went from 7,016 to 17,339 tokens. The filter and
the join were right at the first try: the join key was `customer_id` with p 0.99 (D 22). The run
directory is `runs/run_20261115_223634_8145`.

### F20. The run said `done`, and the last intent was not met

- Part: question. Also: decision model.
- What happened: the intent was "Sort by total revenue descending and keep the top 3 rows". The
  model left out the optional field `descending` (p 0.72), so the sort was ascending, and no
  limit ran. The next root decision abstained (top p 0.39). The sub-agent answered `step_done`
  with the reason "sorted descending, and limited to top 3". The two statements are false: it
  spent 41,609 tokens and 15 calls, and it did not check them. The main agent got no wake. It
  found the fault itself: the summary showed the input of the sort, and it read the eight rows.
- Evidence: D 121, D 124; Q 6; E 30, E 31; `agent-stream.jsonl`, the last two messages.
- Open. This is "no wake when something was wrong", and a sub-agent that did not explore.

### F21. Lists grew past what the intent names

- Part: decision tree (DECISIONS D23).
- What happened: a structure decision below the threshold now takes its top option. In this run
  16 structure decisions were below the threshold, and 6 took a wrong option. Five of them added
  one more item to a list (top p 0.54 to 0.67): `group_by` got six items and `metrics` got six.
  One left out the field `column` of a `sum` metric (p 0.64). The operations failed with errors,
  and the fourth try was right.
- Evidence: D 34, D 48, D 50, D 66, D 70, D 74; E 20 to E 23.
- Open. In the first run the top option was right in 6 of 6 cases. In this run it was right in 10
  of 16. A question for each one costs 23,000 to 44,000 tokens (F15), and a failed operation
  costs about 100 ms and the decisions of one more try.

### F22. The sub-agent gave the text "none fits" as a value

- Part: question.
- What happened: for the sixth item of `group_by`, the sub-agent answered the string
  `"none fits"` with quote characters inside, for 35,657 tokens. The reason of the answer says
  that the list must have one item. The schema allows each string, so the value went into the
  input, and the operation failed.
- Evidence: Q 3; E 21.
- Open. The question has no way to say "this field must not exist".

### F23. The same question was asked two times

- Part: question.
- What happened: the root decision abstained two times during the first intent (top p 0.66 and
  0.52). The two questions were the same, the two answers were the same, and each cost 19,466
  tokens. An answer to a root question is not kept.
- Evidence: D 11, D 14; Q 1, Q 2.
- Open.

### F24. The aggregate ran again after it succeeded

- Part: decision model.
- What happened: after the right aggregate (h5), the model chose `dataset.aggregate` on h5 two
  more times, with no `group_by` and no column. The two calls failed. Then it chose `step_done`.
- Evidence: D 103 to D 117; E 27 to E 29.
- Open. The same kind of failure as F7 and F16.

### F25. Three of four `plan_status` calls returned with nothing to do

- Part: summary.
- What happened: as in F18. The main agent waited 60, 120, 180 and 180 seconds. The first three
  calls returned `running`, and each one added 1,800 to 2,600 tokens to its context.
- Evidence: `agent-stream.jsonl`; `record.json` (`tool_calls`).
- Open.
