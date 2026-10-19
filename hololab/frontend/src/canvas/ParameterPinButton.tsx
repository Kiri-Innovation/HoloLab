export function ParameterPinButton({ names, algorithmKey, pinned, onToggle }: {
  names: string[]; algorithmKey: string; pinned: string[]; onToggle: (names: string[]) => void;
}) {
  const active = names.every(name => pinned.includes(name));
  const label = `${active ? "Unpin" : "Pin"} ${names.join(" / ")} on all ${algorithmKey} nodes in this workflow`;
  return <button type="button" className="hl-parameter-pin" aria-pressed={active}
    aria-label={label} title={label} onClick={() => onToggle(names)}>
    <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"
      fill={active ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round">
      <path d="M5 2h6l-1 5 2 2H4l2-2z" /><path d="M8 9v5" fill="none" />
    </svg>
  </button>;
}
