"""The action gate. It acts as the main agent's principal, not as the orchestrator's principal.

An action is what DataHandles defines: a capability that writes, reaches outside, and is not
idempotent. The orchestrator's principal never holds an action capability, so the runtime refuses
an action from the orchestrator. That holds when the orchestrator code is wrong.

`delegate` gives the orchestrator each capability of a handle that is not an action. An approved
action runs as a one-node graph: `graph.approve`, then `graph.run`. The principal that holds the
action runs the graph: the main agent's, or the human's when only the human holds the action.
docs/GAPS.md (G1 to G4) says why the gate has this shape.
"""
from datahandles import HandleError
from datahandles.sdk import Client


def is_action(capability: dict) -> bool:
    """The definition of an action in the DataHandles specification, read from a capability descriptor."""
    return capability["effects"]["write"] and capability["effects"]["external"] and not capability["execution"]["idempotent"]


def action_graph(target: str, capability: str, input: dict) -> dict:
    return {"id": "gr_action", "nodes": [{"id": "target", "kind": "resource", "reference": target},
                                         {"id": "action", "kind": "operation", "operation": {"capability": capability, "input": input}}],
            "edges": [{"from": "target", "to": "action"}], "execution": {"strategy": "lazy", "consistency": "latest"}}


class Gate:
    def __init__(self, planner: Client, orchestrator: str, human: str, human_client: Client | None = None):
        self.planner, self.human_client = planner, human_client
        self.orchestrator, self.human = orchestrator, human

    def delegate(self, handle_id: str, sources: bool = False) -> list[dict]:
        """Give the orchestrator the capabilities of a handle that are not actions. Return the
        descriptors of the actions: the orchestrator cannot see them and cannot invoke them. It
        can only request them. With `sources`, do the same for each handle that this handle was
        made from: a model runner reads the source of its object."""
        try:
            handle = self.planner.resolve(handle_id)
            capabilities = handle.describe()["capabilities"]
        except HandleError:   # the main agent holds nothing on this handle, so it has nothing to give
            return []
        free = [c["name"] for c in capabilities if not is_action(c)]
        handle.grant(self.orchestrator, free)
        for parent in handle.info.get("parents", []) if sources else []:
            self.delegate(parent, sources=True)
        return [c for c in capabilities if is_action(c)]

    def open(self, name: str, target: str, capability: str, input: dict) -> str:
        """Make the one-node graph of an action. The human's principal gets `graph.approve` and `graph.run` on it."""
        graph = self.planner.register(name, adapter="graph", source={"graph": action_graph(target, capability, input)})
        graph.grant(self.human, ["graph.approve", "graph.run"])
        return graph.id

    def approve(self, graph_id: str, by_human: bool = False) -> None:
        client = self.human_client if by_human else self.planner
        client.resolve(graph_id).invoke("graph.approve", {"node": "action"})

    def run(self, graph_id: str, by_human: bool = False) -> dict:
        """Run the graph as the principal that holds the action. Return the operation result of the action node. With no
        approval, its status is `waiting`."""
        client = self.human_client if by_human else self.planner
        return client.resolve(graph_id).invoke("graph.run")["results"]["action"]
