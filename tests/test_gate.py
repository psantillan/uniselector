"""The action gate, tested against the DataHandles runtime.

The brief gives this shape: the orchestrator holds `graph.run` and not `graph.approve`. The first
test shows that this shape does not gate an action (docs/GAPS.md, G1 and G2). The second test shows
the shape that Uniselector uses: the orchestrator's principal never holds an action capability.
"""
import pytest
from datahandles import HandleError
from datahandles.sdk import Client, connect

from uniselector.gate import Gate, action_graph

ON = {"on": True}


@pytest.fixture
def world(tmp_path):
    planner = connect(str(tmp_path / "state"), "planner")
    others = {name: Client(planner.runtime, name) for name in ("uniselector", "human")}
    room = planner.register("room", adapter="room", source={"interval_ms": 50})
    return planner, others, room


def test_graph_approval_alone_does_not_gate_a_principal_that_holds_the_action(world):
    planner, others, room = world
    orchestrator = others["uniselector"]
    room.grant("uniselector", ["*"])
    graph = planner.register("g", adapter="graph", source={"graph": action_graph(room.id, "room.heater", ON)})
    graph.grant("uniselector", ["graph.run"])
    # The part of the brief's shape that holds: no approval, no run; and the orchestrator cannot approve the planner's graph.
    assert orchestrator.resolve(graph.id).invoke("graph.run")["results"]["action"]["status"] == "waiting"
    with pytest.raises(HandleError) as denied:
        orchestrator.resolve(graph.id).invoke("graph.approve", {"node": "action"})
    assert denied.value.code == "CAPABILITY_DENIED"
    # G2: the orchestrator approves an equal graph of its own. The approval then counts for the planner's graph.
    own = orchestrator.register("own", adapter="graph", source={"graph": action_graph(room.id, "room.heater", ON)})
    own.invoke("graph.approve", {"node": "action"})
    assert orchestrator.resolve(graph.id).invoke("graph.run")["results"]["action"]["status"] == "completed"
    # G1: a direct invoke of the action needs no approval.
    assert orchestrator.resolve(room.id).invoke("room.heater", {"on": False})["heater"] is False


def test_the_orchestrator_cannot_run_an_action_that_it_does_not_hold(world):
    planner, others, room = world
    orchestrator = others["uniselector"]
    gate = Gate(planner, "uniselector", "human", others["human"])
    actions = gate.delegate(room.id)
    assert [a["name"] for a in actions] == ["room.heater"]
    held = [c["name"] for c in orchestrator.resolve(room.id).describe()["capabilities"]]
    assert "room.latest" in held and "room.heater" not in held
    # Each way that the orchestrator has to an action fails in the runtime.
    with pytest.raises(HandleError) as denied:
        orchestrator.resolve(room.id).invoke("room.heater", ON)
    assert denied.value.code == "CAPABILITY_DENIED"
    own = orchestrator.register("own", adapter="graph", source={"graph": action_graph(room.id, "room.heater", ON)})
    own.invoke("graph.approve", {"node": "action"})
    node = own.invoke("graph.run")["results"]["action"]
    assert (node["status"], node["error"]["code"]) == ("failed", "CAPABILITY_DENIED")
    graph_id = gate.open("g", room.id, "room.heater", ON)
    with pytest.raises(HandleError):
        orchestrator.resolve(graph_id)   # the orchestrator holds nothing on the graph of the action
    # The gate: no approval, no run. One approval, one run.
    assert gate.run(graph_id)["status"] == "waiting"
    assert room.observe()["heater"] is False   # `room.latest` has no reading in the first interval
    gate.approve(graph_id, by_human=True)
    done = gate.run(graph_id)
    assert (done["status"], done["output"]["heater"]) == ("completed", True)
    assert gate.run(graph_id)["metadata"].get("reused") is True
