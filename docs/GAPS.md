# Gaps in the DataHandles protocol

Each entry is something that Uniselector needed and DataHandles 0.4 does not give. Each entry has
the workaround that Uniselector uses. The workaround stays within the protocol.

`tests/test_gate.py` shows G1 and G2 with a real runtime.

## G1. A direct invoke of an action needs no approval

`graph.approve` gates a graph. It does not gate `invoke`. A principal that holds `room.heater`
can call it directly, and the runtime runs it.

Effect: "the orchestrator holds `graph.run` but not `graph.approve`" does not gate an action. If
the orchestrator holds the action capability, it can invoke the action with no graph.

Workaround: the orchestrator's principal never holds an action capability. The main agent's
principal holds it. An approved action runs as a one-node graph of the main agent's principal
(`uniselector/gate.py`). The runtime then refuses each action that the orchestrator code tries, also
when that code is wrong.

## G2. An approval is keyed by the structure of the graph, not by who approves

Two equal graphs from two principals are one graph to the approval check. A principal that holds
the capabilities of a graph can approve its own copy, and the approval counts for the other copy.

Effect: a principal that can run an action can also approve it. A separate approver is not
possible when both principals hold the action.

Workaround: the same as G1. Only the main agent's principal (or the human's) holds the action, so
only it can approve.

Proposal for DataHandles: a grant that permits an action only with an approval from the grantor.
Then the orchestrator could hold `graph.run` on the action and the brief's design would hold as
written.

## G3. A grant does not reach a derived handle of another type, and no grant means "all but actions"

A grant by capability name covers the derived handles that have that capability. `room.record`
gives a `window` handle with other capability names, so the grant does not reach it. The grant
`*` reaches it, but `*` includes the actions.

Workaround: after each operation that makes a handle, the gate reads the capabilities of the new
handle as the main agent and grants the orchestrator each one that is not an action
(`Gate.delegate`).

## G4. A principal cannot see a capability that it does not hold

`describe` lists only the capabilities that the caller holds. The orchestrator does not hold the
actions, so the decision model would not know that an action exists.

Workaround: `Gate.delegate` returns the descriptors of the actions, read as the main agent. The
decision model sees them as options. To choose one opens a confirmation request.

## G5. A caller cannot cancel a synchronous operation

`job.cancel` stops a job. A synchronous `invoke` has no handle to cancel.

Effect: a synchronous call that does not return would hold the run past the wall-time limit.

Workaround: each operation of a run is a job (`background=True`). `graph.run` of an approved
action has no job form, so the run waits for it in a second thread and stops at the limit
(`Run._bounded`). The action itself is not cancelled.

## G6. A derived handle has no version

`info.version` of a computation handle is `""`, and `observe` gives the same value until a
parent changes. Uniselector caches probes by (handle id, observed version). This is correct for
one run but it gives no key that two runs can share.

Workaround: none needed in one run.

## G7. A job handle belongs to its submitter only

The job of an operation that the orchestrator starts is a handle of the orchestrator. The main
agent, which owns the resource, holds nothing on it and cannot read its result after a wake.

Workaround: the orchestrator grants the main agent `*` on each job that it starts.

## G8. A handle input does not say which type of handle it needs

`dataset.join` takes `right: {"$handle": id}`. The schema does not say that the handle must be a
dataset. The decision model sees each live handle as an option.

Workaround: none. The runtime refuses a wrong type, and the decision model sees the error.

## G9. The SDK does not export the definition of an action

The specification defines an action: `effects.write`, `effects.external`, and not
`execution.idempotent`. The runtime has this test in `datahandles.core`, which a protocol client
must not import.

Workaround: `gate.is_action` states the same rule from the descriptor.

## G10. An input schema has no field descriptions

`dataset.filter` has `column: {"type": "string"}`. Nothing says that the string must be a column
name of the dataset. A model must infer it from the field name.

Effect: the candidate pool cannot tell which observed strings are legal for a field. See
`docs/FAILURES.md`, F1.

Workaround: the pool ranks a value by the place that it comes from (`docs/DECISIONS.md`, D8).

Proposal for DataHandles: a `description` for each field, and a way to say "one of the column
names of the target".
