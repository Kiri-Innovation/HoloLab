---
title: Rebuild a workflow on another machine
group: Collaboration and reproducibility
order: 40
---
# Rebuild a workflow on another machine

> **Under development**: The capabilities described in this section are under development. This notice is removed when they are complete.

When you hand a working pipeline to a colleague, they need a way to assemble and run it without taking over your machine. That requirement defines the boundary between distributable content and local state. Workflows travel like code; algorithm packages can be synchronized and distributed like pip packages. Pinning versions serves this purpose rather than being the purpose itself.

## Share instructions for rebuilding, with an explicit guarantee

Distribution carries a **recipe, not its outputs**. The recipient gets the instructions and produces outputs by running them. Two levels of reproducibility must remain distinct:

| Level | Meaning | System guarantee |
| --- | --- | --- |
| Pipeline reconstruction | Given the recipe and packages, another person can assemble and run the same topology, parameters, and versions | Guaranteed |
| Identical results | The resulting numbers or artifacts are exactly the same | Not guaranteed |

Identical results also depend on random seeds, hardware and library versions, and nondeterminism inside the algorithm, such as multithreaded reductions or cuDNN algorithm selection. Reconstructing a pipeline does not establish that these conditions are identical or remove nondeterminism.

The Gateway therefore acts as a **resolver and executor**, rather than the owner of the data. The user's workflow files and the package ecosystem are the distributable assets. Changing the Gateway does not change the recipe's identity.

## Separate five layers before deciding what travels

| Layer | Contents | Carrier | Distributed? |
| --- | --- | --- | --- |
| Recipe | Topology, parameters, pin configuration, variable declarations and defaults | Workflow file | Yes |
| Dependency | Algorithm packages identified by `scope/name@version`, plus hashes | Package ecosystem and registry | Yes |
| Environment | Runtime dependencies such as Python, conda, and CUDA; hardware requirements such as GPU memory and architecture | Package manifest declarations | Yes |
| Binding | Device selected for each node and local variable values | Local SQLite | No; bindings cannot travel with the recipe |
| Record | Runs, jobs, snapshots, events, and logs | Local SQLite | No; records need not travel with the recipe |

A variable's declaration and default belong to the recipe; the value used locally belongs to the binding. The environment layer describes what execution requires, while the binding layer describes the local choices that meet those requirements. Execution records preserve evidence without becoming part of the shared recipe.

To classify a field, ask: **On another machine or execution endpoint, should this stay the same, or should it be created again?** Content that stays the same belongs to the recipe; content recreated for the new setting belongs to local state. Package identity and environment requirements remain distributable through dependencies and manifests respectively.

## Why committing the existing JSON is insufficient

The current implementation stores the whole workflow in a `draft_json` JSON blob, mixing several kinds of information:

| Fields | Role |
| --- | --- |
| `nodes`, `edges`, `params`, `algorithm_name`, `algorithm_version` | Recipe, with machine-specific parameter values requiring separate treatment |
| `assigned_node_id` | Local device binding containing a machine UUID |
| An absolute `/cloud/...` path in `params.video_dir` | Environment-specific binding, expressed through a variable |
| `position`, `preview_open` | Personal view state |

Committing that blob does not make it portable. On the recipient's machine, `assigned_node_id` names a device that does not exist and `video_dir` names a path that does not exist. The issue is the sharing boundary, not whether JSON can be stored in git.

Separating these concerns preserves topology and parameter intent in the recipe while the recipient supplies local devices and paths. [Variables, bindings, and snapshots](/docs/contributor/variables-bindings-snapshots) explains parameter references and how execution freezes their resolved values.

## Connect the recipe to execution through package identity

A recipe references algorithms through exact package identities. Released versions are immutable; snapshot hashes identify the actual code used by development versions. Local configuration determines where packages are obtained. [Package identity and registries](/docs/contributor/package-identity-registry) defines these contracts.

**Core invariant: when every package referenced by a workflow has no trailing `+`, and therefore has a definite hash, the pipeline is reproducible.** Here, “reproducible” means **pipeline reconstruction only**: with the recipe and packages available, declared environment requirements met, and local bindings supplied, the same topology, parameters, and versions can run. It does not promise identical numbers or artifacts.
