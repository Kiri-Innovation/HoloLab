# Turn an algorithm into a HoloLab Pack

For a concrete packaging task, orient the user with the
[task roadmap](#guide-the-conversation-and-show-progress) before implementation.
Start with design and a small canvas prototype. Explain release mechanics when
the prototype is ready to review. Treat publication as optional until the user
authorizes it, and open [the release guide](pack-to-release.md) when the work
reaches release preparation.

Assume the user knows their algorithm and may be new to how HoloLab models
work. Start with what the user has already provided. Consult the relevant
source or existing Packs when a split, interface, reuse decision, or uncertain
claim depends on it. Before each decision that affects the design, explain the
relevant concept through this algorithm:
what the choice is, why it fits, and what someone using the canvas would see
or do. Then give your recommendation and ask a focused question only if an
unresolved preference would change the design. Explain concepts as they
become relevant and let the user answer in their own words.

When discussing the value of packaging, explain what the user will receive. The goal is
“one-click reproducibility” for the person receiving the node. They get
a versioned node they can rebuild on another compatible machine through
the standard install and setup steps. The exact version and expected code
hash verify they got the same package code. Dependency declarations and
setup rebuild the runtime to the same requirements. Tell the user which
dependency versions, sources and weight revisions are actually pinned.
A declaration alone leaves unpinned transitive dependencies free to resolve.
To repeat a run, use the same inputs and parameters. Platform or GPU
differences and randomness can still change the output bytes.

For example, “In MegaSAM, the person using the canvas connects a sequence of
images and gets a timed 3D reconstruction. We can keep the inference and
model setup inside the node so they deal only with that input and result.”
Work from the evidence and the user's goals. Once the design is clear, the
implementation steps should be straightforward.

### Guide the conversation and show progress

As the primary agent, send the user one line with the route for this task,
the current stage, and the next action. Do this before delegating
implementation, editing files, or changing the canvas. You may first
investigate read-only. Tell the user you are in Design and what you are
checking. Send the roadmap yourself in a user-visible message or progress
card. Internal plans and sub-agent output do not count.

For example, “Design → Adapt → Canvas smoke test → Release (optional) ·
Now: Design · Next: check what the downstream node reads.” Include only
stages relevant to the current scope. At each stage change or blocker and
before starting a long run, give the current stage, one piece of evidence or
the blocker, and the next action. Use plain stage labels unless a percentage
has a real measurement. Continue routine work within the user's authorization.

Resume from the latest evidence. Confirm what was completed, what changed,
and the next pending action. Repeat checks affected by a change and reuse
the rest. During a wait, report what is pending and continue independent work
when possible. Ask one focused question only when a decision or permission
would change what you can do. The examples below show what to tell the user
and what evidence to carry forward.

## Understand the algorithm and the intended experiment

**Stage 1/7 · Design.** Establish what the algorithm and its likely producers
and consumers do. Explain relevant types with a concrete input and output from this
algorithm. Propose a single node or a directed graph of nodes, with branches
where useful, and explain which results users can inspect or reuse. Record
the purpose, graph and input/output contracts before adapting. Ask about a
choice that changes the graph, such as “Would you want to export again
without retraining?”

### Establish where the integration belongs

Check the repository, upstream revision, and license for the code, models,
weights, and assets you would ship. Explain three separate questions:

- **Repository maintenance.** Can the user add the integration to a repository
  they maintain, propose it upstream, or maintain it in a fork they choose?
- **Redistribution.** Which code and assets can be redistributed under their
  licenses or permissions? Write access alone does not establish those rights.
- **Community publication.** Which `scope/name` and authorized publisher would
  own a release? Repository and registry permissions are separate.

Recommend a location based on the evidence and ask only if the choice remains
open. For example, “The implementation is upstream and your checkout is
read-only. I recommend maintaining the integration in a fork if you want
control of its updates. Do you want to maintain one, or propose the change
upstream?” Obtain explicit authorization before creating a fork or publishing.

Decide the boundary at three levels. The repository is where code is
maintained, a Pack is a versioned release unit, and a publisher scope
controls who can publish in the registry.

- **Inside the same Pack:** keep its adapter, implementation helpers, and
  private scratch state together when another node has no reason to use them.
- **Separate release units in the same project:** give a reusable node or
  data contract its own manifest when it has an independent consumer or
  release lifecycle. For example, a format maintainer can own a generic
  COLMAP4D splitter and its component types because they can maintain the
  contracts on both sides of that boundary.
- **A separate project's publisher scope:** publish a workflow-specific
  conversion under the publisher responsible for its assumptions. A Pi4
  projection tailored to one workflow can consume Pi4 output and emit an
  existing COLMAP4D type without owning either format. Reuse types across
  scopes. A versioned Type Pack's `typename` is global, with a version for
  the release. Publisher scope governs release authority and is separate
  from the type's identity. See [versioned Type Packs](../../docs/pack-registry.md#versioned-type-packs).

For a repository with several integrations, a suggested layout is
`packs/algorithms/<name>/manifest.yaml` with its adapter and, when a custom
type is needed, `packs/types/<type>/manifest.yaml` with its codec. The existing
`packs/<name>/` layout remains suitable for a single Pack and for current
projects. Keep the repository's established layout when it already works.

On the current scanner, register the actual checkout as an absolute Pack source
in the compute node's **Pack sources** setting (`pack_dirs`). The nested
layout fits the three-level scan from that root. A manifest stops descent,
and nested Git checkouts and excluded directories can limit discovery. On an
older or differently configured node, register the individual Pack directories
or exact manifest paths and check what the target build discovers. For type
previews and summaries, the gateway also needs access to the configured type
source. See [source discovery](../../docs/pack-spec.md#multi-pack-source-layout)
and [source registration](../../docs/writing-a-pack.md#registering-a-pack-source).

### Trace what the code actually does

When the description leaves a design choice unresolved, inspect the relevant
native entry point, example, test, or reader/writer. Check the upstream and
downstream contracts where they affect the proposed boundary. Trace the smallest
representative native case and record the revision, invocation, dependencies,
sample inputs, and expected outputs. When authorized to run it, capture the
result as the adapter baseline.

Tell the user whether you found a single operation, several tasks served by
one model, or a pipeline with usable boundaries. Ground the recommendation in
what the code does. Ask about a real workflow choice only if it remains open,
such as whether they need to resume training separately from exporting results.

## Design the node and its contracts

### Choose the node boundaries

Start with one Pack per independently useful operation. A shared model may
support several tasks. Split tasks when they have different inputs or
consumers, or when users benefit from separate inspection or retries. Keep
phases together when they share state that has no useful cross-node contract.
Temporary files and private execution state belong in scratch. State someone
needs to resume, inspect, or feed to another node needs a declared artifact
with a consumer that can read it. For canvas-only inspection, use the
[view sticker](experiments.md#查看某个输出使用-view-贴纸不要写透传-pack).

Before proposing a new node, look for equivalent building blocks in the
[Pack catalog](experiments.md#agent-api-reference) and existing workflows.
Prefer built-ins and suitable official registry releases for common steps.
Check consumer behavior and the actual artifacts as well as the declared
ports. Reuse only when the object meaning, storage, array axes and ordering,
and timing agree. Choose an explicit conversion when it carries real
meaning. Avoid swapping axis labels to fit a wire, changing the time basis,
or inventing an intermediate format just to reuse a node.

Expose a boundary when its artifact has an independent consumer, is worth
inspecting or caching, allows a useful rerun, or lets users replace that
operation. Keep glue and tightly shared, private state inside the Pack.
Leave reusable general-purpose steps on the canvas when they offer one of
those benefits. In a design sketch, mark steps as **reuse** or **new**.

**Reference patterns for the agent.** Use these to reason about similar
designs. Explain only the relevant choice to the user.

- **Single video to timed points.** A completed four-job branch used built-in
  Single Video Source and Frame Extraction to produce `image[frame]` for
  the project Pack MegaSAM Track, which emitted one `colmap4d` model.
  COLMAP4D Split consumed that model and emitted `colmap4d-point-cloud`
  on `4d_points` along with camera and image components. Reuse extraction
  when a frame-level step is useful. Add Split when a consumer needs a
  component. Keep the full model for consumers that need it. See the
  [component contracts](../../docs/colmap4d-components.md#assembly-and-downstream-boundaries).
- **Multiple cameras, reconstruction, then training/export.** In a completed
  workflow, built-in Frame Extraction and Regroup changed the organization
  from `image[cam,frame]` to `image[frame,cam]`. A selected frame went to
  the community COLMAP SfM Pack. Shared calibration fed triangulation and
  undistortion, and Merge COLMAP combined their outputs as
  `colmap-folder[frame]`. The project-specific STG init, train, and SplaTV
  export nodes then consumed the folder, training state and supervision, and
  trained model respectively. This pattern makes calibration reusable and
  export independently rerunnable. Use it when the capture, calibration
  and downstream contracts fit the same structure.

The following exchange is illustrative. A FastGS-Pano source example on the
development node separates training from static export: export reloads the
saved iteration's point cloud and deformation state. That is evidence for
an export boundary. The training code's optional checkpoint save is disabled
in the sampled entry, so verify resume separately before promising it.

> **Agent:** The trainer writes a checkpoint that its export command can
> reload. I recommend one node for the current workflow. If you need to resume
> training or export again from a saved checkpoint, we can give that checkpoint
> a declared output and split the steps. Do you need either workflow?
>
> **User:** I want to try several exports without retraining.
>
> **Agent:** Then I recommend a training node that outputs the existing
> checkpoint format and an export node that reads it.

**STG and FTGSPP show where init, train, and export can separate.** In the
inspected STG manifests, init produces training state and supervision; train
reads both and emits an updated state that the SplaTV exporter can read as a
model. In FTGSPP, train reads the initialization artifact and the viewer export
reads the trained model. These splits let users retry training from a readable
initialization or try another export from a saved model. Keep phases together
when the handoff is process-local or has no independent use. Resuming a run
requires a separately verified checkpoint contract.

### Reuse types before designing a new one

Think of the algorithm as a function that consumes and produces typed data
objects. The logical type defines what the object means and what must hold for
it to be valid. Its on-disk representation defines the files or directories
used to exchange it. A codec reads those files into the object and writes the
object back. In a path-based Pack, the adapter or algorithm handles those
reads and writes. In typed execution the worker uses the codec. Collection
axes describe how objects are grouped across a port. Describe those axes
outermost first and distinguish them from dimensions inside an object.

Check existing types, collections, separate ports, and conversion Packs before
proposing a new type. Use the [port contract](../../docs/pack-spec.md#ports--tags-are-the-object-type)
and [axis rules](../../docs/pack-spec.md#dim-labels) to compare the actual
producer and consumer. For example:

- **Images.** The built-in [single-image source](../../hololab/builtin_packs/single-image-source@0.1.0/manifest.yaml)
  supplies one image. The [image-array source](../../hololab/builtin_packs/images-array-source@0.1.1/manifest.yaml)
  supplies an ordered collection along `[frame]`, a one-dimensional
  `Array[Image]` in a typed interface with the matching codec. As an
  illustrative two-axis case, `[group, cam]` groups camera images under each
  group. It is a collection of collections, with a separate meaning for each
  axis. Image height and width are internal to each image. Check the target
  execution mode's codec support for the chosen shape. In a local fixed-rig
  undistortion case, the adapter checks that calibration agrees across groups
  before emitting one calibration bundle alongside `[group, cam]` images.
  A consumer can reuse that bundle across groups while keeping camera order.
  When a downstream stage needs only pixels, use an explicit conversion from
  timed images and keep the timed object available for consumers that need it.
- **COLMAP and COLMAP4D.** The [FastGS training Pack](../../community_packs/fastgs-silhouette-train@0.1.0/manifest.yaml)
  reads a COLMAP folder. A COLMAP4D consumer can require additional semantics
  such as image and point timestamps. The
  [COLMAP4D assembler](../../community_packs/merge-colmap4d@0.4.0/manifest.yaml)
  reads camera, point, and image components on separate ports and emits one
  COLMAP4D model. Use the existing component types when callers operate on them
  separately. [COLMAP4D Split](../../community_packs/colmap4d-split@0.1.0/manifest.yaml)
  exposes the `4d_images` port as a scalar `colmap4d-images` component. Its
  decoded RGB/RGBA pixels live under `images/<NAME>` and match the camera
  records by name and dimensions. The component contract joins timing through
  camera records by name and image ID; the pixels carry no second authoritative
  time map. The assembler can consume the components separately and enforce
  shared names, tracks, and timestamps. Use an ordinary image array when the
  consumer only needs ordered pixels, and the component when it needs those
  COLMAP4D relationships. See the
  [component profile](../../docs/colmap4d-components.md#colmap4d-images) and
  [assembly contract](../../docs/colmap4d-components.md#assembly-and-downstream-boundaries).
- **Pi4 inference and a lossy downstream conversion.** Keep the official
  inference and its full result at the inference boundary. The inspected
  development adapter serializes that result as `pi4_native_v1`; the
  [type notes](../../hololab/types/pi4-output-vannila/README.md) distinguish
  the sampled official scene export from a native tensor archive. A separate
  development converter projects finite RGB geometry frame by frame into a
  timed `colmap4d-point-cloud` component. It preserves source frame indices,
  order, and timestamp provenance and writes empty tracks. This boundary is
  useful for consumers that need geometry and time. Consumers that need track
  correspondences still need the full result. A projection must not invent
  tracks or quietly change the inference step.

When outputs can be consumed independently, give them separate typed ports.
Before fixing the ports or delegating implementation, give the user a short
type recommendation yourself. Base it on existing types and the actual
producer and consumer. Recommend a specific existing type when it fits. Say
which facts remain unverified and what you will check before committing to the
design.

When existing types, composition, and conversion cannot preserve semantics
that cross a node boundary and have independent consumers or reuse across
Packs, recommend a named Type Pack. Explain what the closest existing type
loses, who writes and reads the new type, and the minimum disk layout and
invariants the codec must preserve on round trip. For example, “Reuse
`image[frame]` for ordered pixels. For the alignment results shared with
another Pack, I recommend a Type Pack that stores one record per frame with
its stable image ID and coordinate system. A pixel array cannot carry those
relationships.” Keep intermediate state that only the Pack uses private.
Give a recommendation in plain language rather than asking the user to pick
a type. Defining a type and authorizing its publication are separate decisions.
Follow the [Type Pack contract](../../docs/pack-registry.md#versioned-type-packs)
for the codec and exact typed-port binding.

### Decide what people need to see on the canvas

Explain what opens when someone clicks the output. Common registered types
such as images and video already have viewers that account for the output
shape and storage. An output with an available artifact and no matching
viewer can still show basic information such as size and file names.
Ask whether people need to see the contents to make a decision. For example,
a companion node can turn a specialized set of tracking results into ordinary
images for later steps. The specialized result can also stand on its own as
the final deliverable. In either case, add a viewer when inspecting that result
on the canvas serves the user's workflow.

First look for an existing viewer in the algorithm repository. Check its
license and whether it can read the released artifact layout and run in
HoloLab's iframe. Reuse a compatible viewer when available. Choose the narrowest
registration that serves the workflow:

- **For a type shared by multiple nodes**, declare a type-level preview with
  `preview.viewer` and, for an HTML viewer, `preview.entry` in the type
  manifest. The output tag must match the discovered type. The
  [COLMAP4D images manifest](../../hololab/types/colmap4d-images/manifest.yaml)
  and [its viewer](../../hololab/types/colmap4d-images/viewer.html) show this
  pattern. The gateway must discover the type definition from a trusted,
  accessible source. Installing a typed-worker dependency alone does not
  register its viewer with the gateway. See
  [source discovery](../../docs/pack-spec.md#multi-pack-source-layout) and
  [versioned Type Packs](../../docs/pack-registry.md#versioned-type-packs).
- **For one Pack's output or diagnostics**, use the output's `preview` to
  select an appropriate existing viewer, or use the Pack's `previews` cards
  for files it already writes, as in the
  [BiRefNet first-mask card](../../community_packs/birefnet-mask@2.2.5/manifest.yaml).
  For a Pack-owned diagnostic iframe, use `node_viewers` with
  `renderer: iframe` and a Pack-relative `entry`. It opens a node panel
  without adding a data port. See the [preview spec](../../docs/pack-spec.md#previews)
  and the [node viewer schema](../../hololab/manifest/schema.py).

Resolve type viewer entries inside the type manifest directory and
`node_viewers.entry` from the algorithm package root. Keep linked JS, CSS,
and other assets reachable relative to the entry. For a published viewer,
check that its entry and dependencies survive archive exclusions and are
servable from the installed or configured source. Viewing reads an existing
artifact. Use a [view sticker](../../docs/workflow-schema.md#view-stickers)
to pin it on the canvas, and use an algorithm node when you need to generate
or transform an artifact.

### Decide what one invocation processes

Compare the native call with the data available from upstream. One invocation
can process an entire collection. Use [arrayed execution](../../docs/pack-spec.md#arrayed-and-arrayable)
when the workflow needs independent work over an additional outer axis.

For a RoMa-style adapter, determine whether its native batch processes multiple
items inside one invocation and how outputs map back to those items. HoloLab
fan-out adds a framework-managed outer axis and multiple independent jobs.
Choose it only when each outer element can run independently and the outputs
can be aggregated with their correspondence intact. If producing timestamped
Colmap4D points, a valid canvas connection checks the declared interface.
Running the intended assembler on the actual output checks whether the payload
is readable, including track consistency and timestamp coverage. See the
[point-cloud contract](../../docs/colmap4d-components.md#colmap4d-point-cloud)
and [assembly checks](../../docs/colmap4d-components.md#assembly-and-downstream-boundaries).

A local RoMa implementation illustrates the tradeoff. Its sequence code shares
one matcher across frames. Its per-frame entry initializes a matcher per
invocation, receives all cameras for that frame, and can fan out over the outer
frame or group axis with shared calibration. Pick based on resource and retry
needs, and check that consumers accept the resulting point data. A reader
requiring observed tracks can reject output with empty tracks even when the
ports connect. The local examples describe decisions observed in source.
Check the target node's exact offering and whether it comes from a checkout
or an installed release before relying on any of them.

### Worked example: MegaSAM from image sequence to timed reconstruction

The published [`k4d/megasam-track@0.7.10`](https://packs.xenotech.studio/packs/k4d/megasam-track/)
shows how to turn a complex inference pipeline into one canvas node:

- **What the canvas user sees.** Connect one flat `image` array with a
  `[frame]` axis, or a `video-source`, and get one standard `colmap4d` model.
  For images, `frames_fps` sets the synthetic timestamps. The whole sequence
  is one invocation because the reconstruction uses information across frames.
- **What the author packages.** The typed adapter stages inputs in scratch,
  runs the native MegaSAM chain, standardizes its intermediate components,
  and assembles and returns a `Colmap4D` object. The release bundles the
  adapter, inference code, setup scripts, and assembly code. Those details
  stay behind the canvas ports.
- **How a node gets ready.** Install retrieves and verifies the exact code
  archive. Setup prepares the mapped `megasam` runtime, checks CUDA-enabled
  PyTorch, builds and checks native extensions, and prepares the checkpoints
  and pinned UniDepth assets in the node's Torch and Hugging Face caches.
  The release ships the weight-fetching code. Weight files come from their
  external sources during setup and stay in the caches the runtime reads.
  After setup, a compatible CUDA compute node can run the Pack from the canvas.

A recorded run of this version on a 4090 compute node consumed 81 images and
produced a `colmap4d` artifact. Its log records assembly validation. The
observed catalog offering was from a source checkout. The public archive
was checked separately, so this example establishes the canvas contract
and a working target-node run. Clean installation and setup from a fresh
environment and portability across platforms were outside that run evidence.

Before implementation, restate the plan in one sentence: “Put the integration
in [repository and folder], make [number and purpose] of nodes, use [input and
output types], and confirm [remaining design choice, if any].”

## Adapt and register the Pack

**Stage 2/7 · Adapt.** Tell the user what the adapter will wrap and which
interface it will expose. Use a validated manifest and an adapter smoke
against the native baseline as evidence. If the target build or supported
interface changes the implementation and cannot be established from the
instance or source, ask which target the user needs to support.

In the [chosen Pack layout](#establish-where-the-integration-belongs), keep a
thin `adapter.py` beside `manifest.yaml` and preserve the original algorithm
entry point. When the target build supports [Typed worker API 1](../../hololab/manifest/schema.py)
and the port codecs you need, make the HoloLab-facing function take the
declared Type objects as keyword-only data inputs and return the declared
Type object or a mapping keyed by output port for multiple outputs. Match
the declared array shape and preserve its ordering. `params` carries
controls and `ctx` provides execution context such as per-job scratch.
They are separate from the data ports.

MegaSAM's actual signature is
`track(*, frames: VideoSource | Array[Image], params: TrackParams, ctx: RunContext) -> Colmap4D`.
The adapter converts objects to the paths, tensors, or arguments the native
algorithm expects, calls it, and wraps its results as the declared output
objects. The worker decodes inputs and writes outputs through their codecs.
Keep borrowed files available through serialization. A path-based entry such
as `exec.shell` remains valid on supported builds. In that mode the adapter
reads and writes the declared paths itself. Keep inputs read-only, stage
temporary files in scratch, and measure peaks in a representative run.

Declare what the prototype needs to run. If it needs native extensions or
weights, use the [environment and setup guidance](pack-to-release.md#review-dependencies-and-setup)
for that step. Continue here to register and smoke-test the Pack.

Use the [writing guide](../../docs/writing-a-pack.md#migrating-an-existing-algorithm-into-hololab)
for layout, adapters, and execution choices, the [Pack Spec](../../docs/pack-spec.md)
for fields, and [environment setup](../../website/content/en/contributor/pack-environments.md)
for dependencies and weights. [Register the source](../../docs/writing-a-pack.md#registering-a-pack-source)
on the compute node and check the exact version and source in its inventory.
Discovery establishes that the node advertises the Pack.

## Prototype the workflow on the canvas

**Stage 3/7 · Prototype.** Build the smallest useful canvas draft with the
actual input node, the adapted Pack, and any consumer needed to test a
promised boundary. Look for a short, representative sample the user has
authorized, and propose a suitable public sample if needed. Ask only about
missing data or permission, for example, “May I use this short clip locally
to test the frame input?” Run a real smoke test and show the user the
workflow, the resulting artifact, and what the downstream node read. Use the
[experiment guide](experiments.md#choose-a-run-mode) for dispatch and
inspection. Suggest
a preview where it helps them judge an intermediate result. Ask for feedback
on a consequential uncertainty in the output or canvas experience. Carry
forward the smoke evidence and the user's feedback before declaring the
prototype accepted.

Record the workflow or snapshot, sample, runtime, Job, output Handle, and
consumer result. If a viewer is promised, check the representative artifact
and viewer assets. If evidence is missing, record what remains pending.
For release preparation, carry this record and the agreed input/output
contract into [stage 4 of the release guide](pack-to-release.md#prepare-the-smallest-distributable-archive).
