// Tiny Markdown renderer for pack.docs — inline in the Inspector.
//
// Kept intentionally minimal: no HTML pass-through, no images, no tables.
// Covers what a pack author writing 2-6 lines of blurb needs — headings
// (# / ##), paragraphs, unordered/ordered lists, inline code, **bold**,
// fenced code blocks, and links. If we ever need more we should reach
// for a real library rather than growing this one; that's the trade the
// short size buys.

import { useMemo, type ReactNode } from "react";

// ---------------------------------------------------------------------------
// Inline: `code`, **bold**, [text](url). Order matters — code first so we
// don't try to bold-parse the inside of a code span.
// ---------------------------------------------------------------------------

function escapeUrl(url: string): string | null {
  // Allow only http(s) and mailto so we don't render javascript: links.
  const trimmed = url.trim();
  if (/^(https?:|mailto:)/i.test(trimmed)) return trimmed;
  return null;
}

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re =
    /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let idx = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const [tok] = m;
    const key = `${keyPrefix}-${idx++}`;
    if (tok.startsWith("`")) {
      out.push(
        <code
          key={key}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "0.92em",
            background: "var(--surface-alt)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius-sm)",
            padding: "0 4px",
          }}
        >
          {tok.slice(1, -1)}
        </code>,
      );
    } else if (tok.startsWith("**")) {
      out.push(
        <strong key={key} style={{ color: "var(--text)", fontWeight: 600 }}>
          {tok.slice(2, -2)}
        </strong>,
      );
    } else {
      const linkMatch = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(tok);
      if (linkMatch) {
        const [, label, rawUrl] = linkMatch;
        const url = escapeUrl(rawUrl);
        if (url) {
          out.push(
            <a
              key={key}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "var(--accent)", textDecoration: "underline" }}
            >
              {label}
            </a>,
          );
        } else {
          out.push(tok);
        }
      }
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// ---------------------------------------------------------------------------
// Block parser: pass over lines, group into paragraphs / lists / code /
// headings. Not a real Markdown AST — one flat pass, driven by the first
// character of each line.
// ---------------------------------------------------------------------------

type Block =
  | { kind: "h"; level: 1 | 2 | 3; text: string }
  | { kind: "p"; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "pre"; text: string };

function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    // Fenced code block: ``` … ```
    if (/^```/.test(line)) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) {
        buf.push(lines[i]);
        i++;
      }
      if (i < lines.length) i++; // consume closing fence
      blocks.push({ kind: "pre", text: buf.join("\n") });
      continue;
    }
    // Headings
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) {
      const level = h[1].length as 1 | 2 | 3;
      blocks.push({ kind: "h", level, text: h[2].trim() });
      i++;
      continue;
    }
    // Unordered list
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ""));
        i++;
      }
      blocks.push({ kind: "ul", items });
      continue;
    }
    // Ordered list
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i++;
      }
      blocks.push({ kind: "ol", items });
      continue;
    }
    // Paragraph — accumulate consecutive non-blank lines.
    const buf: string[] = [line];
    i++;
    while (i < lines.length && lines[i].trim() && !/^(#{1,3}\s|[-*+]\s|\d+\.\s|```)/.test(lines[i])) {
      buf.push(lines[i]);
      i++;
    }
    blocks.push({ kind: "p", text: buf.join(" ") });
  }
  return blocks;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function Markdown({ source }: { source: string }) {
  const blocks = useMemo(() => parseBlocks(source), [source]);
  const H_FONT_SIZE: Record<1 | 2 | 3, string> = {
    1: "var(--fs-lg)",
    2: "var(--fs-md)",
    3: "var(--fs-sm)",
  };
  return (
    <div
      className="hl-md"
      style={{
        fontSize: "var(--fs-sm)",
        color: "var(--text-body)",
        lineHeight: 1.55,
      }}
    >
      {blocks.map((b, i) => {
        if (b.kind === "h") {
          const Tag = (`h${b.level}` as unknown) as "h1" | "h2" | "h3";
          return (
            <Tag
              key={i}
              style={{
                fontSize: H_FONT_SIZE[b.level],
                color: "var(--text)",
                fontWeight: 600,
                margin: i === 0 ? "0 0 6px" : "10px 0 6px",
                lineHeight: 1.3,
              }}
            >
              {renderInline(b.text, `h-${i}`)}
            </Tag>
          );
        }
        if (b.kind === "p") {
          return (
            <p key={i} style={{ margin: i === 0 ? "0 0 8px" : "8px 0" }}>
              {renderInline(b.text, `p-${i}`)}
            </p>
          );
        }
        if (b.kind === "ul" || b.kind === "ol") {
          const ListTag = b.kind === "ul" ? "ul" : "ol";
          return (
            <ListTag
              key={i}
              style={{
                margin: "6px 0 8px",
                paddingLeft: 20,
              }}
            >
              {b.items.map((it, j) => (
                <li key={j} style={{ margin: "2px 0" }}>
                  {renderInline(it, `li-${i}-${j}`)}
                </li>
              ))}
            </ListTag>
          );
        }
        // pre / code block
        return (
          <pre
            key={i}
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "var(--fs-xs)",
              color: "var(--text-body)",
              background: "var(--surface-2)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "var(--radius-sm)",
              padding: "8px 10px",
              margin: "8px 0",
              overflow: "auto",
              lineHeight: 1.45,
            }}
          >
            {b.text}
          </pre>
        );
      })}
    </div>
  );
}
