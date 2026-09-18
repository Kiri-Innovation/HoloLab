// Inline error callout for Cobrowser open failures.
//
// Mirrors FlopsExecutorGuide's positioning + chrome — same "badge hangs
// below the trigger button" layout — but uses the error border colour and
// a generic title+detail structure so each caller can supply a specific,
// actionable message.

export interface SourceErrorCalloutProps {
  title: string;
  detail?: string;
  variant?: "inline" | "header";
}

export function SourceErrorCallout({
  title,
  detail,
  variant = "inline",
}: SourceErrorCalloutProps) {
  const width = variant === "header" ? 280 : 260;
  return (
    <div
      data-hl-source-error=""
      role="alert"
      style={{
        position: "absolute",
        top: "calc(var(--control-h-sm) + 6px)",
        right: 0,
        width,
        padding: "8px 10px",
        background: "var(--surface)",
        color: "var(--text-body)",
        border: "1px solid var(--error, #d55)",
        borderRadius: "var(--radius-sm)",
        boxShadow: "var(--shadow-2)",
        fontSize: "var(--fs-xs)",
        lineHeight: 1.45,
        zIndex: 30,
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: detail ? 4 : 0 }}>
        {title}
      </div>
      {detail && (
        <div style={{ color: "var(--text-muted)", wordBreak: "break-all" }}>
          {detail}
        </div>
      )}
    </div>
  );
}
