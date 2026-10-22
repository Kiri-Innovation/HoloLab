---
title: Fix algorithm dependencies with package identity
group: Collaboration and reproducibility
order: 50
---
# Fix algorithm dependencies with package identity

> **Under development**: The capabilities described in this section are under development. This notice is removed when they are complete.

Finding an algorithm with the right name is not enough when a colleague opens a workflow. The system must resolve its exact version and verify that the code matches the recorded identity. Names identify packages, versions express contracts, and hashes provide evidence.

## Include a scope from the first package

Package identity uses `scope/name@version`, for example `kiri/fastgs-silhouette-train@0.1.0`. Public packages include a scope even when the registry has only one.

Package names appear in workflow files, snapshot records, and port type tags. Adding namespaces after these records accumulate requires rewriting historical identities in all three places. Reserving the scope from the start avoids that migration.

Workflows accept exact versions only, not version ranges. A range can resolve to different dependencies at different times, breaking the invariant that a version without `+` has a fixed identity.

## Hash the sources that actually supply the code

The package hash covers a Merkle tree containing:

- The manifest.
- Code inside the package.
- The **resolved identity** of every declared dependency, including external repository commit SHAs and actual pip dependency versions.

Algorithm code can come from submodules or external repositories rather than the package directory itself. Hashing only that directory allows the same `name@version` to retain the same apparent evidence while upstream code changes. Such a hash cannot establish which code actually ran.

**The execution environment is excluded from the hash, but its requirements must be declared in the manifest.** Runtime requirements such as Python, conda, and CUDA, and hardware requirements such as GPU memory and architecture, determine whether execution is possible. A frequently changing execution environment is not code identity. Resolved identities of declared dependencies still participate in the hash; excluding the execution environment does not exclude those identities.

**Hashes are evidence; versions are contracts.** Hashes do not participate in type compatibility or cache decisions. Editing one line during development therefore does not, merely by changing a hash, turn every downstream node red. Staleness propagation caused by changing a variable value is a separate rule and still applies.

## Run development versions and capture their actual identity

`0.1.1+` is a valid, runnable development version. The trailing `+` is only a marker; it does not contain a hash. Snapshot creation computes the hash at that moment, giving the local code state a small version identifier, or build number, within the development version.

**When every package referenced by a workflow has no trailing `+`, and therefore has a definite hash, the pipeline is reproducible.** This means the pipeline can be reconstructed, not that its numbers or artifacts are identical. A development package is runnable, but its version marker alone does not establish a fixed code identity.

## Publish from within HoloLab

One-click publication follows this sequence: **freeze external dependencies → compute the hash → assign the release version → enter the registry**. Publication turns mutable development state into a definite, resolvable identity.

The registry uses a single central authority. Private needs are handled first through private scopes in that central registry. Version 1 uses a static index and object storage rather than a dynamic registry service. Static content supports mirroring and caching and reduces service failure points. Publication consists of uploading a tarball and atomically updating the index. Self-hosted registries are outside this stage's scope; source configuration remains separate from recipes.

Two rules are mandatory:

1. **A published `name@version` is permanently immutable.** Uploading different content under the same name and version is forbidden; otherwise the version-to-hash relationship is meaningless.
2. **Versions can be yanked, not deleted.** Yanking marks a version as no longer recommended while keeping recorded references resolvable. Deleting it would leave historical snapshots without their dependencies.

## Keep package sources in local configuration

A workflow contains an identity such as `kiri/fastgs-silhouette-train@0.1.0`, never a source address. Local configuration holds the source list, for example “private source, public source.” This follows the separation between dependency names in `requirements.txt` and source selection in `pip.conf`.

Where to look is an environment concern, not part of the recipe. This boundary allows private deployments to obtain packages from different locations without rewriting workflows.

Resolution has three outcomes. The last must not become an ignorable warning:

| Outcome | Behavior |
| --- | --- |
| Package found and hash matches | Proceed |
| Package not found | Identify the package that needs installation |
| Package found but hash differs | **Hard stop**; do not continue installing or running mismatched content |

## Add release evidence without replacing history

Publishing after a development run does not rewrite a completed snapshot as though it originally used the release. A `published_as` annotation connects a release with the same hash while preserving the original identity. [Snapshot and release linkage](/docs/contributor/variables-bindings-snapshots#connect-a-release-to-existing-snapshots) describes the rule and the exception for runs still in flight.
