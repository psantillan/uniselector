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
parent changes. Uniselector runs the operation of an object again when the observed version is
new. This is correct for one run but it gives no key that two runs can share.

Workaround: none needed in one run.

## G7. A job handle belongs to its submitter only

The job of an operation that the orchestrator starts is a handle of the orchestrator. The main
agent, which owns the resource, holds nothing on it and cannot read its result after a wake.

Workaround: none needed now. The main agent reads the handle of the object, not the job
(`docs/DECISIONS.md`, D35). The first design granted the main agent `*` on each job.

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

## G11. A look at a resource waits while a job runs on it

The runtime lets one caller at a time use a resource (`Locked` in `packages/runtime/adapter.py`).
A job holds that lock while it runs. Only `discover` does not wait. `observe` and each read
operation on the same resource wait until the job ends. Measured: `observe` took 3.73 s while a
job of 4 s ran on a script handle.

Effect: while a job runs on a resource, a regulator cannot look at that resource. A look that is
not bounded can hold a run past its wall time. See `docs/FAILURES.md`, F31. With two objects on
one slow resource, the `observe` of the second object waits for the job of the first, and the
loop does not reach the other objects in that time.

Workaround: the wall time bounds each `observe` (`Run._bounded`). The run does not observe an
object while the job of that object runs. The run still waits when another object has a job on
the same resource.

Proposal for DataHandles: `observe` does not take the lock of the resource, or it returns the
last observation with a mark that the resource is busy.

## G12. A result does not state its cost

An operation can spend model tokens or money at the resource. A result has no standard field for
that. The helper of the task runs puts `tokens` in its output, and no caller can know that this
field is a cost.

Effect: the hard budget of a run counts the decision model and the sub-agents only. See
`docs/FAILURES.md`, F36.

Workaround: none in the protocol. The budget is a limit on the spend of the regulator, not on the
spend of the resources.

Proposal for DataHandles: a `cost` object in the result of an operation, with a unit, and the
same object in `job.status`.

## G13. A handle id cannot get a new computation

A derived handle is its computation: the id stands for one operation on one parent. No protocol
method replaces the computation behind an existing id, and no method gives a name to a handle of
the type `computation` (`register` names a resource).

Effect: the ultrastable design needs an interface that stays while the object behind it is
replaced (`docs/DECISIONS.md`, D34). A repair after a schema break gives a new handle id. Each
reader of the old id still reads the broken computation.

Workaround: the name of the object in the run is the stable interface. The run maps the name to
the newest handle, and the summary gives that handle to the main agent.

Proposal for DataHandles: an alias. A named handle that points at another handle, with one method
that changes the target, and `observe` that shows the target's version.

## G14. A describe of a derived handle is not a fresh read

After the source of a derived handle loses a column, `handle_describe` (brief form) of the derived
handle still shows the old schema, and `observe` gives a new version with no error. Only a read
fails: `ADAPTER_ERROR`, "Binder Error: Referenced column ... not found".

Effect: a regulator cannot detect a schema break from the description or the version of a handle.

Workaround: an object is a handle and one read operation (`docs/DECISIONS.md`, D30). The error of
the read is in the state, and the decision model judges it.

Proposal for DataHandles: `observe` of a derived handle states if its computation is still valid
against the newest schema of its source.

## G15. `observe` of an `api` handle gives a new version at each call

A live service has no version, so the `api` adapter gives a new one at each `observe`.

Effect: the loop runs the operation of an object on an `api` handle again at each tick. For a
slow or a costly service, that is one request for each tick, or one request after the other with
no pause.

Workaround: none in the protocol. The main agent can state the goal as a variable and end the run
when the first response is there.

Proposal for DataHandles: a resource states how it changes: a version, a poll interval, or "no
signal", so that a caller can choose when to read again.


## G16. A principal cannot read the descriptor of a capability that it does not hold

This is G4, seen from the main agent. When only the human's principal holds an action
(`docs/DECISIONS.md`, D44), `handle_describe` gives the main agent no name, no input schema and no
effects for it.

Effect: the main agent must learn the name and the input of the action from the user. `prepare`
cannot check the input against a schema, and it cannot check that the capability is an action. The
request for a confirmation has no description of the action.

Workaround: an object with `runners: ["human"]` on a capability that the main agent does not hold
is accepted with no schema check. The runtime checks the input when the human's principal runs the
graph, and a fault shows as the error of the object.

Proposal for DataHandles: `describe` lists each capability of a handle with a mark for the ones
that the caller does not hold. The descriptor is not a secret: it is what a caller needs to ask
for a grant.

## G17. A failed run of a graph uses up the approval, and an approval has no principal

Measured on DataHandles v0.4.0: an approval of a graph node holds for one `graph.run`. When that
run fails (the principal that runs it does not hold the capability), the approval is gone. An
approval does not record which principal gave it, and any holder of `graph.run` can use it.

Effect: when the human approves and the wrong principal runs the graph, the human must approve
again. The orchestrator must know which principal holds the action before it runs the graph.

Workaround: `Entry.holder` records the principal that holds the action, and that principal runs
the graph (D44). One request has one approval and one run.

Proposal for DataHandles: a failed run keeps the approval when the action did not start, and the
approval records its principal.
