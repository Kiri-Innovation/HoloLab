---
title: Artifacts and run history
group: Full Features Intro
order: 35
---
# Artifacts and run history

After running a pipeline with scripts, you may have several output directories and rely on logs and folder names to remember where they came from. HoloLab registers files or directories as artifacts, keeps execution details in task records, and connects those tasks to a workflow snapshot in a run record.

## Run a three-step pipeline

Take “extract frames → reconstruct → export.” Assume a first, complete execution with no reuse or array processing. If all three steps succeed and each registers one output directory, you get **1 Run, 3 Jobs, and 3 Artifacts**.

Instead of remembering that these directories came from the same execution, open a **Run** to review its workflow snapshot and related tasks. The code calls this record a **Snapshot**. It belongs to a Workflow and preserves the graph at that point, separately from the draft you continue editing.

<figure class="concept-diagram" aria-label="First execution: R1 links to frame-extraction Job J1, reconstruction Job J2, and export Job J3. They produce image directory A1, reconstruction directory A2, and export directory A3 respectively.">
<div class="concept-records">
<div class="concept-card concept-run"><strong>R1</strong><span>Run</span></div>
<div class="concept-record-link"><span>Links</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-card"><strong>J1 · Job</strong><span>Frames</span></div>
<div class="concept-record-link"><span>Yields</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-card"><strong>A1</strong><span>Images</span></div>
<div class="concept-record-link"><span>Links</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-card"><strong>J2 · Job</strong><span>Reconstruct</span></div>
<div class="concept-record-link"><span>Yields</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-card"><strong>A2</strong><span>Reconstruction</span></div>
<div class="concept-record-link"><span>Links</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-card"><strong>J3 · Job</strong><span>Export</span></div>
<div class="concept-record-link"><span>Yields</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-card"><strong>A3</strong><span>Export</span></div>
</div>
<figcaption>A1, A2, and A3 are three Artifacts. Arrows show record associations and outputs, not network access.</figcaption>
</figure>

## What does one step leave behind?

To answer “which parameters did reconstruction use?”, look at its **Job**, not just the node on the canvas. Each execution of an ordinary node creates a Job recording parameters, the algorithm version, input references, device, and status. Running it again creates a new Job.

Its output directory is an **Artifact**. An artifact reference, or **Handle**, records its ID, device, path, output port, and producing Job. Downstream tasks use input references to access it; the files remain on the execution device. A registered directory may contain many files, so the artifact count is neither a file count nor a fixed number per node.

<figure class="doc-screenshot">
<picture><source srcset="/images/docs/preview-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/preview-light.webp" alt="The actual point-cloud preview from colmap-triangulate in the STG pipeline. Frame names and artifact references are hidden; no source imagery is shown. Some product labels remain in Chinese." width="704" height="1030" loading="lazy" decoding="async" /></picture>
<figcaption>The actual point-cloud preview from colmap-triangulate in the STG pipeline. Frame names and artifact references are hidden; no source imagery is shown. Some product labels remain in Chinese.</figcaption>
</figure>

## Rerun only the last step

If you only change export parameters, you probably do not want to repeat frame extraction and reconstruction. Reusing their results and rerunning export can create R2, linked to the old J1 and J2 plus a new export Job J4. J4 produces A4; R1 still links to the original J1, J2, and J3.

<figure class="concept-diagram" aria-label="Three columns: Runs R1 and R2; Jobs J1 through J4; Artifacts A1 through A4. R1 links to J1, J2, J3; R2 links to J1, J2, J4. Each Job produces its matching Artifact. Both Runs share the same J1 and J2 and their artifacts A1 and A2, without copying them.">
<div class="concept-records concept-reuse">
<div class="concept-card"><strong>R1</strong><span>Original Run</span></div>
<div class="concept-card"><strong>R2</strong><span>New Run</span></div>
<div class="concept-reuse-lines"><svg viewBox="0 0 120 420" preserveAspectRatio="none" aria-hidden="true"><path d="M2 102 C42 102 70 41 118 41 m-6-5 6 5-6 5" /><path d="M2 102 C42 102 70 149 118 149 m-6-5 6 5-6 5" /><path d="M2 102 C42 102 70 264 118 264 m-6-5 6 5-6 5" /><path d="M2 318 C60 318 48 55 118 55 m-6-5 6 5-6 5" /><path d="M2 318 C60 318 48 163 118 163 m-6-5 6 5-6 5" /><path d="M2 318 C42 318 70 372 118 372 m-6-5 6 5-6 5" /></svg></div>
<div class="concept-reuse-jobs">
<div class="concept-card"><strong>J1 · Frames</strong><span>Shared Job</span></div>
<div class="concept-card"><strong>J2 · Reconstruct</strong><span>Shared Job</span></div>
<div class="concept-card"><strong>J3 · Export</strong><span>Original Job</span></div>
<div class="concept-card"><strong>J4 · Export</strong><span>New Job</span></div>
</div>
<div class="concept-reuse-outputs">
<div class="concept-record-link"><span>Yields</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-record-link"><span>Yields</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-record-link"><span>Yields</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-record-link"><span>Yields</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
</div>
<div class="concept-reuse-artifacts">
<div class="concept-card"><strong>A1</strong><span>Images</span></div>
<div class="concept-card"><strong>A2</strong><span>Reconstruction</span></div>
<div class="concept-card"><strong>A3</strong><span>Original export</span></div>
<div class="concept-card"><strong>A4</strong><span>New export</span></div>
</div>
</div>
<figcaption>Left arrows link Runs to Jobs: R1 links to J1, J2, J3; R2 links to J1, J2, J4. Crossings are not junctions. J1 and J2 each receive an arrow from both Runs; neither the records nor their artifacts A1 and A2 are copied. Right arrows show outputs: J1 → A1, J2 → A2, J3 → A3, J4 → A4. These arrows do not show network access.</figcaption>
</figure>

There are now **2 Runs, 4 distinct Jobs, and 4 Artifacts**, not two independent sets of three Jobs. Runs associate with reusable Jobs. Each artifact records its producing Job, which leads back to the Runs associated with it.

Nor does every click on Run create a new run record: executing steps not yet filled into the current record can continue that record. Array processing also introduces parent and shard Jobs. The “3 nodes, 3 Jobs” count applies only to the simple example above.

## Where to look

Start with your question; you do not need to memorize the IDs.

| Your question | Where to go | What you can inspect |
| --- | --- | --- |
| Which graph did I run, and which steps executed? | Open the workflow → the **Runs** tab in the right dock → select a record | The workflow snapshot and related tasks; historical snapshots are read-only |
| Which step failed, and why? | Workflow → bottom dock → **Jobs** → **Recent jobs** → **View logs / View failure logs** | Logs and failure details for that Job |
| What was produced, and does it still exist? | Workflow list → **Artifacts** → find the artifact | Source algorithm, output port, and file path; check whether the files exist |
| Can I see the result directly? | Canvas → a node’s artifact preview | Previews for supported types; no viewer does not mean no files were generated |

<figure class="doc-screenshot">
<picture><source srcset="/images/docs/runs-jobs-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/runs-jobs-light.webp" alt="Paired STG crops: the right dock’s Runs tab and the bottom dock’s Jobs tab. These panels occupy different regions of the interface; Recent jobs was empty at capture time. Timestamps and record IDs are hidden." width="1616" height="714" loading="lazy" decoding="async" /></picture>
<figcaption>Paired STG crops: the right dock’s Runs tab and the bottom dock’s Jobs tab. These panels occupy different regions of the interface; Recent jobs was empty at capture time. Timestamps and record IDs are hidden.</figcaption>
</figure>

**Current limitation:** filtering artifacts by Run still uses the producing Job’s original run record. It may omit artifacts from Jobs inherited from an earlier Run. Clear the Run filter and check the source algorithm and file path, or open the original Run. An empty filtered view does not prove the files are gone.

<figure class="doc-screenshot">
<picture><source srcset="/images/docs/artifacts-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/artifacts-light.webp" alt="Artifacts filtered to the STG workflow. Actual paths, timestamps, and the workflow name are hidden." width="3200" height="1140" loading="lazy" decoding="async" /></picture>
<figcaption>Artifacts filtered to the STG workflow. Actual paths, timestamps, and the workflow name are hidden.</figcaption>
</figure>

Previews depend on the artifact type and viewer. Progress depends on the Pack reporting parseable updates. For previews, file locations, and cleanup, see [View results](results.md).
