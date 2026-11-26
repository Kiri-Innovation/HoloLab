# Start and stop HoloLab

## Identify the instance and how it is managed

Use the instance address supplied by the operator. For a local default instance,
use `HOLO=http://127.0.0.1:8828`. For a remote or managed instance, identify
the owning host and launch method before taking service actions. A connection
failure from the agent can also mean the address is wrong or unreachable.

For installation, frontend setup, and available launch modes, see the
[Quick Start](../../website/content/en/use/quick-start.md) and the
[README](../../README.md#quick-start-one-command).

## Start and check readiness

For local foreground use, follow the Quick Start launch command. `hololab start`
runs the gateway and a local compute node. Use the documented host binding for
the intended access. Distributed setups start the gateway and compute nodes
separately. See [distributed mode](../../README.md#distributed--manual-mode)
and the [connection topology](../../docs/architecture.md#connection-topology).

Check both the gateway and the intended compute node:

```bash
curl -fsS "$HOLO/api/health" | jq .
curl -fsS "$HOLO/api/nodes" | jq .
```

Health confirms the gateway responds. Check that the intended compute node is
connected and offers the needed Pack version before dispatching work.

## Stop, restart, and recheck

Use Ctrl+C in the foreground terminal. The CLI also provides `hololab stop`
for the recorded service belonging to the current HoloLab home. For a managed
deployment, use its established service controls. Before stopping or restarting,
check for active jobs on the affected instance and confirm the intended scope.
Restart with the original launch mode and repeat the readiness checks.

To cancel experiment jobs while keeping the service available, use
[experiment cancellation](experiments.md#cancel-a-run-single-job-whole-snapshot-whole-node-everything).

## Diagnose an unreachable gateway or offline node

Check the host, port, binding, and network path first. If the gateway is healthy
but a node is offline, check the node process, logs, and configured gateway URL.
If the node is online but the Pack is missing, check its inventory and
[Pack source discovery](../../docs/pack-spec.md#multi-pack-source-layout).
