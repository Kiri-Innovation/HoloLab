// Tiny Markdown renderer for pack docs.
//
// Intentionally minimal — we avoid react-markdown + a plugin farm because
// pack docs are meant to be a few sentences, not a wiki. Coverage:
//
//   * headings: `#`, `##`, `###`
//   * paragraphs (blank line separated)
//   * unordered lists (`- `, `* `, `+ `) and ordered lists (`1. `)
//   * fenced code blocks (```)
//   * inline: **bold**, `inline code`, [text](url)
//
// No raw HTML, no images. The renderer escapes text into React children,
// so any `<script>` in the source is inert.

import { useMemo, type ReactNode } from "react";

type Block =
  | { kind: "h"; level: 1 | 2 | 3; text: string }
  | { kind: "p"; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "code"; text: string };

function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const out: Block[] = [];
  let i = 0;
  const flushPara = (buf: string[]) => {
    if (buf.length) out.push({ kind: "p", text: buf.join(" ").trim() });
  };
  while (i < lines.length) {
    const line = lines[i];
    // Fenced code
    if (/^```/.test(line)) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      if (i < lines.length) i++; // eat closing fence
      out.push({ kind: "code", text: buf.join("\n") });
      continue;
    }
    // Heading
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) {
      out.push({ kind: "h", level: h[1].length as 1 | 2 | 3, text: h[2].trim() });
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
      out.push({ kind: "ul", items });
      continue;
    }
    // Ordered list
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i++;
      }
      out.push({ kind: "ol", items });
      continue;
    }
    // Paragraph (accumulate until blank line / block start)
    const buf: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^(#{1,3}\s+|```|\s*[-*+]\s+|\s*\d+\.\s+)/.test(lines[i])
    ) {
      buf.push(lines[i].trim());
      i++;
    }
    flushPara(buf);
    // Skip blank line
    if (i < lines.length && lines[i].trim() === "") i++;
  }
  return out;
}

// Inline: **bold**, `code`, [text](url). Left-to-right greedy split.
function renderInline(text: string, keyBase: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    const key = `${keyBase}-${k++}`;
    if (tok.startsWith("**")) {
      nodes.push(<strong key={key} style={{ fontWeight: 600 }}>{tok.slice(2, -2)}</strong>);
    } else if (tok.startsWith("`")) {
      nodes.push(
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
    } else {
      const linkMatch = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(tok);
      if (linkMatch) {
        nodes.push(
          <a
            key={key}
            href={linkMatch[2]}
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "var(--accent)", textDecoration: "underline" }}
          >
            {linkMatch[1]}
          </a>,
        );
      }
    }
    last = m.index + tok.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

const H_STYLE: Record<1 | 2 | 3, React.CSSProperties> = {
  1: { fontSize: "var(--fs-lg)", fontWeight: 600, margin: "8px 0 4px", color: "var(--text)" },
  2: { fontSize: "var(--fs-md)", fontWeight: 600, margin: "6px 0 3px", color: "var(--text)" },
  3: { fontSize: "var(--fs-sm)", fontWeight: 600, margin: "4px 0 2px", color: "var(--text)" },
};

export function MarkdownView({ source }: { source: string }) {
  const blocks = useMemo(() => parseBlocks(source), [source]);
  return (
    <div
      style={{
        fontSize: "var(--fs-sm)",
        color: "var(--text-body)",
        lineHeight: 1.5,
      }}
    >
      {blocks.map((b, i) => {
        const key = `b-${i}`;
        if (b.kind === "h") {
          const Tag = (`h${b.level}` as unknown) as keyof JSX.IntrinsicElements;
          return (
            <Tag key={key} style={H_STYLE[b.level]}>
              {renderInline(b.text, key)}
            </Tag>
          );
        }
        if (b.kind === "p") {
          return (
            <p key={key} style={{ margin: "4px 0" }}>
              {renderInline(b.text, key)}
            </p>
          );
        }
        if (b.kind === "ul") {
          return (
            <ul key={key} style={{ margin: "4px 0", paddingLeft: 18 }}>
              {b.items.map((it, j) => (
                <li key={`${key}-${j}`} style={{ margin: "2px 0" }}>
                  {renderInline(it, `${key}-${j}`)}
                </li>
              ))}
            </ul>
          );
        }
        if (b.kind === "ol") {
          return (
            <ol key={key} style={{ margin: "4px 0", paddingLeft: 20 }}>
              {b.items.map((it, j) => (
                <li key={`${key}-${j}`} style={{ margin: "2px 0" }}>
                  {renderInline(it, `${key}-${j}`)}
                </li>
              ))}
            </ol>
          );
        }
        // code
        return (
          <pre
            key={key}
            style={{
              margin: "6px 0",
              padding: "8px 10px",
              background: "var(--surface-alt)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "var(--radius-sm)",
              fontFamily: "var(--font-mono)",
              fontSize: "var(--fs-xs)",
              lineHeight: 1.5,
              overflow: "auto",
              whiteSpace: "pre",
            }}
          >
            {b.text}
          </pre>
        );
      })}
    </div>
  );
}
