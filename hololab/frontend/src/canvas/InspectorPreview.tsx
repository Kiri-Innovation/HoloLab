import type { AlgorithmNodeData } from "./AlgorithmNode";
import { NodePreviews, useNodePreviewSelection } from "./NodePreviews";

/** Uses the canvas's resolved targets and policy; never resolves draft handles for a snapshot. */
export function InspectorPreview({ data, onShowRun }: { data: AlgorithmNodeData; onShowRun?: (snapshotId: string) => void }) {
  const selection = useNodePreviewSelection(data, true);
  // A live custom viewer can show newer frames than its historical output.
  // Without a frame attribution, hide the link rather than point at an older run.
  const target = selection.defaultNodeViewer
    ? selection.defaultNodeViewerFrames.length === 0 && selection.defaultNodeViewer.history_output
      ? data.previews?.[selection.defaultNodeViewer.history_output] : null
    : selection.currentPreview?.target;
  const runId = target?.snapshot_id;
  return <section className="hl-inspector-preview" aria-label="Preview">
    <div className="hl-inspector-preview-header">
      <h3>RECENT RUN</h3>
      {runId && onShowRun && <div className="hl-inspector-preview-run">
        <code title={runId}>{runId.slice(0, 8)}</code>
        <button type="button" aria-label={`Show run ${runId} in Runs`} title="Show in Runs" onClick={() => onShowRun(runId)}>
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M2 8h12M9 3l5 5-5 5" /></svg>
        </button>
      </div>}
    </div>
    <div className="hl-inspector-preview-body">
      <NodePreviews data={{ ...data, readOnly: true }} selection={selection} inspector />
    </div>
  </section>;
}
