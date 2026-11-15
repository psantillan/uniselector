"""Lineage export: rebuild the graph of what a run did, from the handles that it made.

DataHandles keeps the operation that made each derived handle. `graph.export` gives that history
for one handle. The lineage of a run is the union of those graphs. It is a DataHandles graph, so a
caller can read it, or submit it to run the same operations again.
"""
from datahandles import HandleError


def export(run_id: str, handles: list) -> dict:
    nodes, edges = {}, {}
    for handle in handles:
        if handle.info["kind"] == "job":   # a job is how an operation ran, not a result of it
            continue
        try:
            graph = handle.graph()
        except HandleError:
            continue
        nodes.update({n["id"]: n for n in graph["nodes"]})
        edges.update({(e["from"], e["to"], e.get("input_name")): e for e in graph["edges"]})
    return {"id": f"gr_{run_id}", "nodes": list(nodes.values()), "edges": list(edges.values()),
            "execution": {"strategy": "lazy", "consistency": "latest"}}
