---
title: Preserve execution meaning with variables and snapshots
group: Collaboration and reproducibility
order: 60
---
# Preserve execution meaning with variables and snapshots

> **Under development**: The capabilities described in this section are under development. This notice is removed when they are complete.

Two people can run the same reconstruction pipeline with different data directories and devices while sharing topology and parameter definitions. Variables separate local choices from the recipe. Snapshots establish what one particular execution actually used.

## Keep inline values or bind parameters to variables

A workflow variable's **name, type, description, and default** travel with the recipe. Its local value belongs to the binding. Sharing a default does not turn the author's local value into the recipient's binding.

Variables support scalar types only: `path`, `int`, `float`, `bool`, `string`, and `enum`. They do not bind arrayed or port types, avoiding a conflict with fan-out array expansion semantics.

A parameter has two input states:

| State | Editing and resolution |
| --- | --- |
| Inline value | Enter the value at the parameter itself, retaining direct, in-place editing |
| Workflow-wide variable binding | Select a variable from a type-filtered dropdown; only variables of a matching type are available |

If several parameters bind to one iteration-count variable, changing it changes every bound parameter. **A variable value change must propagate staleness to affected downstream outputs.** Otherwise, green downstream states incorrectly suggest that old outputs reflect the new value.

Snapshots freeze **resolved parameter values**, regardless of whether they came from inline input or a variable. If a run resolves a value to `12000` and the variable later changes to `10000`, the old snapshot still says that the run used `12000`. Current variable values cannot stand in for execution history.

## Share capability requirements, choose devices locally

The package manifest declares requirements such as a GPU, 24GB of GPU memory, or a particular runtime environment. These requirements are distributable. Selecting a specific machine is a local binding; machine UUIDs such as `assigned_node_id` do not travel with the recipe.

When another person opens your workflow, every node starts **unassigned**. This is the correct starting state, not missing data. The interface clearly marks unassigned nodes and indicates which machines meet their requirements. Path variables likewise need bindings in the recipient's environment; an author's absolute path does not establish that the data exists there.

## Capture the basis for reconstructing a run

A snapshot consists of a **recipe reference + local bindings + dependency inventory + optional environment fingerprint + optional input data digest**.

| Component | Question answered |
| --- | --- |
| Recipe reference | Which workflow revision was used? |
| Local bindings | Which machines and variable values were used, and what were the resolved parameter values? |
| Dependency inventory | What were each package's name, version, and hash? |
| Environment fingerprint, optional | What was the identity of the execution environment? |
| Input data digest, optional | What was the identity of the input data? |

A snapshot is the basis for reconstructing a run. Sufficient evidence lets another person reconstruct the pipeline; missing evidence leaves a reminder tied to one machine. The two optional components remain optional, and their presence does not guarantee identical results. They help compare the conditions of execution, while random seeds, hardware and library versions, and internal nondeterminism still affect results.

The original machine choices and variable values preserve historical facts. They do not automatically become valid bindings on another machine. A recipient uses the shared recipe and packages, refers to the record, and supplies bindings appropriate to their environment. Execution records themselves remain local state.

The current implementation captures only part of the recipe and part of the bindings. It lacks hashes, environment identity, and input data identity. These gaps explain why saving a graph alone cannot describe the full conditions of a past execution.

## Connect a release to existing snapshots

Suppose a run uses `name@0.1.1+` and records hash `H`. The algorithm is then published as `0.1.1`, also with hash `H`.

**Completed snapshots are not rewritten.** The original identity `0.1.1+ @H` remains, with a forward reference added as `published_as: 0.1.1`. The system can then determine that the snapshot's dependency is satisfiable through `0.1.1`. It does not claim the run originally used that release. A snapshot records what happened; replacing its original identity would change the evidence.

Publication scans historical snapshots for the same package's `name@X+` references and annotates those with matching hashes in a batch. Similar version names are insufficient: the hashes must match.

**An in-flight run is the exception and can be repointed directly** to the corresponding release. It is still happening rather than completed history. This exception does not weaken the immutability rule for completed snapshots.

## Collaborate through git without sharing personal layout

Because a workflow is a file, collaboration uses git's existing branches, merges, conflict resolution, and history. Contributors share the recipe, not machine bindings or local variable values. There is no separate workflow collaboration mechanism to invent.

Canvas `position` is a known source of collaboration noise: every drag can produce a diff. Personal view state is treated separately from recipe semantics. Layout uses either a local overlay or a separate section of the same file with local preference during conflict resolution. Both approaches keep personal arrangement from overriding shared pipeline intent. `preview_open` is also personal view state.
