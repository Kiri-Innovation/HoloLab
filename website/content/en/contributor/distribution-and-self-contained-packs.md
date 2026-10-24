---
title: "Why we chose self-contained packs"
group: Collaboration and reproducibility
order: 70
---
# Why we chose self-contained packs

Out-of-the-box integration buys convenience through ongoing coordination of implementations and environments. Keeping algorithms independent requires explicit setup and validation for each pack. HoloLab chooses self-contained packs because resisting software decay and rebuilding workflows later matter more to us than eliminating configuration on the first run.

## The existing approaches behind our decision

These are practices we weighed, not three distribution options offered by HoloLab. Each assigns responsibility for implementation, dependencies, and upgrades differently. Putting code in a directory does not settle those responsibilities.

| Distribution model | Integration approach | Benefit | Cost |
| --- | --- | --- | --- |
| Clone each official repository | Keep each algorithm in its own repository and environment; wrap its entry point and interface | Stay aligned with upstream | Separate environments and inconsistent entry points require individual wrappers |
| Share an abstraction, as in diffusers | Adapt models to a common pipeline interface and foundation | A consistent interface | Every model needs adaptation; dependency ranges such as `>=x,<y` can conflict |
| Integrate centrally, as in ComfyUI | Rewrite implementations in the core repository and maintain them on a thin shared dependency stack | Capabilities work out of the box, without per-model integration setup | The core grows, implementations diverge from upstream, and new models keep arriving through community PRs |

Here, out-of-the-box means users do not have to assemble official repositories and plugins one model at a time. It still requires an installed runtime and downloaded weights. A shared interface also leaves model-specific implementation work to someone. Distribution determines who does that work.

## ComfyUI earns convenience through ongoing integration

The following observations come from an inspected ComfyUI instance. The counts describe that inspection, rather than every release or installation.

| Inspected location | Observation | Distribution consequence |
| --- | --- | --- |
| `comfy/ldm/` | Native implementations for 52 model families | Cloning the repository supplies inference code |
| `models/` | Only 5 manually downloaded files | Weights are acquired separately from implementations |
| `requirements.txt` | Just 36 lines, with no diffusers dependency | Core code also has no `import diffusers`; `diffusers_convert.py` handles weight-format conversion |
| `custom_nodes/` and `comfy_extras/` | The former was empty; the latter contained 152 built-in node files | Built-in nodes provide the available capabilities without requiring third-party plugins |

The implementations extend beyond generation: SAM3 covers segmentation, RT-DETR detection, MoGe geometry, and Depth-Anything-3 depth. Hunyuan3D and TripoSplat add 3D and Gaussian reconstruction capabilities.

These nodes do not simply wrap diffusers. Model families have different structures, and each needs its own adaptation and rewritten implementation. Community PRs supply much of this implementation work. MiniMax-H3, for example, is credited in the commit history to community developer kijai and was merged through a PR, rather than supplied by the model vendor or the official team.

**A flattened dependency stack makes one environment for 52 families possible.** The integration avoids both the official repositories' environments and the diffusers dependency stack. The project also publishes key packages itself and pins their exact versions, including `comfy-kitchen==…` and `comfyui-frontend-package==…`. Less setup for users means sustained implementation and dependency maintenance for the integrators.

Partner/API Nodes follow a separate distribution path. They expose cloud APIs through vendor partnerships; they are distinct from the local native implementations described here.

## HoloLab puts reproducibility at the pack boundary

HoloLab's choice is **self-contained packs, environment contracts, and package identity**, with a registry handling discovery and distribution. A central place to resolve identities can support independently maintained algorithms without becoming a central repository of model implementations.

**We value a workflow that survives a handoff or the passage of time more than a first run with less setup.** Running successfully today is not enough if the execution conditions cannot be explained and reconstructed later. Upgrading one algorithm's environment should leave other algorithms independent of that upgrade. We accept the work of maintaining a contract for each pack rather than making a single convenient environment our first priority.

Traceable sources, fixed dependency identities, and declared execution requirements let a pipeline be reconstructed later or elsewhere. [Package identity and the registry](/docs/contributor/package-identity-registry) establish which package is required; [workflow reconstruction](/docs/contributor/rebuild-and-distribute) separates the recipe from local state. Reconstruction does not guarantee identical numerical results or artifacts.

## The costs we accept at the pack boundary

Self-contained packs leave integration work to be done. This comparison shows where our choice places that work and which costs we accept.

| Model | Code ownership | Environment | Adding an algorithm | Main cost |
| --- | --- | --- | --- | --- |
| Central integration | Implementations live in the core repository and are maintained by integrators and contributors | Shared dependencies with centrally coordinated versions | Rewrite and adapt the model, then merge a PR into the core | A growing core, upstream divergence, and upgrades that affect multiple models |
| Self-contained packs | Algorithms stay in their own repositories; packs declare sources and execution contracts | Each pack declares and validates its requirements, containing upgrade effects within its boundary | Supply an entry point, typed interface, environment declaration, and traceable identity | Fragmented environments, dependency accountability for every pack, and explicit data exchange across environments |

“Self-contained” describes a complete execution contract. It does not require copying every dependency into the package. A pack must account for its code sources, dependencies, interfaces, and failure conditions without relying on undocumented state on its author's machine.

This choice retains the cost of separate environments. Every pack author must establish that the declared dependencies are sufficient, and operators may need to maintain several environments. Data crossing those boundaries needs agreed types, serialization formats, and file accessibility. An object in one process, or a path on one machine, is not automatically a usable input in another environment.

## Put the decision into everyday pack work

The following five rules turn that priority into obligations for pack authors. Explicit, validated execution conditions let future users rebuild a workflow without guessing what was on its author's machine.

1. **Keep algorithm implementations in their own repositories.** The platform declares `source_entry` and the interface. The entry point connects execution to the algorithm without moving its implementation into the platform or concealing its source.
2. **Declare dependencies in the manifest's environment requirements.** Include runtime, library, and hardware requirements. Validate them before execution and fail explicitly when they are unmet; do not silently substitute another implementation or environment.
3. **Specify typed inputs, outputs, and failure conditions.** Explain data conventions and the conditions that prevent execution. The pack owns this contract, including data exchanged across environments; it cannot assume the platform will fill in missing requirements.
4. **Keep upstream dependencies traceable.** Record commits or versions for external dependencies. A directory copied at an unspecified time cannot identify the code that actually ran.
5. **Declare an external runtime when the algorithm depends on a larger integration.** An H3 pack that runs through ComfyUI must identify ComfyUI as an external runtime dependency. Record the required version and state, and explain coupling risks from shared process state, dependencies, or configuration changes. A pack wrapper alone does not remove that coupling.

**A capability must be expressible as typed inputs and outputs plus reproducible execution. If it cannot, defer integration.**
