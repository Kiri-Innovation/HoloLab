---
title: Your first workflow
group: Quick start
order: 20
---
# Your first workflow

Start with a Pack whose environment and data are already available on a compute device. Run a node that needs no upstream input first, check that it works, then connect the next step.

## Create a workflow and add nodes

1. In the workflow gallery, click **+ New workflow**. An empty gallery also offers **+ Create your first workflow**.
2. Enter a workflow name at the top of the canvas.
3. Find an algorithm using **Search packs…** in the **Packs** panel and add its node to the canvas.

If the list is empty, check that the device is online and that **Pack sources** in its settings includes the manifest location. Search only filters Packs that have been discovered; it does not download or install algorithms.

## Set parameters and choose a device

Select an algorithm node. Edit its **Parameters** in the inspector and choose a device that provides the Pack under **Compute node**. Execution checks device assignments, so replace `unassigned` before running.

Each Pack exposes its own parameters. File paths must refer to the device that will execute the algorithm; paths on your browser's computer may not be accessible there.

## Connect the steps

Drag from an upstream output port to a downstream input port. While dragging, the interface indicates whether the connection is valid. A rejected connection reports the expected type, actual type, or reason for the mismatch.

Checks cover more than type names: array depth and applicable axis-name rules also matter. A single image and an image collection are different inputs. Similar port names do not guarantee compatibility.

Connect all required inputs and keep the graph free of cycles. Consult the algorithm for input semantics and parameter constraints.

## Wait for the save, then run

Canvas edits save automatically; the top bar shows the save state. Wait for **Saved**, then click **Run** at the upper right. If saving fails, click **Save failed · Retry**. Resolve any warning about another page modifying the workflow before running.

HoloLab validates Packs, devices, required inputs, and links before execution. If **Cannot run** appears, address the errors shown for the affected nodes or links.

Later draft edits do not change tasks that have already been submitted. If a task is running with old parameters, check its state before deciding whether to stop it and run with the new configuration.

## Follow or stop a task

Canvas nodes and the task panel show execution state. Progress depends on the Pack reporting progress in a format HoloLab can parse. Each task row provides access to its logs.

Use the stop action in the task row when needed, checking the scope in any confirmation dialog. Stopping a task does not complete it or generate missing outputs.

Next, [view your results](results.md). For failed tasks or disconnected devices, see [Troubleshooting](troubleshooting.md).
