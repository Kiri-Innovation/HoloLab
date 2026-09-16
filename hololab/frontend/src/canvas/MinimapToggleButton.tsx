// Small square button pinned bottom-right of the canvas: click to
// toggle the MiniMap panel open/closed. The open state is styled with
// the accent border + tinted fill so it's obvious the button is the
// same control that will hide the just-opened minimap again.

export interface MinimapToggleButtonProps {
  open: boolean;
  onToggle: () => void;
}

export function MinimapToggleButton({ open, onToggle }: MinimapToggleButtonProps) {
  return (
    <button
      type="button"
      aria-pressed={open}
      aria-label={open ? "Hide minimap" : "Show minimap"}
      title={open ? "Hide minimap" : "Show minimap"}
      onClick={onToggle}
      style={{
        width: "var(--control-h-md)",
        height: "var(--control-h-md)",
        border: `1px solid ${open ? "var(--accent)" : "var(--border)"}`,
        borderRadius: "var(--radius-sm)",
        background: open ? "var(--accent-soft)" : "var(--surface)",
        color: open ? "var(--accent)" : "var(--text-subtle)",
        cursor: "pointer",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        boxShadow: "0 1px 2px rgba(0,0,0,0.08)",
        transition:
          "background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease)",
      }}
    >
      <svg
        width="13"
        height="13"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        aria-hidden
      >
        <rect x="1.5" y="1.5" width="13" height="13" rx="1.2" />
        <rect
          x={open ? 5.5 : 3.5}
          y={open ? 5.5 : 3.5}
          width={open ? 5 : 7}
          height={open ? 5 : 7}
          fill="currentColor"
          stroke="none"
        />
      </svg>
    </button>
  );
}
