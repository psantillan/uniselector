# Failures

Each entry says what happened, which part failed, and where the evidence is. The parts are: plan,
decision tree, candidate pool, abstain, question, action gate, summary, DataHandles. Two more
parts came up in the runs: the decision model (the model behind `decide` chose wrong with a good
set of options), and the state (the decision model did not get the information).

F1 to F38 are from runs with the first model behind `decide`. That model was a chat model, not a
decision model (F39). Those entries stay as the record of that build. "The decision model" in
F1 to F38 means that chat model. The runs after F39 use a decision model.

F1 to F45 are from the first design: a decision model that chose each operation of a plan. F46 is
the failure of that design. The code of the parts "decision tree", "candidate pool", "abstain" and
"question" is gone (`docs/DECISIONS.md`, D29), so an open entry of those parts cannot occur again.
The entries stay as the record. F47 and later are from the ultrastable design. Its parts are:
plan (the objects and the variables that the main agent wrote), look (when the decision model
looks), decision model, state, wake, action gate, summary, DataHandles, and the task driver.

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

## The design

### F46. The decision model sat in the first loop

- Part: the design (the loop and the decision tree of the brief, Steps 3 and 4).
- What happened: the decision model chose each operation and each field of its input. It was a
  router in the path of the work. The user found the fault on 2026-11-16: "You drifted by
  optimizing it for accurate classification on every step."
- Evidence: the pipeline task has 6 operations. With Uniselector it took 123.9 s
  (`pipeline_20261115_235850`: 38 decisions, 2 questions, 116,159 run tokens). The main agent
  alone took 19.9 s (`pipeline-direct_20261116_002722`), and its context grew by the same 4,900
  tokens. A bounded sequence needs no second control layer. The stream task improved
  (`stream_20261116_000054`: 12 turns, against 86 in `stream-direct_20261116_003123`), and 40 of
  its 49 decisions chose `wait`: the gain came from the observation that the run took away from
  the main agent, not from the choices.
- Fixed: the ultrastable design (`docs/DECISIONS.md`, D29). The main agent builds the objects, the
  objects run with no model, and the decision model judges only if each essential variable is
  inside its limits.

## Task runs of the ultrastable design

Each run is one task with a real main agent (Claude Code in headless mode, `claude-sonnet-5-5`)
on 2026-11-16 (UTC). The decision model was `perplexity/pplx-decider-v1.2`. The runs used the
working tree that became the commit of this entry, with one difference: `cut` kept only the first
part of a long value (F50). In a run directory, "D 7" means line 7 of `decisions.jsonl` (one line
for each look) and "E 16" means line 16 of `events.jsonl`.

| Run | Answer | Looks | Wakes | Operations | Confirmations |
| --- | --- | --- | --- | --- | --- |
| `schema_20261116_162735` | right | 19 | 2 | 38 | 0 |
| `schema_20261116_163338` | right | 22 | 3 | 42 | 0 |
| `shift_20261116_164133` | right | 15 | 2 | 38 | 0 |
| `silent_20261116_164259` | right | 54 | 2 | 65 | 0 |
| `goal_20261116_164515` | right | 55 | 1 | 56 | 0 |
| `goal-human_20261116_164819` | right | 58 | 1 | 61 | 2, each one by the human |
| `code_20261116_165137` | right | 6 | 1 | 1 | 0 |
| `api_20261116_165326` | right | 6 | 1 | 1 | 0 |

What the brief said to watch for, in these runs:

- **A wake when nothing was wrong:** one (F47). **No wake when something was:** none. Each break
  gave a wake at the first look after it. In `schema_20261116_162735` the column got its new name
  18.0 s after the feed started, and the look that woke the main agent was at 20.1 s of run time
  (D 7). In `silent_20261116_164259` the variable said "within the last 10 seconds", and the wake
  came at the look where the object showed "last change 10.0 s ago" (D 16).
- **An early choice that makes each later choice consistent and wrong:** none seen. The detector
  makes no choice that a later look builds on: each look judges the state of that moment.
- **Probes that cost more than the decisions they inform:** the design has no probes now.
- **An action that reached confirmation and should not have, or the reverse:** F52.
- **A summary that was not enough to continue from a fresh context:** not tested. Each main agent
  kept its context for the whole task, and no run made the main agent compact.

One more event is not a failure of Uniselector. In `silent_20261116_164259` the client refused
the first `plan_submit` call: the main agent wrote JSON that did not parse
(`agent-stream.jsonl`, "InputValidationError"). The second call was valid.

### F47. A variable about something that the state does not show gave a wake with no fault

- Part: plan.
- What happened: the main agent wrote the variable "The totals object reads without error: the
  columns region, status and amount still exist." The object showed the totals with the columns
  `region` and `total`. The state had no `status` and no `amount`, so the decision model gave the
  sentence 0.14 at the first look, and the run woke the main agent 0.1 s after its start. Nothing
  was broken. The main agent wrote a new variable ("... shows a row for each region with a number
  in total, and shows no error"), and the run went on.
- Evidence: `schema_20261116_163338`, D 1 (`probs` 1.0, 0.98, 0.14) and the `answer` event of
  `wake_1` in `events.jsonl`.
- Open. The cost was one wake. The decision model was right: the sentence was not true of the
  state. The tool text does not say that a variable must be about what an object shows.

### F48. The driver did not start the feed of a baseline run, and then started it too early

- Part: the task driver.
- What happened: in the baseline mode, the driver waited for a new handle of its own DataHandles
  client before it started the changes of the feed. The handles that the main agent derived did
  not show there, so the feed stayed at 200 rows. The main agent asked for the row count and the
  schema 67 times in 73 turns, its context went from 5,852 to 36,702 tokens, and the session cost
  $0.52. It then said that the feed did not change, and it gave no final totals. That answer was
  correct for the feed that it saw. The run is void as a baseline.
- The first fix looked for the text `handle_invoke` in the event stream of the main agent. The
  first event of the stream lists the tools, so the feed started 1.5 s into the session, before
  the main agent built anything (`schema-direct_20261116_163205`, `facts.changes`). That run is a
  valid task run, and its feed ran 16 s earlier in the session than in the Uniselector run.
- Evidence: `schema-direct_20261116_162916` (`record.json`: `"right": false`, `"rows": 200`, no
  `changes`); `schema-direct_20261116_163205`.
- Fixed: the driver starts the feed at the first `handle_invoke` call of the main agent
  (`FIRST_OPERATION` in `tasks/drive.py`; `schema-direct_20261116_163736` started at 7.5 s).

### F49. The decision model looks at each change, so the looks grow with the feed

- Part: look.
- What happened: an object that shows each new reading changes at each reading, and each change
  gives a look (`docs/DECISIONS.md`, D32). In `goal_20261116_164515` the run made 55 looks for 56
  operations in 164 s. 54 of the 55 looks found each variable inside its limits. The looks cost
  21,273 tokens ($0.000425), about 387 tokens for each one.
- Evidence: the outcome line of `decisions.jsonl` in `goal_20261116_164515` (55 looks),
  `silent_20261116_164259` (54) and `goal-human_20261116_164819` (58). Each look of these runs
  has the reason `change`. `code_20261116_165137` and `api_20261116_165326` have 5 looks with the
  reason `silence` in 90 s: at 4, 8, 16, 32 and 64 s.
- A variable about the spend was built for this entry and removed again: it gave a wrong wake
  (F53, and `docs/DECISIONS.md`, D48). What stays is a tool text and one field (D46).
- Open. The money is small. The hard budget is the limit: at 387 tokens for each look and one
  change each 3 s, a budget of 400,000 tokens ends a run after about 1,030 looks, which is about
  50 minutes. A main agent can build an object whose output changes only when the work needs
  attention (a count of the rows that break a rule). Then a change is rare, and the silent looks
  double. No run measured that.

### F50. An output was longer than the bound of the state

- Part: state.
- What happened: the main agent built an object that shows the totals for each region and each
  status. After the new status, that table had 16 rows and more than 600 characters. The state
  kept the first 600. The row with `settled` was row 4, so the decision model saw it, and the
  wake was right. With that row at the end of the table, the decision model does not see the new
  status.
- Evidence: `shift_20261116_164133`, E 16 (the output ends with "(cut:"), and D 7.
- Changed: a long value keeps its first and its last part now (`docs/DECISIONS.md`, D38), and the
  tool text says so. Open: a new value in the middle of a long output does not reach the decision
  model. The object that fits is small (one row for each status). The main agent built that
  object at the wake.

### F51. The main agent left a run at a wake

- Part: wake.
- What happened: the task was one slow request. The variable was "The report at
  /reports/quarterly is not yet returned with status 200." The response came after 90 s, the run
  woke the main agent, and the summary had the report. The main agent gave the answer to the user
  and ended its session. It did not answer the wake with `end: true`. The run ended with the
  state `left`.
- Evidence: `api_20261116_165326`, `events.jsonl` (the last events are `woke` and `end`, with no
  `answer`), and `record.json` (`"outcome": "left"`). In `code_20261116_165137` the main agent
  did answer with `end: true`.
- Open, with no change in version 0.3. The answer of the task was right. The log of the run says `left`, not `done`, so a review
  of the logs cannot tell this from a main agent that failed. A run on an `api` handle that goes
  on after the wake also sends the request again at each tick (`docs/GAPS.md`, G15).

### F52. The main agent switched the heater itself, with no confirmation

- Part: action gate.
- What happened: in the task with no human-only capability, the main agent switched the heater
  on with a direct `handle_invoke`, gave the run one object that reads the newest temperature,
  slept, and switched the heater off with a second direct `handle_invoke` after the wake. No
  request for a confirmation existed. This is the gate as designed: the main agent is the
  approver, and its principal holds the action (`docs/DECISIONS.md`, D1). The gate is in the path
  of the orchestrator only.
- In the task with `room.heater` marked human-only, the main agent gave each switch to the run as
  an object, and the human approved each one (2 confirmations, 2 human requests). The prompt told
  it to do that. The protocol does not stop a direct invoke of that capability by the main
  agent's principal (`docs/GAPS.md`, G1).
- Evidence: `goal_20261116_164515`, `record.json` (`tool_calls`: `handle_invoke` 2,
  `confirmations` 0); `goal-human_20261116_164819`, the two `confirmation` events with
  `"by": "human"`.
- Open in DataHandles (G1).
- Changed after this entry: the human's principal can hold the action alone (`docs/DECISIONS.md`,
  D44). Then the protocol refuses a direct invoke by the main agent. In
  `goal-held_20261116_190023` the main agent made no `handle_invoke` call, and the human approved
  and ran each switch (`record.json`, `tool_calls`; the `confirmation` events of the two runs).
  With no such arrangement the gap is open: in `goal-pass_20261116_190342` the main agent's
  principal held the heater. It made no direct invoke in that run (`plan_confirm` 1, `handle_invoke`
  0), and nothing but its prompt stopped one.

## The first task run with model runners

One task run on 2026-11-16 (UTC): `schema_20261116_181501`, with the runners `local`
(`ornith-1.5:9b` behind Ollama) and `top` (`deepseek/deepseek-v4.1-flash-1017`), a budget of
400,000 tokens, and `wake_tokens` of 60,000 and 120,000. In its run directory
`runs/run_20261116_181523_7ae4`, "D 3" means line 3 of `decisions.jsonl`, "E 11" means line 11 of
`events.jsonl`, and "R 1" means line 1 of `runners.jsonl`.

The run ended with the outcome `limit`: "the token budget is spent: 401411 of 400000" (D 5),
after 832.5 s of run time, 4 looks, 3 wakes and 4 wakes of a model runner. No runner gave a
repair. The answer of the main agent was right: after each wake came back to it, it built the
totals on the new column itself (`record.json`).

### F53. The spend variable woke a runner 4 s after the start

- Part: look.
- What happened: at the third look, 4.1 s into the run, the state had `projected_tokens` 589,352
  against a budget of 400,000. The run had spent 2,239 tokens. The statement about the projected
  tokens was the only one outside its limits. Its owner was the object `rowcount`, and the wake
  went to the runner `local`. The feed of the task was 60 s long.
- Evidence: D 3 (`"outside": ["The projected tokens are not more than the token budget."]`, and
  the limits of the state); R 1 (`"wake": "wake_2"`, `"object": "rowcount"`).
- Changed: the variable is removed (`docs/DECISIONS.md`, D48).

### F54. A local model with 4,096 tokens of context made no call of a tool

- Part: runner.
- What happened: the runner `local` got two wakes. In the first one it made 18 requests, and 14
  of the answers had no call of a tool and no text. It spent 63,742 tokens in 516.9 s and gave no
  repair. The second wake was the same: 20 requests, 14 with no call, 63,713 tokens, 185.1 s.
  The server gave the model 4,096 tokens of context: the default of Ollama. The mean request of
  the first wake counted 3,541 tokens, so the conversation was at that limit. The same model
  repaired a smaller object in one wake four minutes before the task run
  (`repair_20261116_181108`: 7 calls, 19,242 tokens, 58.3 s; a mean of 2,749 tokens for each
  request). The log does not have the text that the server cut, so the cause is not proved.
- Evidence: R 1 and R 3 (`calls` with `"tool": null`, `tokens`, `ms`).
- Changed: an answer with no call ends the wake, and the next runner gets it (D50). The local
  runner of the task runs is now a model whose server gives it 245,760 tokens. Not changed: the
  harness does not read the context of a server. The example configuration tells the user to
  read it.

### F55. A wake of a model runner stopped the run

- Part: wake.
- What happened: the run waited for the runner. Look 3 was at 4.1 s and look 4 was at 619.8 s. In
  that time no object ran its operation again, and the decision model saw nothing. The schema of
  the feed broke in that time, and no look found it until the two runners had ended.
- Evidence: D 3 and D 4 (`elapsed_s`); E 11 and E 13 (`ms`: 516,862 and 98,177).
- Changed: a model runner works beside the loop (D49).

### F56. The runner `top` reached its token limit two times, with no repair

- Part: runner.
- What happened: the runner `top` read the object, its sources and the logs: 18 calls in the
  first wake, 27 in the second one. Each call was a fair step. It reached `wake_tokens` (120,000)
  after 137,145 and 132,273 tokens. The largest result of a tool was 10,236 characters
  (`log.audit`). The limit counted each request, and each request sends the whole conversation
  again, so 18 requests were the whole wake.
- Evidence: R 2 and R 4 (`reason`, `tokens`, `calls`).
- Changed: the limits are larger, and each one has its origin (D50). Open: a runner that reads
  much before it writes still pays for the whole conversation at each request. The first wake of
  `top` with the new limit is F57.

## Task runs with a local model that has its full context

Three task runs on 2026-11-16 (UTC), after D48 to D50. The runner `local` was `qwen3.8-27b` behind
vLLM, with 245,760 tokens of context. The runner `top` was `deepseek/deepseek-v4.1-flash-1017`.
The budget was 5,000,000 tokens, and `wake_tokens` was 2,000,000 and 1,000,000. "D", "E" and "R"
name lines of the logs of a run, as above.

| Task | Answer | Session time | Turns of the main agent | Outcome of the run | Looks | Wakes | Wakes of a model runner | Confirmations | Run tokens | Cost of the run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| schema, 60 s feed, with model runners | right | 768.3 s | 16 | `done` after 734.1 s | 29 | 2 | 3 | 0 | 778,229 | $0.134062 |
| goal, only the human holds the heater | right | 199.2 s | 12 | `failed` after 153.8 s, then a second run: `done` after 18.1 s | 51 and 6 | 0 and 2 | 0 | 1 and 1, each one by the human | 31,707 and 3,095 | $0.000634 and $0.000062 |
| goal, the main agent passes one decision to the human | right | 210.0 s | 14 | `done` after 174.3 s | 59 | 2 | 0 | 2: one by the human, one by the main agent | 31,155 | $0.000623 |

The evidence, in the order of the table: `schema_20261116_184734`, `goal-held_20261116_190023`,
`goal-pass_20261116_190342`.

**What worked.** In the schema run the column got its new name, and the look at 20.5 s found the
error of the object `totals` (E 17). The runner `local` read the object and its source with 11
calls, and it wrote the repair in 15.3 s for 27,991 tokens (R 1, E 28). The repair held. The main
agent slept through it: it got no wake about the schema. In that time the other objects ran, and
5 looks held a statement for the result of the repair (the `held` events).

### F57. A goal went to the model runners as a check of an object

- Part: plan, runner.
- What happened: the main agent gave the object `row_count` the check "The count is less than 600
  while the feed still gets rows." and the runners `["local", "top"]`. The prompt of the driver
  told it to give each object the runners. The feed ended at 600 rows, so the check was not true,
  and the object had no break. The wake went to `local` at 62.6 s. `local` found that the object
  works, and it stopped with a text after 88.5 s and 43,614 tokens. It had no tool to say more.
  The wake went to `top`. `top` made 36 calls in 580.5 s, spent 676,367 tokens ($0.133457), and
  stopped with no repair and no text. The main agent got the wake at 734.1 s. The feed was 60 s
  long.
- These are two items of the watch list of the brief: a sub-agent that answered a question about
  intent, and a sub-agent whose exploration cost more than the question was worth. The larger
  limits of D50 made the cost possible: with the limit before D50, `top` stopped after 120,000
  tokens.
- Evidence: `schema_20261116_184734`, run `run_20261116_184758_f7f1`: E 50 to E 56, and R 2 and
  R 3.
- Changed: a runner can give the wake to the main agent with `to_main`; the tool text says that
  the check of an object is about the object only; the prompt of the driver does not tell the
  main agent to give each object the runners (`docs/DECISIONS.md`, D51).
- Live check: `schema_20261116_192536`. The main agent wrote the goal as a variable of the plan
  ("The row count of row_count is less than 600."), so this failure did not occur again. The run
  showed three more failures: F60, F61 and F62.

### F58. One HTTP 429 of the Decisions API ended a run

- Part: look.
- What happened: the run made a look at each change of the temperature: 51 looks in 153.8 s. The
  API answered request 52 with HTTP 429, "Too Many Requests". The harness took it as a fault of
  its own, and the run ended with the outcome `failed`. Each object worked at that time, and the
  heater was on. The main agent read the reason, and it sent a second plan that ended the work.
  The log does not show why the API refused the request.
- Evidence: `goal-held_20261116_190023`, run `run_20261116_190035_1998`: E 57 (the traceback) and
  E 58 (`"outcome": "failed"`, `"looks": 51`); run `run_20261116_190314_0412`, E 17.
- Changed: the decision model is busy, and the look stays due (`docs/DECISIONS.md`, D51). Open:
  the looks at each change are still the cause of the many requests (F49).

### F59. The log shows a rebind from `main` to `main`

- Part: log.
- What happened: the object `temp` had the runners `["main"]`. Its repair did not hold, and the
  step to the next place of the list wrote a `rebind` event "from main to main". The run counted
  1 rebind. Nothing changed.
- Evidence: `goal-pass_20261116_190342`, run `run_20261116_190358_3aa3`, E 72 and E 76
  (`"rebinds": 1`).
- Changed: the step writes an event and counts a rebind only when the name of the runner changes
  (D51).

## The live check of D51

One task run on 2026-11-16 (UTC), with the code of D51 and the same models and limits as the three
runs above. Evidence: `schema_20261116_192536`, run `run_20261116_192556_404d`.

| Task | Answer | Session time | Turns of the main agent | Outcome of the run | Looks | Wakes | Wakes of a model runner | Rebinds | Run tokens | Cost of the run | Cost of the main agent |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| schema, 60 s feed, with model runners | right | 533.8 s | 18 | `done` after 496.2 s | 29 | 4 | 3 | 3 | 553,684 | $0.090073 | $0.1130 |

**What worked.** The look at 20.9 s found the error of the object `totals` (E 20). The runner
`local` read the object and its sources with 10 calls, and it wrote the repair at 68.1 s: an
aggregate on the filtered handle with the column `amount_usd` (R 2: 78,746 tokens, 45.8 s; E 56).
The repair held, and the totals of the four regions are the true values. The context of the main
agent went from 8,531 to 18,409 tokens.

### F60. A runner replaced an object that worked

- Part: plan, runner.
- What happened: the main agent gave the object `schema` (the operation `dataset.schema` on the
  feed) the check "The columns are id, region, status and amount." and the runners
  `["local", "top"]`. The feed got the column `amount_usd` in place of `amount`, so the check was
  not true. The object worked: it showed the columns, with no error. Only a change of the data
  could make the check true again. The wake went to `local` (E 22). `local` did not call
  `to_main`. It replaced the object with the head of an aggregate by region (R 1: 9 calls, 28,894
  tokens, 39.3 s; E 49). The object `schema` then showed totals, and no columns. The trial did not
  hold, and the object went to `top` (E 52, E 53). `top` got the version of the failed repair in
  a new wake with no `tried`, so it did not see the operation that the main agent wrote.
- This is an item of the watch list of the brief: a sub-agent that guessed instead of exploring.
- Evidence: E 22, E 47 to E 53, R 1.
- Changed: the tool text says that a model runner cannot change the data, so a check that only a
  change of the data can make true again is a variable of the plan (`docs/DECISIONS.md`, D52).
- Open: a model runner can replace an object that works. After a repair that does not hold, the
  next runner gets the version of that repair.

### F61. The runner `top` read for 430 s and gave no result

- Part: runner.
- What happened: `top` got the object `schema` after F60. It made 22 calls in 429.7 s (13
  `handle_invoke`, 9 `handle_describe`) and spent 414,109 tokens ($0.089434). It read the
  operation log of the source two times with a limit of 200 rows (`log.own`, `log.audit`). Its
  last reply had no text and no call of a tool, so the wake ended with no repair (R 3, E 65). This
  is the second wake of `top` with no result: the first one spent 676,367 tokens (F57). In two
  wakes, `top` called no tool that ends a wake.
- This is an item of the watch list of the brief: a sub-agent whose exploration cost more than the
  question was worth.
- Evidence: R 3, E 53, E 65.
- Open. The limits did not change: one wake is one turn of an agent (D50). With D52 the main agent
  does not wait for the wake, and the end of the run stops the runner at its next request.

### F62. The goal of the plan waited 432 s for a runner of another object

- Part: wake.
- What happened: the feed ended at 600 rows. The variable of the plan "The row count of row_count
  is less than 600." was not true from look 21, at 62.3 s (D 21). At that time `top` worked on the
  object `schema` (F61). A statement with no object waited while a runner worked (D49), so 7 looks
  held the goal (E 58 to E 64). The main agent got the wake at 494.2 s, when `top` stopped (E 67).
  The session took 533.8 s for a feed of 60 s. The variable "The columns of the feed are id,
  region, status and amount." waited from 20.9 s to 494.2 s in the same way (E 24 to E 46). The
  main agent wrote that variable to learn of a change of the schema.
- This is an item of the watch list of the brief: no wake when something was wrong.
- Evidence: D 21, E 24 to E 46, E 58 to E 67, E 76.
- Changed: a variable of the plan wakes the main agent at once, also while a runner works
  (`docs/DECISIONS.md`, D52). The tests
  `test_a_variable_with_no_object_wakes_the_main_agent_while_a_runner_works_and_waits_after_the_answer`
  and `test_one_look_wakes_the_runner_of_the_object_and_the_main_agent` cover it.
- Live check: `schema_20261116_195950` (below). The goal woke the main agent at 62.0 s, 0.4 s
  after the object `row_count` showed 600 rows.

## The live check of D52

One task run on 2026-11-16 (UTC), with the code of D52 and the same models and limits. Evidence:
`schema_20261116_195950`, run `run_20261116_200008_6843`.

| Task | Answer | Session time | Turns of the main agent | Outcome of the run | Looks | Wakes | Starts of a model runner | Rebinds | Run tokens | Cost of the run | Cost of the main agent |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| schema, 60 s feed, with model runners | right | 101.2 s | 19 | `done` after 56.1 s | 19 | 4 | 1 | 0 | 50,636 | $0.000479 | $0.1320 |

**What worked.** Look 7, at 20.6 s, found the error of the object `region_totals` and the variable
of the plan about the columns. That one look gave the object to `local` (E 20) and woke the main
agent (E 21, `wake_2`). The main agent answered after 3.7 s with a new check for the object
`source_schema`, and `local` went on in that time. The main agent answered the next wake with
`objects: []`, and 3 looks held its statements until `local` ended (E 27 to E 33). The goal of the
plan woke the main agent at 62.0 s (E 53). The session took 101.2 s. The session before D52 took
533.8 s.

### F63. A variable that states the check of an object again gave a wake with nothing new

- Part: plan.
- What happened: in its answer to `wake_2`, the main agent wrote a new variable of the plan: "The
  object region_totals shows one row for each region with a numeric total." This is the check of
  the object `region_totals`, and that object was with `local` at the time. The tool text says
  not to state the check of an object again as a variable of the plan. The variable was new, so
  the next look woke the main agent again (`wake_3`, 2.0 s after the answer). The main agent
  answered with `objects: []` after 3.7 s.
- This is an item of the watch list of the brief: a wake when nothing was wrong that the main
  agent did not know.
- Evidence: E 23, E 26, E 27, D 8.
- Open. The cost was one wake. D52 names this cost. The code does not read a sentence to find the
  object that it is about: only the decision model reads sentences.

### F64. A repair held, and its totals were wrong

- Part: runner, plan.
- What happened: the object `region_totals` showed the total of `amount` for each region, over the
  orders with the status `paid`. After the new column name, `local` built a new aggregate on the
  feed, not on the filtered handle. It described the filter capability, and it made no filter (R 1,
  11 calls, 26,706 tokens, 18.3 s). The new version showed the totals of each order, paid or not:
  its first output counted 109 orders for north, and the full feed has 61 paid orders for north. The check of the object was "The output has one row for
  each region with the columns region, total, and n." and "The column total is a number and is not
  null." The two sentences were true for the wrong totals, so the repair held (D 12). The object
  showed wrong totals from 39.4 s to 80.3 s. The notes of the plan named the filter, and the runner
  had the notes.
- The summary of `wake_4` showed that `wake_1` wrote the object. The main agent read the new
  version, found that the filter was gone, and built the object again with the filter (E 54,
  E 55). The answer of the task was right. In its two runs before this one, `local` kept the filter.
- This is an item of the watch list of the brief: an early choice that makes each later choice
  consistent and wrong. Here the main agent found it at the next wake.
- Evidence: R 1 (the `dataset.aggregate` call on the handle of the feed), E 37, E 56, D 12, and
  the answer in `record.json`.
- Open. A check rejects only what it states: this check stated the form of the output, and no
  sentence stated which orders the totals count. The uniselector of the Homeostat also keeps each
  configuration that holds the essential variables inside their limits. The text of the runner
  says already: "Change only what is broken: the new version shows the same thing as the old one."
  `handle_describe` of the filtered handle showed the old column name (`docs/GAPS.md`, G14), so the
  runner could not see from it that the filter still worked.
