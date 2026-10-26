import './workflowEvidence.css';

/** Readable frozen values; no transformation of the underlying evidence. */
export function EvidenceValue({ value }: { value: unknown }) {
  if (value == null) return <span className="hl-evidence-muted">未记录</span>;
  if (Array.isArray(value)) return value.length
    ? <ul className="hl-evidence-list">{value.map((item, index) => <li key={index}><EvidenceValue value={item} /></li>)}</ul>
    : <span className="hl-evidence-muted">无</span>;
  if (typeof value === 'object') {
    const entries = Object.entries(value);
    return entries.length ? <dl className="hl-evidence-rows">{entries.map(([key, item]) =>
      <div key={key}><dt>{key}</dt><dd><EvidenceValue value={item} /></dd></div>)}</dl>
      : <span className="hl-evidence-muted">无</span>;
  }
  const text = typeof value === 'boolean' ? (value ? '是' : '否') : String(value);
  if (text.length > 48) return <details className="hl-evidence-long"><summary title="展开完整值">{text.slice(0, 24)}…{text.slice(-8)}</summary><div>{text}</div></details>;
  return <span>{text || '空值'}</span>;
}
