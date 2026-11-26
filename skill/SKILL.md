---
name: hololab
version: 0.0.1
description: >
  Start or stop HoloLab, run or inspect experiments, or turn an algorithm
  into a HoloLab node and publish a community version.
---

# HoloLab

HoloLab runs node-graph workflows on compute nodes. A Pack defines a versioned
algorithm interface. A Workflow is the editable graph. A Snapshot records the
graph for a run. A Job is an execution, and a Handle refers to an artifact.

## Choose the guide for your task

If the user activates this skill without a task, ask once: “Do you want to
operate HoloLab, run or inspect an experiment, or turn an external algorithm
into a Pack?” Offer these three choices through the harness when available,
or ask in one line. If the intent is already clear, go straight to the
matching guide below. Read it before acting and open another only when needed.

1. [Start or stop HoloLab](./references/start-stop.md) for service lifecycle
   and readiness.
2. [Run or inspect an experiment](./references/experiments.md) for workflows,
   jobs, artifacts, and pasted `hololab://` references.
3. [Turn an algorithm into a Pack](./references/algorithm-to-pack.md) for
   design, adaptation, and a canvas prototype. If the user only wants to
   prepare or publish an existing Pack, go directly to the
   [release guide](./references/pack-to-release.md).

Resolve each relative link against the file that contains it. If reading via
GitHub Raw, use that file's Raw URL as the base so all linked files stay on
the same Git ref.

## Improve this guide as you use it

This skill evolves with HoloLab. While carrying out any of the three tasks,
watch for missing steps, misleading advice, and claims that conflict with
what you observe. Treat the guide as guidance. When in doubt, check the
target version's source and the behavior you can actually observe.

Keep working through minor friction. If you find a material gap or error,
briefly tell the user what happened, what evidence you found, and what
should change. When useful, draft a short note the user can forward to
HoloLab developers. Leave the decision to send it with the user.
