# Failures

Each entry says what happened, which part failed, and where the evidence is. The parts are: plan,
decision tree, candidate pool, abstain, question, action gate, summary, DataHandles. Two more
parts came up in the runs: the decision model (the model behind `decide` chose wrong with a good
set of options), and the state (the decision model did not get the information).

F1 to F38 are from runs with the first model behind `decide`. That model was a chat model, not a
decision model (F39). Those entries stay as the record of that build. "The decision model" in
F1 to F38 means that chat model. The runs after F39 use a decision model.

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

### Slow API, first run: `api_20261115_224841`

The task: get a report from a service that answers after 150 s, and give the region with the
highest revenue. The run ended `done` in 640 s with 61,275 tokens, 2 operations, 40 waits and 3
questions. The answer of the main agent was right. Its context went from 7,001 to 11,264 tokens.
The run did not get the report: its two operations failed. The run directory is
`runs/run_20261115_224850_2271`.

### F26. The pool did not have the path that the intent states

- Part: candidate pool.
- What happened: the intent was "Get the report at the path /reports/quarterly from the reports
  service". The options for `path` were the base address of the service and `none fits`. The
  model chose the base address (p 0.97), and `api.get` failed.
- Evidence: D 33; E 32.
- Fixed (DECISIONS D27).

### F27. The sub-agent did the work, and the run got no result

- Part: question.
- What happened: three decisions abstained, and each question went to the sub-agent. Each time
  the sub-agent made the slow request itself with the right path, read the report, and tried to
  answer with `{"region": "west", "revenue": 91377}`. The schema refused that. It then answered
  `step_done` two times, and one time it answered the text "west, revenue 91377" for the field
  `path`. The slow request ran three times (about 450 s of the 640 s). No operation of the run
  has the report as its result. The main agent found the answer in the input of a failed
  operation, and said so: "I could not read the final result directly".
- Evidence: Q 1, Q 2, Q 3; E 35, E 41, E 44; `record.json` (`answer`).
- Open. A sub-agent with read grants can do a read operation of the plan. The work then leaves
  no handle and no result in the run. This is also "`step_done` when the intent was not met".

### F28. The regulator waited when nothing ran

- Part: decision model. Also: plan.
- What happened: the first 31 decisions chose `wait` (p 0.76 to 1.00). No job ran, no stream was
  open, and no request was sent. The intent of the main agent said "The service is slow, so wait
  for it". The stall signal grew for 62 s before the model chose an operation.
- Evidence: D 1 to D 31; E 1 to E 31.
- Open.

### Slow code: `code_20261115_225936`

The task: run a script that takes 152 s, and give the checksum that it prints. The run ended
`done` in 181 s with 12,905 tokens, 5 decisions, 2 operations and 1 question. The answer was
right. The context of the main agent went from 6,986 to 9,443 tokens. The run directory is
`runs/run_20261115_225946_0704`.

### F29. The wait for the job ran inside a question

- Part: abstain, question.
- What happened: after `script.run` gave a job, the next decision abstained: `step_done` 0.51,
  `job.status` 0.32, `wait` 0.07. The abstain was right, because `step_done` was wrong. The
  sub-agent then looked at the job until it was complete (6 calls, 8,943 tokens) and answered
  `h2 job.result`. The run has 0 waits: the fast loop did not wait for the job.
- Evidence: D 3; Q 1; E 1, E 2.
- Open. The result was right, and the cost was one question. The fast loop had no part in the
  wait, so the stall signal had no look at the job during that time.

### Stream: `stream_20261115_230519`

The task: switch the heater on, read the room until 26 degrees or more, switch the heater off,
and report the last temperature. The run ended `done` in 181.7 s with 57,301 tokens, 57
decisions, 3 operations, 48 waits and 0 questions. The main agent approved 2 actions. The room
shows the heater on at reading 4 and off after reading 62 (26.253 degrees). The answer (26.28)
was right. The context of the main agent went from 7,003 to 12,795 tokens. The run directory is
`runs/run_20261115_230527_fbc5`.

An earlier try of this task has no record. The check of the driver asked the room for readings
that the room had not made, and stopped with `INVALID_INPUT: readings 50 to 99 are not all made
yet`. The check now reads up to the newest reading.

### F30. The waits were 87% of the spend

- Part: decision tree.
- What happened: 48 of the 57 decisions chose `wait` (p 0.78 or more). Each wait is one model
  call with the full state. The 48 calls used 49,731 of the 57,301 tokens. The brief makes a wait
  a choice of the decision model at each look, so this is the cost of the design, not a wrong
  choice.
- Evidence: `decisions.jsonl`, each line with `"choice": "wait"`; the outcome line.
- Open. The wait time is 2 s (D10). A longer wait time spends less and sees a change later.

### Slow API, second run: `api_20261115_230837`

The same task, after the fix of F26. `path` got `/reports/quarterly` (p 1.00). `api.get` gave a
job, and `job.result` gave the report. The answer of the main agent was right. The context of the
main agent went from 7,001 to 9,535 tokens. The run has no end line: see F32. The run directory
is `runs/run_20261115_230845_ca79`.

### F31. The run could not look while the request ran

- Part: DataHandles. Also: the hard limits.
- What happened: D 9 came 17.3 s after the start, and D 10 came at 168.5 s. No decision and no
  event is between them. The request took 150 s. The look at `h1` before D 10 waited for the
  request: DataHandles lets one caller at a time use a resource, and a job on the resource holds
  that lock (`docs/GAPS.md`, G11). A measurement outside the run: `observe` on a script handle
  took 3.73 s while a job of 4 s ran on it. The look was not bounded, so a longer job could hold
  a run past its wall time.
- Evidence: `decisions.jsonl` lines 9 and 10 (`t`); `events.jsonl` lines 7 and 8 (`job.result`
  took 7 ms).
- Fixed in part: the wall time bounds each look (D28;
  `test_the_wall_time_stops_a_run_whose_look_at_a_handle_waits_for_a_job`). Open: during a job on
  a resource, the fast loop cannot look at that resource, so it makes no decision and the stall
  signal does not grow.

### F32. The main agent left a run that worked, and the run had no end

- Part: summary. Also: the harness.
- What happened: the second `plan_status` returned `running`, with the report in the last
  results. The main agent took the answer from that summary and ended its session. The run was at
  intent 1 of 3: D 11 abstained (`step_done` 0.53, `h2 job.result` 0.41), and a question was with
  the sub-agent. The MCP server ended with the session. The run directory has no `summary.json`
  and no end line, and the record has `"runs": []`. D25 says that such a run ends with the state
  `left`. It did not: the server closed its runs only at the end of its input. A signal with
  its default action ends the process without that step.
- Evidence: `hook-input.jsonl` line 2 (`"state": "running"`); the last line of `events.jsonl`
  (a result, not an end); `record.json` (`runs`).
- Fixed: the server ends its runs on SIGTERM and SIGHUP, and the end line is the last line of the
  log (`test_a_run_ends_in_the_logs_when_the_client_ends_the_server`). Open: a summary of a run
  that works can hold the answer, and the main agent then leaves the run.

### Stream with a human-only action: `stream-human_20261115_231150`

The same stream task, with `room.heater` in `human_only`. The main agent passed the first request
to the human, told the user the command, and ended its session after 15.6 s. The task has no
result. The context of the main agent went from 7,007 to 9,941 tokens. The run directory is
`runs/run_20261115_231159_8c49`.

The hook worked: after the second `plan_status`, `uniselector-hook` gave the system message
"Uniselector: an action needs YOUR decision. room.heater on room with input {"on": true}. To
decide, run: uniselector-confirm ..." (`agent-stream.jsonl`, the second `hook_response` event).

### F33. The log did not show a request that went to the human

- Part: action gate.
- What happened: `plan_confirm` with `human` moved the request to the human. `events.jsonl` got
  no line for that. Its last line said `waiting_confirm`, with the main agent. The stand-in for
  the human in the driver reads that log, so it did nothing (`"human_stand_in": []`).
- Evidence: the last line of `events.jsonl`; `hook-input.jsonl` line 2 (`"state":
  "waiting_human"`).
- Fixed: the move writes a `waiting_human` line (`test_a_human_only_action_needs_the_human`).

### F34. A session with one turn ends when the main agent asks the user

- Part: the driver of the tasks.
- What happened: the main agent did the right thing for a session with a user. It gave the
  command and ended its turn. A `claude -p` session has one turn, so the session, the MCP server
  and the run ended there.
- Evidence: `record.json` (`answer`, `turns`: 6).
- Fixed in the driver: the prompt of this task says that the user cannot reply, and that the
  main agent must follow the run until the human decides.

### Sub-agent: `helper_20261115_231211`

The task: ask a helper agent a question about a long document. The helper reads the document in
7 parts with a model. The run ended `done` in 105.2 s with 9,797 tokens, 3 decisions, 1 operation
and 1 question. The answer was right. The context of the main agent went from 7,016 to 8,568
tokens. The run directory is `runs/run_20261115_231219_e4ae`.

### F35. The sub-agent asked the helper again

- Part: abstain, question. The same pattern as F27 and F29.
- What happened: `helper.ask` gave a job. The next decision abstained (top option 0.27). The
  sub-agent did not look at the job. It invoked `helper.ask` with the same question, got the
  answer, and answered `step_done`. The helper did the work two times. The result of the run's
  own job was not in the state of the run, so the main agent read it with one `job.result` call.
- Evidence: D 3; `questions.jsonl` line 1, calls 2 and 5; the `handle_invoke` call in
  `agent-stream.jsonl`.
- Open.

### F36. The spend of the helper is not in the budget

- Part: the hard limits. Also: DataHandles.
- What happened: the helper used 9,887 model tokens for one answer, and it answered two times.
  The run counted 9,797 tokens: its 3 decisions and its question. The hard budget did not see
  the spend of the resource (`docs/GAPS.md`, G12).
- Evidence: `"tokens": 9887` in the output of `helper.ask` (`questions.jsonl` line 1, call 2);
  the outcome line (`"tokens": 9797`).
- Open.

### Stream with a human-only action, second run: `stream-human_20261115_232558`

The same task, after the fixes of F33 and F34. The run ended `done` with 53,838 tokens, 56
decisions, 6 operations, 40 waits and 0 questions. Its clock shows 163.4 s: the clock stops while
a request waits. The main agent passed 6 requests to the human. The stand-in for the human
approved each one with `uniselector-confirm --url` and the token of the human principal, and the
gate ran each action (`human.log`). The heater went off after reading 62 (26.253 degrees). The
answer (26.28) was right. The context of the main agent went from 7,063 to 25,067 tokens. The run
directory is `runs/run_20261115_232603_f317`.

The hook ran after each of the 12 `plan_status` calls, and 5 of its outputs have the message.
The stand-in approved the first request before the next `plan_status` call, so that request has
no message.

### F37. The human got the same action five times

- Part: decision model, action gate. The same pattern as F16 and F24.
- What happened: the first `room.heater` with `on: true` gave `{"heater": true}`. The next four
  root decisions chose `room.heater` again (p 0.96, 0.94, 0.93, 0.85), with `step_done` at 0.03
  to 0.15. Each one is an action, so each one went to the human. The sixth root decision chose
  `step_done` (0.91). In the stream run without the human (`stream_20261115_230519`), the same
  model chose `step_done` after one `room.heater`.
- Evidence: D 1, D 3, D 5, D 7, D 9, D 11; `events.jsonl` lines 4, 10, 16, 22 and 28
  (`waiting_human`); `human.log`.
- Open. The gate did its work: no action ran without the human. The cost is four requests that
  the human did not need.

### F38. Each request added about 3,000 tokens to the context of the main agent

- Part: summary.
- What happened: the context of the main agent grew by 18,004 tokens, the most of all task
  runs. One request needs a `plan_confirm` call and one or two `plan_status` calls, and each
  summary is 3,100 to 4,500 characters. The 6 requests took 18 calls. Without F37 the run has 2
  requests.
- Evidence: `record.json` (`tool_calls`, `context_added`); the size of each line of
  `hook-input.jsonl`.
- Open.

### Slow API, third run: `api_20261115_232915`

The same task again, after the fixes of F31 and F32. The run ended `done` in 277.7 s with 32,351
tokens, 44 decisions, 2 operations, 38 waits and 0 questions. The answer was right. The context
of the main agent went from 7,001 to 9,392 tokens. The run directory is
`runs/run_20261115_232920_d132`.

Two open failures show again:

- F28: the first 38 decisions chose `wait` (p 0.81 or more) for 119.3 s, before any request. The
  intent said "The service is slow, so wait for the result". These waits used 27,383 of the
  32,351 tokens. Evidence: D 1 to D 38.
- F31: D 41 came at 120.8 s and D 42 at 272.4 s. The run made no decision while the request ran.
  Evidence: `decisions.jsonl` lines 41 and 42 (`t`).

### Check: a run ends `left` when the main agent leaves (`leave_20261115_233422`)

A real session submitted a plan and ended at once. The last line of `events.jsonl` is the end
line with the outcome `left`, and the run directory has `summary.json`. This is the fix of F32
with a real client.

## The model behind `decide`

### F39. The first build used a chat model as the decision model

- Part: the decision model. It is the root of the build, so each run before this entry has it.
- What happened: the brief names a decision model. A decision model is a class of model: it reads
  a state and typed questions, it gives one probability for each allowed option, and it writes no
  text (TypeSafe Jev, Perplexity pplx-decider, Cloudflare Clef, and others). The first build put
  a chat model (`ornith-1.5:9b` on Ollama) behind `decide`. It asked for one output token and
  read the log probabilities of the letters A to T. The builder did not look up the term, and the
  README said that the brief was done.
- Effect: the probabilities were not calibrated, so the abstain rule had no base. The limit of
  20 options came from `top_logprobs`, so the root was two decisions and the pool had 19
  candidates. A third abstain rule (`abstain_mass`) was there only because a chat model can put
  its probability on a token that is not an option.
- Evidence: `uniselector/decide.py` at commit `969418c` (`OllamaDecider`, `LABELS`); the README at
  that commit ("The decision model is a model on an Ollama server that gives log probabilities");
  `"decision_model": "ornith-1.5:9b"` in each `record.json` before `code_20261115_235541`.
- A replay shows the difference (`python -m uniselector_tasks.replay`). 20 logged decisions of
  `pipeline_20261115_223625` went to five decision models with the same state and options. Each
  model answered each decision. At D 11 and D 14 the chat model was below the abstain threshold
  with `step_done` on top (p 0.66 and p 0.52, the second option at 0.34 and 0.48). Each decision
  model chose `step_done` with p 0.95 to 1.00. At D 46 the chat model chose "add one more item"
  (p 0.75, the failure F21). Four of the five decision models chose "stop: the list is complete"
  (p 0.77 to 0.96), and `cloudflare/clef-flash` chose "add one more item" (p 0.86). The replay
  does not show which choice is right: the task runs below do.
- Evidence of the replay: `replay_20261115_235716/replay.txt`.
- Fixed (DECISIONS D4, D5, D17): `decide.Decider` sends one decision as one `choice` question to a
  Decisions API. The walk, the pool and the loop did not change, apart from the limit of options.

## Task runs with the decision model

The decision model was `perplexity/pplx-decider-v1.2`. The sub-agent model was `ornith-1.5:9b`.
Each of the six tasks ran one time, and each answer was right. The run directories are
`code_20261115_235541`, `pipeline_20261115_235850`, `stream_20261116_000054`,
`api_20261116_000457`, `helper_20261116_000738` and `stream-human_20261116_000825`.

In these runs the tokens of a run are of two kinds. The decision model used 58,930 (pipeline),
51,375 (stream) and 44,636 (stream with a human) input tokens. The sub-agent used the other
57,229, 90,258 and 76,629 tokens. The other three runs had no question.

### What the earlier failures did in these runs

One run of each task does not show that a failure is gone. It shows only that the failure did
not occur.

- F21 (lists grew) did not occur. The pipeline run made each list with the items that the
  intent names (`pipeline_20261115_235850` D 16 to D 19, D 25).
- F28 (waits when nothing ran) did not occur. The first decision of the API run chose `api.get`
  (`api_20261116_000457` D 1). The chat model had waited 38 times first.
- F30 is open. 40 of the 49 decisions of the stream run chose `wait`, and 32 of 44 in the run
  with a human.
- F31 is open, with the same cause (`docs/GAPS.md`, G11). The look at the resource waited for
  the job, so the run made no decision for 151 s (API), 153 s (code) and 38 s (helper). When
  the look returned, the job was done, and the decision model chose `job.result` (p 0.94 to
  0.98). These three runs made 4 to 6 decisions and no `wait`. Evidence: the `t` of D 3 and D 4
  in `api_20261116_000457`, and of D 2 and D 3 in `code_20261115_235541` and
  `helper_20261116_000738`.
- F35 (the sub-agent asked the helper again) did not occur: no decision abstained in the helper
  run.
- F36 is open: the tokens of the helper are not in the 2,760 tokens of the helper run.
- F37 (the human got the same action five times) did not occur: the human got 2 requests, one
  for each heater action.
- F38 is open. The context of the main agent grew by 9,760 tokens in the run with a human (2
  requests and 1 question) and by 7,984 tokens in the stream run (2 requests and 2 questions).

### F40. Two words of one phrase split the decision on a new column name

- Part: candidate pool, question.
- What happened: the intent was "Group by country and sum amount as total revenue." The field
  `metrics[0].as` is the name of a new column. The pool had the words `revenue` and `total` as
  two candidates, and not the phrase `total revenue`. The decision model gave `revenue` 0.43 and
  `total` 0.38, and the decision abstained. The sub-agent then did the work of the plan: it ran
  the aggregate, the sort and the limit itself (11 calls, 39,508 tokens). It answered with a
  sentence that states the final result. That sentence became the column name. The answer of
  the task was right, because a column can have any name.
- Evidence: `pipeline_20261115_235850` D 23 and D 24; `questions.jsonl` line 1; the input of
  `dataset.aggregate` at E 17.
- Open. Each pattern of the brief is here: a question that the pool should have answered, a
  sub-agent that did not answer the question that it got, and exploration that cost more than
  the question was worth.

### F41. A close decision on "show more options" became a question

- Part: abstain, candidate pool.
- What happened: for the field `n` of `dataset.head`, the first page had one value: 10, the
  default. The decision model gave "none of these: show more options" 0.59 and the default 0.41.
  The second option was close, so the decision abstained. The next page had the value 3, with
  the places "h7 dataset.count: rows; in the plan". The sub-agent used 17,721 tokens and 7 calls
  to answer 3.
- Evidence: `pipeline_20261115_235850` D 36 and D 37; `questions.jsonl` line 2 (`candidates`).
- Open. One more decision on the next page costs about 1,500 tokens.

### F42. Two operations that fit the intent made the decision abstain

- Part: abstain, question.
- What happened: the intent was "Read the room temperature until a reading shows 26 or more."
  `room.readings`, `room.latest` and `room.read` each carry it out. The decision model gave
  0.53, 0.30 and 0.11 in the first run, and 0.43, 0.42 and 0.07 in the second. The decision
  abstained in each run. In the first run the sub-agent made 16 calls, reached the cap of the
  question with 44,271 tokens, and the question went to the main agent, which answered `wait`.
  The next decision came 70 s after the one that abstained. In the second run the sub-agent
  answered `h1 room.readings` after 30,246 tokens, and the next decision came after 88 s.
- Evidence: `stream_20261116_000054` D 4, `questions.jsonl` lines 1 and 2, E 7;
  `stream-human_20261116_000825` D 4, `questions.jsonl` line 1.
- Open. The rule "the top two options are too close" cannot tell two good options from two
  doubtful options. A calibrated model splits its probability between options that are equal.
  The chat model of the first build did not abstain here, and its choice was not better
  informed.

### F43. An intent with no operation went to the sub-agent and then to the main agent

- Part: plan, abstain, question.
- What happened: the last intent was "Report the temperature of the last reading seen." No
  operation carries it out: the reading is in the results. The decision model gave `room.latest`
  0.39, `step_done` 0.33 and `wake_agent` 0.23 (second run: `step_done` 0.36, `wake_agent` 0.30,
  `room.latest` 0.29). The decision abstained. The sub-agent reached the cap in each run (45,987
  and 46,383 tokens), and the main agent answered `step_done`.
- Evidence: `stream_20261116_000054` D 49, `questions.jsonl` lines 3 and 4;
  `stream-human_20261116_000825` D 44, `questions.jsonl` lines 2 and 3.
- Open. The escalation went in the order of the brief, and the main agent settled the question.
  The cost was two sub-agent questions that could not have an answer in the data.

### F44. A question passed its cap

- Part: question.
- What happened: the cap of one question is 40,000 tokens. Three questions ended with 44,271,
  45,987 and 46,383 tokens. The sub-agent checks the cap after a model call, so the last call
  passes it.
- Evidence: `tokens` in `questions.jsonl` of `stream_20261116_000054` (lines 1 and 3) and of
  `stream-human_20261116_000825` (line 2).
- Open. The spend is in the hard budget of the run, so the hard limit holds.

### F45. The first baseline run was not a baseline, and the session denied an approval

- Part: the task driver. Also: action gate.
- What happened: the first baseline run told the main agent to do the stream task itself, and it
  still had the Uniselector tools. The main agent switched the heater on and asked for the newest
  reading four times. Then it handed the wait off with `plan_submit`. When the run asked for the
  confirmation of "heater off", the auto mode of Claude Code denied the `plan_confirm` call
  ("Undermining Oversight"): the prompt had told the main agent to do the work itself. The task
  ended with the heater on.
- Evidence: `stream-direct_20261116_002748`, `agent-stream.jsonl` (the calls `plan_submit` and
  `plan_confirm`, and the result of `plan_confirm`); `record.json` (`"right": false`).
- Fixed for the driver: a baseline session has no Uniselector server (`tasks/drive.py`,
  `--direct`). Open: a permission layer of the client can deny an approval of the main agent. The
  run then waits until its end, and the summary does not say why.

