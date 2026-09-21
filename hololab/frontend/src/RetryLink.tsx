// Small "重试" text button anchored next to an error message.
//
// Convention: whenever a preview / card ends up in a permanent-looking
// error state, its error copy must give the user one clear next step.
// The auto-retry loop in ``net.ts`` handles transient network glitches
// invisibly; this button is the escape hatch for anything that outlived
// that window (persistent server issue, business error, etc.).

interface RetryLinkProps {
  onRetry: () => void;
  label?: string;
}

export function RetryLink({ onRetry, label = "重试" }: RetryLinkProps) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onRetry();
      }}
      data-hl-retry=""
      style={{
        background: "transparent",
        border: "none",
        padding: "0 4px",
        marginLeft: 6,
        cursor: "pointer",
        color: "var(--accent)",
        font: "inherit",
        textDecoration: "underline",
      }}
    >
      {label}
    </button>
  );
}
