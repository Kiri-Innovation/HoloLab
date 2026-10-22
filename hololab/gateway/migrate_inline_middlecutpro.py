"""Opt-in draft migration after gateway/node upgrade. Never changes history."""

import argparse
import json
import urllib.request

WORKFLOW = "0eb1b412-e095-4f9d-b55a-5862cf9fc705"
TRANSFORMS = [[0, 0, 1], [0.727, 0.0198, 0.25]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8828/api")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            args.api.rstrip("/") + path, data=data, headers={"Content-Type": "application/json"}
        )
        with opener.open(request, timeout=30) as response:
            return json.load(response)

    draft = call("/workflows/" + WORKFLOW)
    graph = draft["graph"]
    node = next(n for n in graph["nodes"] if n["id"] == "compose-final")
    if node.get("algorithm_name") != "compose-video":
        raise SystemExit("compose-final is not compose-video; migration stopped")
    if any(e["target"] == node["id"] and e["targetHandle"] == "transforms" for e in graph["edges"]):
        raise SystemExit(
            "transforms is wired; migration stopped rather than silently overriding its source"
        )
    node.setdefault("port_values", {})["transforms"] = TRANSFORMS
    payload = {
        "workflow_id": WORKFLOW,
        "name": draft["name"],
        "graph": graph,
        "base_updated_ts": draft["updated_ts"],
    }
    if not args.apply:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    device = next((n for n in call("/nodes") if n["node_id"] == node["assigned_node_id"]), None)
    if not device or "literal-v3" not in device.get("intrinsics", []):
        raise SystemExit(
            "Upgrade gateway and consumer node first (literal-v3 capability required); no workflow was changed"
        )
    print(json.dumps(call("/workflows", payload), ensure_ascii=False))
    if args.run:
        print(json.dumps(call("/workflows/" + WORKFLOW + "/run", {}), ensure_ascii=False))


if __name__ == "__main__":
    main()
