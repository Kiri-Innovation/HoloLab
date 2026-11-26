# Prepare, verify, and publish a HoloLab Pack

Use this guide once a Pack is ready for release preparation. For a new
integration, start with [algorithm to Pack](algorithm-to-pack.md). If the
user came directly for release work, confirm the exact candidate and reuse
any existing prototype evidence. Continue the seven-stage plan at the
first pending release stage. Show the current stage, its evidence or
blocker, and the next action at each transition or wait.

## Prepare the smallest distributable archive

**Stage 4/7 · Prepare release.** Once the prototype meets the user's needs,
explain why publishing matters. A recipient can install the exact version
and check its code hash, then rebuild its environment. Use a mature Pack
already on the user's canvas as an example when available. Verify its badge
using the conditions below before explaining it.

Review the candidate with the user in terms of what a fresh machine needs.
Use [package-level `.packignore`](../../docs/pack-registry.md#包级源码排除)
to remove unrelated source, data and generated files while retaining the
adapter, required code and assets, license notices and setup script. Check the
archive contents, dependency declarations, native extension build and
weight setup before freezing the candidate. Ask about a concrete unresolved
requirement, for example, “Do recipients have permission to fetch this model
weight?” Record the contents, requirements and any external prerequisites.

### Review dependencies and setup

Declare Python and ordinary dependencies in `requires.python` and
`requires.packages`, and declare Conda-provided toolchain packages in
`requires.conda_packages`. Record host prerequisites separately, including
the GPU driver and the minimum CUDA version it must support (`requires.cuda`).

Prepare resources explicitly with `pack setup`. When custom steps are needed,
the author supplies a manifest-declared [`setup.script`](../../docs/pack-spec.md#setup-hook)
to build C++/CUDA extensions for the target environment and fetch, verify, and
cache [model weights](../../website/content/en/contributor/pack-environments.md#download-weights-into-node-storage).
Use `setup.needs` for toolchain checks and `setup.modules` for post-build
import checks. Keep preparation behind setup. The canvas exposes algorithm
controls, and each Job loads the prepared resources. If a resource is missing,
report which one is missing and prompt the operator to run setup.

### Rebuild the environment and run a small workflow

**Stage 5/7 · Rebuild.** Freeze the candidate in a temporary `file://`
registry and record its exact version and code hash. Tell the user where
you'll reinstall and what sample you'll reuse. Treat the install receipt,
setup and import results, and smoke output from the same sample as separate
evidence. If neither a clean host nor an isolated instance is available,
report the blocker and ask which test environment is accessible.

Prefer a separate clean test machine that meets the Pack's declared
requirements. Use the same candidate archive and a representative, authorized
sample to run install, setup, and the smallest real workflow. Windows with WSL2
is a useful additional Linux test environment for exposing path, dependency,
and toolchain assumptions. For GPU Packs, confirm driver and GPU access from
inside WSL2 first. Record the result as WSL2 coverage; native Windows support
requires its own evidence.

If only one machine is available, use a temporary instance:

1. **Isolate the instance and install the archive.** Use a new temporary
   directory. Set `HOLOLAB_HOME` there before any CLI step. Write a fresh
   `node.yaml` with temporary absolute paths for `installed_packs`,
   `workspace_root` and `cache_root`, and choose an unused `file_server_port`.
   Leave production source directories and environment mappings out of this
   config. Follow the [local registry exercise](../../docs/pack-registry.md#本地自验)
   to stage the candidate in a temporary `file://` registry. Install by exact
   `scope/name@version` with `hololab pack install <spec> --config <node.yaml>
   --source <file://registry> --expected-hash <code-sha256>`. Verify the node
   later offers that installed identity rather than a development checkout.
2. **Rebuild from the declared requirements.** Separate host prerequisites
   such as the GPU driver from what setup is meant to prepare. For a conda
   Pack, create a fresh temporary prefix with the declared Python version and
   map its logical runtime name to that existing prefix in `envs`. Run
   `hololab pack setup <spec> --config <node.yaml>`. The spec selects the
   installed Pack, and `--config` selects the node configuration and its
   runtime mappings. Use that same test config for the instance and keep the
   production configuration untouched. An explicit fresh prefix
   matters because setup can otherwise reuse a same-named environment. For a
   host-runtime Pack, prepare the test node's host interpreter separately.
   Point Conda package and pip caches at empty temporary directories
   (`CONDA_PKGS_DIRS`, `PIP_CACHE_DIR`) and check for Pack-specific cache
   settings. Use only the Pack declarations, setup and public instructions.
   Start without copied environments, built extensions, or production
   weights. Record this cold result separately from a warm repeat using only
   what the isolated run populated. See the
   [environment contract](../../docs/environment-contract.md#时机缓存与证据).
3. **Run only on the test instance.** With the same `HOLOLAB_HOME`, use
   `hololab start --host 127.0.0.1 --port <unused-gateway-port> --node-config
   <node.yaml>`, with a gateway port distinct from the file server port.
   Propose a short sample that exercises the real input contract, such as a
   few seconds of representative video. Ask only when the sample choice or
   authorization is unclear. Copy an authorized sample into temporary storage
   or use a publicly usable sample, leaving the original untouched and
   without uploading it to an external service. Verify imports under the
   target interpreter, then connect the normal input node to the installed
   Pack and run the smallest workflow. Inspect the output and any promised
   preview or consumer. If a manual fix was needed, fold it into the declared
   dependencies or setup, or document it as a host prerequisite, then repeat
   from a clean environment.

Separate directories and ports isolate application state. The host driver,
hardware and GPU resources are still shared; schedule around other GPU work.
Use a container if stronger user-space isolation is needed. Record the
tested host and cold or warm result as validation evidence for the candidate.
Choose coverage according to the environments and workflows the release promises.

## Finalize and publish a community version

**Stage 6/7 · Publish.** Show the final scope, exact version, code hash and
validation evidence. Ask explicitly before publishing, for example, “May I
publish this exact version to the community registry?” If this identity is
already published, verify the listing and proceed with the existing release.
If final release content or identity differs from the tested candidate,
identify what changed and repeat the affected checks for the final identity.
Scope smoke coverage to promised interfaces. Use the recorded evidence to
show what passed and what remains pending.

After publication, verify the listing and the canvas node's exact Pack
version and expected code hash. For a concrete explanation, use an existing
shielded node on the user's canvas when available. The current
[badge logic](../../hololab/frontend/src/canvas/packCertification.ts)
shows a shield for built-ins shipped with HoloLab, and for other Packs only
when the catalog reports an identical code identity for the canonical name
and version at the official registry, with a fresh, non-yanked result. The
[node header](../../hololab/frontend/src/canvas/NodeCardHeader.tsx) displays
that badge. Explain the shield as code identity evidence. Security review,
successful execution, and cross-machine reproduction each require their own
evidence. Keep those results separate when reporting completion.

Review the intended release contents and publishing scope. Follow the
[registry publishing guide](../../website/content/zh/contributor/introduction.md#从发布一个包到别人用上它)
for archive rules and credentials, and the
[community publishing guide](../../website/content/en/contributor/introduction.md)
for the version handoff. Publish the authorized release under a precise
`scope/name@version` and give the recipient the registry root and expected
code hash. A published version is immutable. Publish corrections under a new
version and use yank to retract a formal release.

Use a local registry for temporary checks. If verifying the public path
requires a throwaway release, follow the [repository cleanup agreement](../../AGENTS.md#publishing-and-test-artifacts)
for its database record and COS object.

Report the three outcomes individually:

| Outcome | Evidence |
| --- | --- |
| Code finalized and published | Exact version, code hash, registry root, and publication result |
| Clean installation | Exact archive and installed identity, clean runtime, and dependency/import checks |
| Real workflow validation | Target node, sample, job and artifact references, and actual consumer result |

Scope workflow validation to the interfaces promised in the release. Record
the evidence and any pending checks against the version they apply to.

## Hand off the release for someone else to try

**Stage 7/7 · Handoff.** Give the user the exact community `scope/name@version`,
registry root, and expected code hash. Recommend that they or a recipient
install, set up, and try it on a compatible machine. Explain the minimum
input requirements and offer an approved small sample or instructions for
using their own data. Ask a single question only if needed, such as whether
a particular sample can be shared. Let the user decide whether to send the
package reference or data. Handoff is complete when the instructions,
validation evidence, and remaining checks are clear. Record any later
recipient run separately.
