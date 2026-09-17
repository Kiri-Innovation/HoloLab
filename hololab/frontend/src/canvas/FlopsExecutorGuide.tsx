// Shared "set the Flops executor id first" guide callout.
//
// Both the preview drawer's ↗ button and the canvas node header's
// code-icon "Jump to source" button expand this callout when clicked
// on a compute node that has no ``flops_executor_id`` configured.
// A tooltip alone is easy to miss — the callout points the operator
// at the exact widget path they need to fill.

export interface FlopsExecutorGuideProps {
  /** Display name of the node the operator needs to configure. */
  nodeLabel: string;
  /** Anchor style. ``inline`` (default) hangs the callout below-right
   *  of the trigger button (small triggers inside a preview drawer);
   *  ``header`` widens it and hangs below the trigger to fit inside
   *  the tighter canvas node header row. */
  variant?: "inline" | "header";
}

export function FlopsExecutorGuide({
  nodeLabel,
  variant = "inline",
}: FlopsExecutorGuideProps) {
  const width = variant === "header" ? 280 : 260;
  const top = "calc(var(--control-h-sm) + 6px)";
  return (
    <div
      data-hl-open-cocoder-guide=""
      role="status"
      style={{
        position: "absolute",
        top,
        right: 0,
        width,
        padding: "8px 10px",
        background: "var(--surface)",
        color: "var(--text-body)",
        border: "1px solid var(--warn, #f0ad4e)",
        borderRadius: "var(--radius-sm)",
        boxShadow: "var(--shadow-2)",
        fontSize: "var(--fs-xs)",
        lineHeight: 1.45,
        zIndex: 30,
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 4 }}>
        ↗ Set the Flops executor id first
      </div>
      <div style={{ color: "var(--text-muted)" }}>
        {nodeLabel} has no <code>flops_executor_id</code> configured.
        Open the right-side <b>Compute nodes</b> panel → click the ⚙ on{" "}
        <b>{nodeLabel}</b> → fill the <b>Flops executor id</b> field →{" "}
        <b>Apply</b>.
      </div>
    </div>
  );
}
