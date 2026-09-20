// Job log viewer — dark, monospace, one modal.
//
// Answers the "why did this go red?" question from the Recent Jobs panel.
// Opens on top of the whole app (position: fixed via the Modal shell) and
// shows the persisted stdout+stderr for one job, with subtle highlighting
// on lines that look like errors so a scroll-to-bottom lands the eye on
// the actual failure quickly.
//
// The viewer is deliberately read-only:
//   * fetch once when opened (tail=10000, big enough for every real run
//     we've seen — SpacetimeGaussians training tops out well under that);
//   * no live tail — job_update WS events already flip the panel dot in
//     real time, and users open this view *after* a run finishes to
//     investigate. Add polling later only if a real "watch a running job
//     without staring at Cocoder" ask comes up.
//   * copy-all writes plain "line\n" without stream tags so the paste
//     lands cleanly in a chat/issue.

import { useEffect, useMemo, useRef, useState } from "react";
import { getJob, tailJobLog } from "../api";
import type { JobDetail, LogLine } from "../wire";
import { stateColour } from "./AlgorithmNode";
import { CopyRefButton } from "./CopyRefButton";

// Requested tail size. Very generous — one SQLite range scan either way,
// and this frees us from a "load more" pager for anything under a big
// training run. If a real job exceeds this we surface the ``truncated``
// flag in the footer so the operator knows they're only seeing the tail.
const DEFAULT_TAIL = 10000;

export interface JobLogModalProps {
  // Non-null triggers the modal open; null closes it.
  jobId: string | null;
  // Optional priming context so the header renders synchronously before
  // the ``GET /api/jobs/{id}`` roundtrip fills in fail_message + exit
  // code. Fed from the RecentJobRow the click originated on.
  primer?: {
    algorithm_name?: string;
    algorithm_version?: string;
    state?: string;
    fail_reason?: string | null;
    elapsed?: string;
  } | null;
  onClose: () => void;
}

// Escape closes; click on the overlay closes; click on the surface does not.
// Matches the semantics of ui/Modal.tsx — we can't reuse that shell directly
// because it caps at 80vh and pads the body area, and this viewer needs the
// full-bleed log area to itself.
export function JobLogModal({ jobId, primer, onClose }: JobLogModalProps) {
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [lines, setLines] = useState<LogLine[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "err">("idle");
  const logsRef = useRef<HTMLDivElement | null>(null);

  // Fetch on open. Two parallel roundtrips — detail (fail_message) and log
  // tail — since neither depends on the other.
  useEffect(() => {
    if (!jobId) return;
    setDetail(null);
    setLines(null);
    setTruncated(false);
    setError(null);
    setLoading(true);

    let cancelled = false;
    Promise.all([
      getJob(jobId).catch((e) => {
        // Detail is nice-to-have; if it 404s (older row?) we still show the
        // log — surface only if both requests fail.
        console.warn("job detail fetch failed", e);
        return null as JobDetail | null;
      }),
      tailJobLog(jobId, { tail: DEFAULT_TAIL, stream: "both" }),
    ])
      .then(([d, tail]) => {
        if (cancelled) return;
        setDetail(d);
        setLines(tail.lines);
        setTruncated(tail.truncated);
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : String(e);
        setError(msg);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [jobId]);

  // Escape to close.
  useEffect(() => {
    if (!jobId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [jobId, onClose]);

  // Auto-scroll to the bottom once the lines land — matches the mental
  // model of "watching stdout scroll live". Only runs on the first flush;
  // if we ever add re-fetch, gate on "user hasn't scrolled up yet".
  useEffect(() => {
    if (!lines || !logsRef.current) return;
    logsRef.current.scrollTop = logsRef.current.scrollHeight;
  }, [lines]);

  const streamCounts = useMemo(() => {
    if (!lines) return { stdout: 0, stderr: 0 };
    let so = 0;
    let se = 0;
    for (const l of lines) {
      if (l.stream === "stdout") so++;
      else se++;
    }
    return { stdout: so, stderr: se };
  }, [lines]);

  if (!jobId) return null;

  // Header text: prefer live JobDetail (once it lands) over primer (what
  // the panel row had at click time). ``detail?.state`` supersedes primer
  // because the row can be stale-by-seconds while the modal is opening.
  const algoName = detail?.algorithm_name ?? primer?.algorithm_name ?? "job";
  const algoVersion = detail?.algorithm_version ?? primer?.algorithm_version;
  const state = detail?.state ?? primer?.state ?? "unknown";
  const failReason = detail?.fail_reason ?? primer?.fail_reason ?? null;
  const failMessage = detail?.fail_message ?? null;
  const failExit = detail?.fail_exit_code ?? null;

  const doCopyAll = async () => {
    if (!lines) return;
    const text = lines.map((l) => l.line).join("\n");
    let ok = false;
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        ok = true;
      } catch {
        ok = false;
      }
    }
    if (!ok) {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      try {
        ok = document.execCommand("copy");
      } catch {
        ok = false;
      }
      document.body.removeChild(ta);
    }
    setCopyState(ok ? "copied" : "err");
    window.setTimeout(() => setCopyState("idle"), 1500);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Log for ${algoName}`}
      data-hl-job-log-modal=""
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1200,
        background: "rgba(0, 0, 0, 0.65)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "var(--space-6)",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "100%",
          maxWidth: 1040,
          height: "min(85vh, 820px)",
          background: "var(--overlay)",
          color: "var(--text-body)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-2)",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {/* HEADER — dot · algo name · version · state · elapsed · copy · × */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-2)",
            padding: "var(--space-3) var(--space-4)",
            borderBottom: "1px solid var(--border-subtle)",
            background: "var(--surface-2)",
            minHeight: 44,
          }}
        >
          <span
            title={state}
            style={{
              width: 8,
              height: 8,
              borderRadius: "var(--radius-pill)",
              background: stateColour(state),
              flex: "0 0 auto",
            }}
          />
          <div
            style={{
              flex: 1,
              minWidth: 0,
              display: "flex",
              alignItems: "baseline",
              gap: "var(--space-2)",
              overflow: "hidden",
            }}
          >
            <strong
              style={{
                color: "var(--text)",
                fontSize: "var(--fs-md)",
                fontWeight: "var(--fw-semibold)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {algoName}
            </strong>
            {algoVersion && (
              <span
                style={{
                  color: "var(--text-muted)",
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--fs-xs)",
                }}
              >
                v{algoVersion}
              </span>
            )}
            <span
              style={{
                color: "var(--text-subtle)",
                fontSize: "var(--fs-xs)",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
              }}
            >
              · {state}
            </span>
            {primer?.elapsed && (
              <span
                style={{
                  color: "var(--text-subtle)",
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--fs-xs)",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                · {primer.elapsed}
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={doCopyAll}
            disabled={!lines || lines.length === 0}
            title={
              copyState === "copied"
                ? "copied whole log to clipboard"
                : copyState === "err"
                  ? "copy failed"
                  : "copy the whole log"
            }
            data-hl-job-log-copy=""
            style={{
              height: "var(--control-h-sm)",
              padding: "0 var(--space-2)",
              border: "1px solid var(--border-strong)",
              borderRadius: "var(--radius-sm)",
              background: "var(--surface)",
              color:
                copyState === "copied"
                  ? "var(--success)"
                  : copyState === "err"
                    ? "var(--error)"
                    : "var(--text-muted)",
              cursor: lines && lines.length > 0 ? "pointer" : "default",
              opacity: lines && lines.length > 0 ? 1 : 0.4,
              fontSize: "var(--fs-xs)",
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            {copyState === "copied" ? "✓ copied" : copyState === "err" ? "! failed" : "⧉ copy log"}
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            title="Close (Esc)"
            style={{
              width: 24,
              height: 24,
              border: "none",
              background: "transparent",
              color: "var(--text-subtle)",
              fontSize: 18,
              lineHeight: 1,
              cursor: "pointer",
              borderRadius: "var(--radius-sm)",
              flex: "0 0 auto",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "var(--surface-hover)";
              e.currentTarget.style.color = "var(--text)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.color = "var(--text-subtle)";
            }}
          >
            ×
          </button>
        </div>

        {/* FAIL BANNER — only when the job failed and there's something to say */}
        {(failReason || failMessage || failExit != null) && (
          <div
            data-hl-job-log-fail=""
            style={{
              padding: "var(--space-2) var(--space-4)",
              borderBottom: "1px solid var(--border-subtle)",
              background: "var(--error-soft)",
              color: "var(--text-body)",
              fontSize: "var(--fs-xs)",
              lineHeight: "var(--lh-normal)",
            }}
          >
            <div style={{ color: "var(--error)", fontWeight: "var(--fw-semibold)" }}>
              {failReason ?? "failed"}
              {failExit != null && (
                <span style={{ color: "var(--text-subtle)", fontWeight: 400 }}>
                  {" "}· exit {failExit}
                </span>
              )}
            </div>
            {failMessage && (
              <div
                style={{
                  marginTop: 4,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  fontFamily: "var(--font-mono)",
                  color: "var(--text-body)",
                }}
              >
                {failMessage}
              </div>
            )}
          </div>
        )}

        {/* LOG BODY — dark, monospace, scrollable */}
        <div
          ref={logsRef}
          data-hl-job-log-body=""
          style={{
            flex: 1,
            minHeight: 0,
            overflow: "auto",
            background: "var(--media-surface)",
            color: "var(--text-on-dark)",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--fs-xs)",
            lineHeight: 1.5,
            padding: "var(--space-2) 0",
          }}
        >
          {loading && (
            <div
              style={{
                padding: "var(--space-4)",
                color: "var(--inverse-muted)",
                fontFamily: "var(--font-sans)",
              }}
            >
              Loading log…
            </div>
          )}
          {error && !loading && (
            <div
              style={{
                padding: "var(--space-4)",
                color: "var(--error)",
                fontFamily: "var(--font-sans)",
                whiteSpace: "pre-wrap",
              }}
            >
              Failed to load log: {error}
            </div>
          )}
          {lines && lines.length === 0 && !loading && !error && (
            <div
              data-hl-job-log-empty=""
              style={{
                padding: "var(--space-4)",
                color: "var(--inverse-muted)",
                fontFamily: "var(--font-sans)",
              }}
            >
              {failMessage ? (
                <>
                  {/* Framework-level failure (e.g. input handle resolution)
                      never let the pack process start writing, so the log
                      is legitimately empty. Surface the fail_message here
                      too — the compact banner above is easy to miss, and
                      operators clicking "view log" want the reason inline
                      with the log area they're staring at. */}
                  <div style={{ marginBottom: "var(--space-2)" }}>
                    Job failed before writing any log output.
                  </div>
                  <div
                    style={{
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                      fontFamily: "var(--font-mono)",
                      color: "var(--text-on-dark)",
                    }}
                  >
                    {failMessage}
                  </div>
                </>
              ) : (
                <>
                  No log lines yet. The job may not have written anything to
                  stdout/stderr, or its output was rotated off the tail.
                </>
              )}
            </div>
          )}
          {lines && lines.length > 0 && <LogBody lines={lines} />}
        </div>

        {/* FOOTER — count + truncation notice + job ref */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-2)",
            padding: "var(--space-2) var(--space-4)",
            borderTop: "1px solid var(--border-subtle)",
            background: "var(--surface-2)",
            fontSize: "var(--fs-xs)",
            color: "var(--text-muted)",
            minHeight: 32,
          }}
        >
          <span style={{ fontVariantNumeric: "tabular-nums" }}>
            {lines ? `${lines.length} line${lines.length === 1 ? "" : "s"}` : "—"}
            {lines && (
              <span style={{ color: "var(--text-subtle)" }}>
                {" "}· stderr {streamCounts.stderr} · stdout {streamCounts.stdout}
              </span>
            )}
          </span>
          {truncated && (
            <span
              data-hl-job-log-truncated=""
              style={{
                color: "var(--warning)",
                fontStyle: "italic",
              }}
            >
              · log longer than {DEFAULT_TAIL} lines — showing tail only
            </span>
          )}
          <div style={{ flex: 1 }} />
          <CopyRefButton
            kind="job"
            id={jobId}
            comment={`${algoName} · ${state}`}
            size="xs"
          />
        </div>
      </div>
    </div>
  );
}

// -- log body ---------------------------------------------------------------
//
// One row per line: line-no, tiny stream tag, text. Error-looking lines get
// a red-tinted background so the eye lands on them fast; scrolled to the
// bottom, the "actual" failure line is usually the last such highlight.
// Kept as a separate component so React can key on lines[].length and skip
// re-diffing the whole (potentially long) list when only the container's
// scroll position moves.

const ERROR_PATTERN =
  /\b(error|traceback|exception|fatal|failed|refused|denied|cannot|no such file|unbound variable)\b|exit\s+[1-9]\d*|^\s*at\s+[\w$.]+ \(/i;

function looksLikeError(line: string): boolean {
  if (!line) return false;
  return ERROR_PATTERN.test(line);
}

function LogBody({ lines }: { lines: LogLine[] }) {
  return (
    <div data-hl-job-log-lines="" style={{ display: "block" }}>
      {lines.map((l, i) => {
        // Highlight purely on line content — stream is informational, not a
        // proxy for "error". tqdm, conda progress, and pip output all write
        // to stderr by convention but are not errors; gating on stream would
        // light up every progress bar as red and bury the real failure lines.
        const isErr = looksLikeError(l.line);
        return (
          <div
            key={i}
            data-hl-job-log-line=""
            data-hl-stream={l.stream}
            data-hl-error={isErr ? "1" : undefined}
            style={{
              display: "grid",
              gridTemplateColumns: "48px 32px 1fr",
              gap: 0,
              padding: "1px var(--space-3)",
              background: isErr
                ? "color-mix(in srgb, var(--error) 22%, transparent)"
                : "transparent",
              borderLeft: isErr
                ? "2px solid var(--error)"
                : "2px solid transparent",
            }}
          >
            <span
              style={{
                color: "var(--inverse-muted)",
                opacity: 0.55,
                fontVariantNumeric: "tabular-nums",
                textAlign: "right",
                paddingRight: 8,
                userSelect: "none",
              }}
            >
              {i + 1}
            </span>
            <span
              title={l.stream}
              style={{
                // Red only when the line content actually looks like an error;
                // a plain stderr line (tqdm progress, conda output, pip logs)
                // gets the same muted colour as stdout — stream alone is not
                // enough to call something an error.
                color: isErr
                  ? "color-mix(in srgb, var(--error) 80%, white)"
                  : "var(--inverse-muted)",
                fontSize: 10,
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                userSelect: "none",
                paddingRight: 6,
              }}
            >
              {l.stream === "stderr" ? "err" : "out"}
            </span>
            <span
              style={{
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                color: "var(--text-on-dark)",
              }}
            >
              {l.line || " "}
            </span>
          </div>
        );
      })}
    </div>
  );
}
