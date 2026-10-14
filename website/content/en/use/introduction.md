---
title: Introducing HoloLab
group: Quick start
order: 0
---
# Introducing HoloLab

A vision experiment often means chaining frame extraction, reconstruction, and training, then changing parameters and checking the results. Think of HoloLab as an experiment flowchart that can dispatch the work: you arrange the steps on a canvas.

## Workflows: turn a pipeline into a node graph

You might use a Python or Shell script to run several algorithms in sequence. Changing the pipeline means editing that script.

In HoloLab, you arrange those steps with nodes and links on a canvas. Editing this **node graph** edits the **Workflow**. Your existing programs still execute the algorithms; the graph controls how they connect.

## Nodes contain individual algorithm steps

Given a video, you might run a frame-extraction script and pass its output to another program. That algorithm could be a Python program or something launched with a Shell command.

HoloLab wraps an independent step like frame extraction as a **Node**. Its **ports** receive and return data through a common, machine-readable interface declaration, so the next step knows what to expect.

Add a manifest describing your program’s input and output ports, parameters, environment, and execution command to make a **Pack**. Once a device scans the Pack, its node type becomes available on the canvas. You still need to prepare the environment and data.

Select a node to edit its parameters or check its execution status.

## Links tell the system which data to pass

In a script, you often copy an output path into the next command or configuration file. A wrong path or incompatible input may go unnoticed until execution.

On the canvas, draw a **Link** from an output port to an input port to record “use this output as that input.” It declares a data dependency between steps.

At runtime, the system passes the upstream artifact reference to the downstream task. The execution device resolves it to a local path, fetching files first if they live on another device. Files do not travel along the line drawn on the canvas.

Only compatible port types can connect; incompatible links are rejected when you draw them.

<figure class="concept-diagram" aria-label="A video feeds a frame-extraction step, producing images. Links connect upstream output ports to downstream input ports.">
<div class="concept-flow">
<div class="concept-card"><strong>Video source</strong><span>Output: video</span></div>
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h17m-6-6 6 6-6 6" /></svg>
<div class="concept-card"><strong>Extract frames</strong><span>Input: video</span><span>Output: images</span></div>
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h17m-6-6 6 6-6 6" /></svg>
<div class="concept-card"><strong>Image collection</strong><span>One image per element</span></div>
</div>
<figcaption>The arrows map outputs to inputs. The execution device reads or fetches the files as needed.</figcaption>
</figure>

## How HoloLab runs

You submit a workflow through the browser canvas. The service that validates the graph, resolves dependencies, and dispatches tasks in dependency order is **Gateway**.

Once a device has scanned a Pack and connected, you can select that **compute node** for a step. Its **Executor** launches the algorithm as a separate process in the environment declared by the Pack and configured on the device.

Gateway receives task status and registers artifact locations. The files stay on the execution device for later steps or inspection.

<figure class="concept-diagram" aria-label="Separate machines: Gateway and Executors A and B each have a dashed machine boundary. Arrows point from the user to Gateway through the browser canvas, and from each Executor to Gateway over WebSocket.">
<div class="concept-flow concept-access">
<div class="concept-person"><strong><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM2 18v-3a4 4 0 0 1 4-4h2M10 11h12v9H10zM8 22h15M15 14l-2 2 2 2m3-4 2 2-2 2" /></svg></strong></div>
<div class="concept-connection"><span>Browser canvas</span><svg viewBox="0 0 100 24" aria-hidden="true"><path d="M2 12h96m-7-6 7 6-7 6" /></svg></div>
<div class="concept-machine"><span>Gateway host</span><div class="concept-card"><strong>Gateway</strong><span>Dispatch / status</span><span>Records / artifact index</span></div></div>
<div class="concept-connection"><span>WebSocket</span><svg viewBox="0 0 100 24" aria-hidden="true"><path d="M98 12H2m7-6-7 6 7 6" /></svg></div>
<div class="concept-machine"><span>Machine A</span><div class="concept-card"><strong>Executor A</strong><span>Processes / local files</span></div></div>
<div class="concept-connection"><span>WebSocket</span><svg viewBox="0 0 100 24" aria-hidden="true"><path d="M98 12H2m7-6-7 6 7 6" /></svg></div>
<div class="concept-machine"><span>Machine B</span><div class="concept-card"><strong>Executor B</strong><span>Processes / local files</span></div></div>
</div>
<figcaption>Here, the services run on three separate machines. Dashed outlines mark machines; solid cards mark programs. Blue arrows show who initiates a connection. Executors connect to Gateway; dispatch and status updates share that WebSocket. Previews follow a separate path: browser → Gateway → device file service. For cross-device inputs, the consuming device fetches files directly from the producer, whose file service must be reachable.</figcaption>
</figure>

Gateway and the device service can run on the same machine or on separate machines.

<figure class="concept-diagram" aria-label="One machine: the user accesses Gateway through the browser canvas. Gateway and the Executor share a dashed machine boundary. The Executor connects locally to Gateway; Gateway accesses the local file service for previews.">
<div class="concept-flow concept-access concept-colocated">
<div class="concept-person"><strong><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM2 18v-3a4 4 0 0 1 4-4h2M10 11h12v9H10zM8 22h15M15 14l-2 2 2 2m3-4 2 2-2 2" /></svg></strong></div>
<div class="concept-connection"><span>Browser canvas</span><svg viewBox="0 0 100 24" aria-hidden="true"><path d="M2 12h96m-7-6 7 6-7 6" /></svg></div>
<div class="concept-machine concept-shared"><span>One machine · local connections</span>
<div class="concept-card"><strong>Gateway</strong></div>
<div class="concept-deployment-links"><span>WebSocket</span><svg viewBox="0 0 100 24" aria-hidden="true"><path d="M98 12H2m7-6-7 6 7 6" /></svg><svg viewBox="0 0 100 24" aria-hidden="true"><path d="M2 12h96m-7-6 7 6-7 6" /></svg><span>HTTP previews</span></div>
<div class="concept-card"><strong>Device service</strong><span>Executor</span><span>File service</span></div>
</div>
</div>
<figcaption>The browser still connects to Gateway; only the services’ location changes. With default ports, the node connects to ws://127.0.0.1:8828, the file service listens on 127.0.0.1:8829, and advertised_url can be http://127.0.0.1:8829.</figcaption>
</figure>

## Find your results and their history

Looking through output directories, you might wonder which execution created a file and which parameters it used.

In HoloLab, registered output files and directories appear in **Artifacts**. The producing task record (**Job**) lets you trace parameters, the device, algorithm version, and logs. A run record (**Run**) links a workflow snapshot to its tasks. Previews depend on the artifact type and available viewer; progress depends on the Pack reporting parseable updates.

For the relationships and what gets reused on a rerun, see [Artifacts and run history](run-records.md).

## Let the system schedule the work; focus on the logic

Processing a dataset usually means planning batches and concurrent execution as well as the algorithm itself. In HoloLab, you express the processing logic on the canvas; the system handles batching, concurrency, and scheduling according to your configuration.

It does not automatically fuse models. Any speedup depends on the algorithm, data, and device resources.

**Planned:** a compact execution mode could compile away nodes that only move or copy data, reducing overhead without changing results. This node-elimination capability is not available yet.

Exporting a workflow as a deployable Shell (sh) script is also planned, so the logic assembled on the canvas could run independently. This is not available yet either.

## If you have used ComfyUI

Nodes and links will feel familiar, and ComfyUI also checks connection types.

HoloLab integrates external programs through Packs and lets you choose execution devices, rather than relying on the usual server-side Python custom-node model. Array dimensions and axis names also matter, as do the relationships between runs, tasks, and artifacts.

Next: [Install and run →](quick-start.md)
