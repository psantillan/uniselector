"""Lineage export: rebuild the graph of what a run did, from the handles that it made.

DataHandles keeps the operation that made each derived handle. `graph.export` gives that history
for one handle. The lineage of a run is the union of those graphs. It is a DataHandles graph, so a
caller can read it, or submit it to run the same operations again.
"""
import json

from datahandles import HandleError


def graph_of(handle) -> dict:
    """The graph that made one handle. A job has none: it is how an operation ran, not a result of it."""
    if handle.info["kind"] != "job":
        try:
            return handle.graph()
        except HandleError:
            pass
    return {"nodes": [], "edges": []}


def made(graph: dict) -> str:
    """The operations of a graph in its order, as one line of text. The graph of a resource has none."""
    return " → ".join(f"{n['operation']['capability']} {json.dumps(n['operation'].get('input', {}))}" for n in graph["nodes"] if n["kind"] == "operation")


def export(run_id: str, handles: list) -> dict:
    nodes, edges = {}, {}
    for graph in map(graph_of, handles):
        nodes.update({n["id"]: n for n in graph["nodes"]})
        edges.update({(e["from"], e["to"], e.get("input_name")): e for e in graph["edges"]})
    return {"id": f"gr_{run_id}", "nodes": list(nodes.values()), "edges": list(edges.values()),
            "execution": {"strategy": "lazy", "consistency": "latest"}}
