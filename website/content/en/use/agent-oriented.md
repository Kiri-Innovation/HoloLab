---
title: Everything Agent-Oriented
group: Quick start
order: 25
---
# Everything Agent-Oriented

You can work on the canvas and hand a specific question to an agent without reconstructing the scene in a chat. HoloLab gives people a visual workspace and agents structured access to the same workflows and execution records.

## Everything referrable

Instead of describing “the node that failed,” use its reference action. Workflows, canvas nodes, Runs, Jobs, output Handles, Packs and compute nodes have `hololab://` references; `hololab://workflows` and `hololab://artifacts` refer to whole lists. A reference identifies an object within an instance, so give the agent the instance address too.

<figure class="doc-screenshot">
<picture><source srcset="/images/docs/reference-entry-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/reference-entry-light.webp" alt="The frame-extraction node in STG. The clipboard icon to the left of the Run triangle is the reference action. Source imagery and device details are excluded." width="744" height="280" loading="lazy" decoding="async" /></picture>
<figcaption>The frame-extraction node in STG. The clipboard icon to the left of the Run triangle is the reference action. Source imagery and device details are excluded.</figcaption>
</figure>

<div class="flops-only">

In the Flops embedded browser, the action inserts a reference into the conversation input. The inserted reference includes a Markdown summary labeled `hololab.reference-snapshot/v1`, with identity, state, context and navigation sections. It is a summary captured at that moment; the agent can resolve the reference again for current data.

</div>

<!-- Flops input-pill screenshot: add here when a reviewed real-device capture is available. -->

<div class="no-flops-only">

In a regular browser, the reference action copies text that you can paste into your conversation with an agent.

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

The repository's `skill/SKILL.md` explains how to check an instance, resolve references, edit and run workflows, wait for results, inspect outputs and logs, and cancel work. It also points to the Pack authoring and specification documents in `docs/`.

For example, the skill uses one request to wait for completion and read task states instead of writing a polling loop. `$HOLO` is the instance address and `$SNAP` is the snapshot ID returned by a run. This is a verbatim excerpt; it was not executed during capture:

```bash
# c) LONG-POLL until done — one call, up to timeout seconds
curl -s "$HOLO/api/snapshots/$SNAP?wait_for_state=done&timeout=1800" \
  | jq '{waited, wait_timed_out, jobs: [.jobs[] | {algorithm_name, state}]}'
```

Ask your agent to read that file from a checkout, or load it through the agent's supported skill mechanism. Providing an instance URL alone does not install the skill: there is currently no automatic skill-download or installation endpoint on the instance. The skill is an operating guide with links to further documentation, not a bundle containing every manual.

## Try a read-only question first

Give an agent with HTTP access your instance address, the skill file and a Job reference. Ask: “Why did this task fail? Read its parameters and logs before suggesting a change.” This makes the first handoff concrete without starting another run.

For the entities themselves, see [Introducing HoloLab](/docs/introduction) and [Artifacts and run records](/docs/use/run-records). Next: [Your first workflow →](/docs/use/workflows).
