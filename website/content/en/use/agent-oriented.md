---
title: Everything Agent-Oriented
group: Quick start
order: 25
---
# Everything Agent-Oriented

You can work on the canvas and hand a specific question to an agent without reconstructing the scene in a chat. HoloLab gives people a visual workspace and agents structured access to the same workflows and execution records.

## Everything referrable

Instead of describing “the node that failed,” use its reference action. Workflows, canvas nodes, Runs, Jobs, output Handles, Packs and compute nodes have `hololab://` references; `hololab://workflows` and `hololab://artifacts` refer to whole lists. A reference identifies an object within an instance, so give the agent the instance address too.

In the Flops embedded browser, the action inserts a reference into the conversation input. In a regular browser, it copies reference text for you to paste. The inserted reference includes a Markdown summary labeled `hololab.reference-snapshot/v1`, with identity, state, context and navigation sections. It is a summary captured at that moment; the agent can resolve the reference again for current data.

## Everything agent-readable

An agent can call `GET /api/resolve?ref=hololab://…` to inspect the object and follow its related links. For a canvas node, the response includes saved parameters, upstream and downstream connections, and the latest execution attribution and output Handles when available.

This is the resolver's response structure, abbreviated:

```text
kind + ref   → which object?
resource     → its data and context
related      → links to further API reads
```

The resolver returns JSON; the four-section Markdown summary above is built from it. Job logs are available at `/api/jobs/<id>/log`, and Run snapshots at `/api/snapshots/<id>`. That lets an agent investigate a failed step without reading status from pixels. Judging whether a render looks right still requires looking at the output.

## Everything included in a skill

The repository's `skill/SKILL.md` explains how to check an instance, resolve references, edit and run workflows, wait for results, inspect outputs and logs, and cancel work. It also points to the Pack authoring and specification documents in `docs/`.

Ask your agent to read that file from a checkout, or load it through the agent's supported skill mechanism. Providing an instance URL alone does not install the skill: there is currently no automatic skill-download or installation endpoint on the instance. The skill is an operating guide with links to further documentation, not a bundle containing every manual.

## Try a read-only question first

Give an agent with HTTP access your instance address, the skill file and a Job reference. Ask: “Why did this task fail? Read its parameters and logs before suggesting a change.” This makes the first handoff concrete without starting another run.

For the entities themselves, see [Introducing HoloLab](/docs/introduction) and [Artifacts and run records](/docs/use/run-records). Next: [Your first workflow →](/docs/use/workflows).
