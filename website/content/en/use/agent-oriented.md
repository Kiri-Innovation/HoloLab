---
title: Everything Agent-Oriented
group: Quick start
order: 25
---
# Everything Agent-Oriented

You can work on the canvas and hand a specific question to an agent without reconstructing the scene in a chat. HoloLab gives people a visual workspace and agents structured access to the same workflows and execution records.

## Everything referrable

Instead of describing “the node that failed,” use its reference action to hand the agent a `hololab://` reference. It identifies an object within an instance, so give the agent the instance address too.

<div class="no-flops-only">

<figure class="doc-screenshot">
<div class="annotated-canvas"><picture><source srcset="/images/docs/reference-entry-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/reference-entry-light.webp" alt="The highlighted clipboard icon marks “Copy reference info” in the STG node header, to the left of Run." width="744" height="280" loading="lazy" decoding="async" /></picture><div class="canvas-annotations" aria-hidden="true"><span class="canvas-region region-reference"><span class="reference-label">Copy reference info</span></span></div></div>
<figcaption>The highlighted clipboard icon marks “Copy reference info” in the STG node header, to the left of Run.</figcaption>
</figure>

Click the highlighted action to copy reference text for your agent conversation. It contains a `hololab://` reference and an optional comment, not a Markdown summary. This is the node’s copied text, with the workflow ID redacted:

```text
hololab://graph-node/<workflow-id>/fx  # frame-extraction
```

</div>

<div class="flops-only">

<figure class="doc-screenshot">
<div class="annotated-canvas"><picture><source srcset="/images/docs/reference-flops-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/reference-flops-light.webp" alt="Illustration: “Reference to Flops” in a simulated Flops environment. The paired quotation marks match the product source; ordinary browsers use a clipboard icon. This is not a capture from Flops itself." width="744" height="280" loading="lazy" decoding="async" /></picture><div class="canvas-annotations" aria-hidden="true"><span class="canvas-region region-reference"><span class="reference-label">Reference to Flops</span></span></div></div>
<figcaption>Illustration: “Reference to Flops” in a simulated Flops environment. The paired quotation marks match the product source; ordinary browsers use a clipboard icon. This is not a capture from Flops itself.</figcaption>
</figure>

In the Flops embedded browser, the action inserts a reference into the conversation input. The inserted reference includes a Markdown summary labeled `hololab.reference-snapshot/v1`, with identity, state, context and navigation sections. It is a summary captured at that moment; the agent can resolve the reference again for current data.

</div>

For the complete list of supported objects, see the [supported references table](#support-quick-reference) at the end of this page.

## Everything agent-readable

An agent can call `GET /api/resolve?ref=hololab://…` to inspect the object and follow its related links. For a canvas node, the response includes saved parameters, upstream and downstream connections, and the latest execution attribution and output Handles when available.

This excerpt comes from a completed Job in the STG workflow. Only selected fields are shown, with record IDs replaced by placeholders. `ref` identifies the object, `resource` carries its state and algorithm context, and `related` provides links to further reads.

```json
{
  "kind": "job",
  "ref": "hololab://job/<job-id>",
  "resource": {
    "job_id": "<job-id>",
    "graph_node_id": "und",
    "algorithm_name": "image-undistort",
    "algorithm_version": "0.5.0",
    "state": "done",
    "progress": null
  },
  "related": {
    "log": "/api/jobs/<job-id>/log",
    "workflow": "/api/workflows/<workflow-id>",
    "snapshot": "/api/snapshots/<snapshot-id>"
  }
}
```

The resolver returns JSON; the four-section Markdown reference summary is built from it. Job logs are available at `/api/jobs/<id>/log`, and Run snapshots at `/api/snapshots/<id>`. That lets an agent investigate a failed step without reading status from pixels. Judging whether a render looks right still requires looking at the output.

For reading endpoints and when to use them, see [Readable interfaces](#readable-interfaces) at the end of this page.

## Everything included in a skill

The repository's [skill/SKILL.md](https://github.com/Kiri-Innovation/HoloLab/blob/main/skill/SKILL.md) is HoloLab's operating guide. It covers resolving references, editing and running workflows, waiting for results, inspecting outputs and logs, and cancelling tasks.

Give an agent that can reach your instance the message below, replacing `<URL>` with your instance address first. The link points to the plain-text skill file so the agent can read it directly.

<div class="copyable-code">

```text
Read https://raw.githubusercontent.com/Kiri-Innovation/HoloLab/main/skill/SKILL.md
and use it as your operating skill for HoloLab. My instance is at <URL>.
Resolve any hololab:// references I paste before acting. Use long-polling
for run results, and read relevant logs before suggesting changes.
Keep edits and runs within the scope of the task I give you.
```

</div>

The skill already includes steps for waiting for a run to finish and collecting task states, so you do not need to write your own polling loop.

This asks the agent to read and use the guide for the current task; it does not install it into a persistent skill library. The instance currently has no automatic skill-download or installation endpoint. The skill is an operating guide with links to further documentation, not a bundle containing every manual.

## Try a read-only question first

Give an agent with HTTP access your instance address, the skill file and a Job reference. Ask: “Why did this task fail? Read its parameters and logs before suggesting a change.” This makes the first handoff concrete without starting another run.

For the entities themselves, see [Introducing HoloLab](/docs/introduction) and [Artifacts and run records](/docs/use/run-records). Next: [Your first workflow →](/docs/use/workflows).

## Support quick reference

### Reference objects

These objects are currently supported. The last column describes what resolving each reference provides.

| Object | Reference format | What you can read |
|---|---|---|
| Workflow | `hololab://workflow/<id>` | Draft graph, parameters and latest run summary. |
| Workflow node | `hololab://graph-node/<workflow-id>/<node-id>` | Parameters, port types when available, connections and latest task outputs. |
| Run (snapshot) | `hololab://run/<id>` | Graph snapshot, associated tasks and a link for waiting on results. |
| Job | `hololab://job/<id>` | State, progress, failure details, parameters, inputs, outputs and log link. |
| Output Handle | `hololab://handle/<id>` | Type tags, size, output port, file access and summary links. |
| Pack | `hololab://pack/<name>@<version>` | Inputs, outputs, parameter declarations and devices providing the Pack. |
| Compute node | `hololab://node/<id>` | Device information, GPU and available Packs. |
| Workflow index | `hololab://workflows` | Workflow count, names, node counts and latest run summaries. |
| Artifact index | `hololab://artifacts` | Artifact count, total size, per-workflow summaries and inventory link. |
| Library resource | `hololab://library-resource/<id>` | Current version, type, size, placements and transfer status. |
| Library folder | `hololab://library-folder/<id>` | Name, parent folder and resource-list link. |

`<id>` is the object ID; the library root is `hololab://library-folder/root`. Use `run` for a snapshot and `handle` for a single output; `snapshot` and `artifact` are not supported kinds.

### Readable interfaces

These read-only interfaces cover the skill’s inspection workflow; dispatching and other write operations are not included. Replace `<id>` with the corresponding object ID.

| Method + path | Returns | When to use it |
|---|---|---|
| `GET /api/health` | Service status and protocol version | Check that the instance is reachable. |
| `GET /api/overview` | Devices, workflows and recent task overview | Get an initial view of the instance. |
| `GET /api/resolve?ref=hololab://…` | Object data and related links | Follow a reference into its context. |
| `GET /api/workflows` | Workflow list and latest run summaries | Find a workflow to work with. |
| `GET /api/workflows/<id>` | Draft graph and parameters | Inspect the current draft. |
| `GET /api/workflows/<id>/runs` | Past runs and state rollups | Find a run to investigate. |
| `GET /api/snapshots/<id>` | Frozen graph and associated tasks | Inspect what ran. |
| `GET /api/snapshots/<id>?wait_for_state=done&timeout=1800` | Run details and timeout indicator | Wait for all tasks to succeed; check the returned state on timeout. |
| `GET /api/jobs?workflow_id=<id>&state=failed&limit=5` | Filtered task list | Narrow down failed tasks. |
| `GET /api/jobs/<id>` | State, progress, parameters, inputs and outputs | Inspect an individual task. |
| `GET /api/jobs/<id>/log?tail=200&stream=both` | Tail of stdout / stderr logs | Read errors before suggesting changes. |
| `GET /api/handles/<id>` | Output metadata and proxy URL | Inspect an output and find its download URL. |
| `GET /api/handles/<id>/summary` | Server-parsed output summary | Inspect metadata before downloading large files. |
| `GET /api/artifacts?workflow_id=<id>` | Artifact inventory, sizes and attribution | Find a workflow’s output files. |
| `GET /api/artifacts/summary` | Artifact counts, sizes and per-workflow totals | Review storage use. |
| `GET /api/artifacts/<handle-id>/lineage` | Artifact ancestors and descendants | Trace an output’s provenance. |
| `GET /api/nodes` | Connected devices, GPUs and Packs | Check available compute nodes. |
| `GET /api/nodes/metrics/history?since=<timestamp>` | CPU, memory and GPU history | Inspect resource-use trends. |
| `GET /api/pack-catalog` | Port and parameter declarations from connected nodes | Check a Pack’s interface before editing. |
| `GET /api/packs` | Persistent Pack inventory | Find previously registered Packs, including offline ones. |
| `GET /api/snapshots/<id>/deletion-preview` | Expected impact of deleting a run | Review cleanup impact without deleting anything. |
