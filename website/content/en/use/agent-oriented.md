---
title: Everything Agent-Oriented
group: Quick start
order: 25
---
# Everything Agent-Oriented

You can work on the canvas and hand a specific question to an agent without reconstructing the scene in a chat. HoloLab gives people a visual workspace and agents structured access to the same workflows and execution records.

## Everything referrable

Instead of describing “the node that failed,” use its reference action. Workflows, canvas nodes, Runs, Jobs, output Handles, Packs and compute nodes have `hololab://` references; `hololab://workflows` and `hololab://artifacts` refer to whole lists. A reference identifies an object within an instance, so give the agent the instance address too.

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

This asks the agent to read and use the guide for the current task; it does not install it into a persistent skill library. The instance currently has no automatic skill-download or installation endpoint. The skill is an operating guide with links to further documentation, not a bundle containing every manual.

For example, the skill uses this request to wait for completion and read task states instead of writing a polling loop. `$HOLO` is the instance address and `$SNAP` is the snapshot ID returned by a run. This excerpt is verbatim:

<div class="copyable-code">

```bash
# c) LONG-POLL until done — one call, up to timeout seconds
curl -s "$HOLO/api/snapshots/$SNAP?wait_for_state=done&timeout=1800" \
  | jq '{waited, wait_timed_out, jobs: [.jobs[] | {algorithm_name, state}]}'
```

</div>

## Try a read-only question first

Give an agent with HTTP access your instance address, the skill file and a Job reference. Ask: “Why did this task fail? Read its parameters and logs before suggesting a change.” This makes the first handoff concrete without starting another run.

For the entities themselves, see [Introducing HoloLab](/docs/introduction) and [Artifacts and run records](/docs/use/run-records). Next: [Your first workflow →](/docs/use/workflows).
