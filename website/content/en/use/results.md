---
title: View results
group: Runs and results
order: 30
---
# View results

After a task finishes, inspect its output before running downstream steps. A completed task has finished executing; whether its result is useful still depends on the data and algorithm.

## Preview on the canvas

Expand a node's artifact preview to inspect supported data types. The available view depends on the output type and previewer. No previewer does not mean no file was produced.

- **Not run yet:** no corresponding execution result is available for this node.
- **Artifact cleaned up:** the record remains, but the file was deleted through artifact cleanup.
- **Result needs updating** or **Result based on old inputs:** the draft or upstream results have changed; check which steps need to run again.

Some placeholder messages in the editable draft offer a run button. Historical snapshots are read-only; return to the draft to execute a step.

## Logs and run history

Open logs using the task panel's log button. For failures, read the specific error before deciding whether the input, environment, or algorithm process needs attention.

Select a record in the run history panel to inspect its snapshot. Its completion state belongs to that record; it does not establish that your current draft has run with its new parameters.

## Locate the files

Artifacts live on the compute device that produced them. The default workspace is `~/.hololab/node/workspace/`, which the device configuration can override.

**Opening an artifact's location requires Flops Cobrowser integration.** The entry is absent in an ordinary browser. In Cobrowser, the device mapping must also be configured correctly. If the location cannot be opened, check the device configuration and artifact record before concluding that the file is missing.

See [Artifacts and workspaces](../../../../docs/artifacts.md) for directory layout, workspace migration, and cleanup rules. Its future-direction section describes plans, not available features.

## Check the impact before deleting

You can delete artifacts from the Artifacts page. The run history context menu also offers deletion of a run and its artifacts. The confirmation dialog shows how many items will be deleted or retained.

Artifacts still referenced by other runs are retained; removing the last reference may delete the actual files. Active tasks can prevent deletion and must be handled first. Deleting a run is not simply hiding it from history.

If you remove files manually, their records may remain and previews may report missing files. See [Troubleshooting](troubleshooting.md) for more cases.
