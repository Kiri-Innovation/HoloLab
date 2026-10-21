"""Migrate only MiddleCutPro's draft after gateway/node upgrade; never rewrite history."""

import argparse
import json

import requests

WORKFLOW = "0eb1b412-e095-4f9d-b55a-5862cf9fc705"


def migrate(graph):
    from copy import deepcopy

    graph = deepcopy(graph)
    for node in graph["nodes"]:
        name = node.get("algorithm_name")
        if name not in ("vec3-array-source", "float-array-source"):
            continue
        vector = name == "vec3-array-source"
        params = node["params"]
        if isinstance(params.get("items"), str):
            values = json.loads(params["items"])
            params = (
                {
                    key: [v["transform"][i] for v in values]
                    for i, key in enumerate(("x", "y", "scale"))
                }
                if vector
                else {"items": [v["offset"] for v in values]}
            )
        params = {k: v for k, v in params.items() if k != "ids"}
        op = "constant-vec3-array" if vector else "constant-float-array"
        node.update(
            kind="intrinsic",
            operation=op,
            assembly=None,
            algorithm_name="@builtin/" + op,
            algorithm_version="1",
            assigned_node_id=None,
            params=params,
            arrayed_toggle=False,
        )
        pins = graph.setdefault("pinned_params", {})
        old_key = name + "@0.1.0"
        if old_key in pins:
            pins[node["algorithm_name"] + "@1"] = pins.pop(old_key)
        for key in ("port_types", "resolved_outputs", "latest_run", "constant"):
            node.pop(key, None)
    return graph


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8828/api")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--run", action="store_true", help="Run the migrated workflow after saving")
    args = parser.parse_args()
    session = requests.Session()
    session.trust_env = False
    response = session.get(args.api + "/workflows/" + WORKFLOW)
    response.raise_for_status()
    draft = response.json()
    payload = {
        "workflow_id": WORKFLOW,
        "name": draft["name"],
        "graph": migrate(draft["graph"]),
        "base_updated_ts": draft["updated_ts"],
    }
    if not args.apply:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    # Old gateways reject the new graph model; save is atomic and optimistic.
    result = session.post(args.api + "/workflows", json=payload)
    result.raise_for_status()
    print(json.dumps(result.json(), ensure_ascii=False))
    if args.run:
        result = session.post(args.api + "/workflows/" + WORKFLOW + "/run", json={})
        result.raise_for_status()
        print(json.dumps(result.json(), ensure_ascii=False))


if __name__ == "__main__":
    main()
