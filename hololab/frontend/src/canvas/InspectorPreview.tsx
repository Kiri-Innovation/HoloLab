import type { AlgorithmNodeData } from "./AlgorithmNode";
import { NodePreviews, useNodePreviewSelection } from "./NodePreviews";

/** Uses the canvas's resolved targets and policy; never resolves draft handles for a snapshot. */
export function InspectorPreview({ data }: { data: AlgorithmNodeData }) {
  const selection = useNodePreviewSelection(data, true);
  return <section className="hl-inspector-preview" aria-label="Preview">
    <h3>RECENT RUN</h3>
    <div className="hl-inspector-preview-body">
      <NodePreviews data={{ ...data, readOnly: true }} selection={selection} inspector />
    </div>
  </section>;
}
