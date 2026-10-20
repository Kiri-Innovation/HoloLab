---
title: Nodes, data types, and links
group: Full Features Intro
order: 27
---
# Nodes, data types, and links

When you connect two algorithms, matching their input and output names is not enough. HoloLab checks the declared data types before accepting a link.

## An algorithm with inputs and outputs

A Pack describes an algorithm’s inputs, outputs, and parameters; its node puts those interfaces on the canvas. For example, `frame-extraction` receives a video directory through `video_dir` and produces an image sequence through `frame_sequence`. Parameters control how it runs; see [Your first workflow](workflows.md) for editing them.

## Ports carry type declarations

The connection points are **ports**: inputs on the left receive data, and outputs on the right pass it on.

Each port has a machine-readable declaration: type tags identify the data, while array structure and axis labels describe collections. Here, `video_dir` accepts `video-source`; `frame_sequence` outputs an array of `image` elements along a `frame` axis. The port’s name is a label, not its type.

<figure class="doc-screenshot">
<picture><source srcset="/images/docs/ports-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/ports-light.webp" alt="A video source output connected to the video_dir input of frame-extraction in the STG workflow." width="1462" height="280" loading="lazy" decoding="async" /></picture>
<figcaption>The video source feeds frame-extraction through compatible ports. Port names describe their role; type declarations determine compatibility.</figcaption>
</figure>

Drag an output to an input to connect them. HoloLab checks compatibility during the drag and rejects an incompatible link when you release it. Checks include type tags, array depth, and axis names where required. A single image and an image collection are different shapes; similar names alone do not make them compatible.

## Links carry dependencies

At runtime, a link tells the system to pass the upstream output’s artifact reference to the downstream input. For inspecting this context through an agent, see [Everything Agent-Oriented](agent-oriented.md).

Type checks protect the declared data contract. They do not guarantee that the algorithm’s result is correct.

Next, [view the results](results.md). To expose your own algorithm as a node, see [Write a Pack](/docs/build/writing-a-pack).
