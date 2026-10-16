import { useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import "./enum-select.css";

/** Select-only combobox. Option indices never become stored parameter values. */
export function EnumSelect({ name, values, index, onChange }: {
  name: string; values: (string | number)[]; index: number;
  onChange: (value: string | number) => void;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(index);
  const [position, setPosition] = useState({ left: 0, top: 0, width: 0, maxHeight: 240 });
  const show = () => { setActive(index); setOpen(true); };
  const choose = (i: number) => {
    onChange(values[i]); setOpen(false); trigger.current?.focus();
  };

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const el = trigger.current;
      if (!el || !el.getClientRects().length) { setOpen(false); return; }
      const r = el.getBoundingClientRect();
      const below = Math.max(0, window.innerHeight - r.bottom - 8);
      const above = Math.max(0, r.top - 8);
      const desired = Math.min(240, values.length * 28 + 8);
      const up = below < desired && above > below;
      const height = Math.min(desired, up ? above : below);
      setPosition({ left: Math.max(4, Math.min(r.left, window.innerWidth - r.width - 4)),
        top: up ? r.top - height - 4 : r.bottom + 4, width: r.width, maxHeight: height });
    };
    const outside = (event: PointerEvent) => {
      if (!trigger.current?.contains(event.target as Node) && !list.current?.contains(event.target as Node)) setOpen(false);
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    document.addEventListener("pointerdown", outside);
    const observer = new ResizeObserver(place);
    if (trigger.current) observer.observe(trigger.current);
    return () => {
      observer.disconnect(); window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true); document.removeEventListener("pointerdown", outside);
    };
  }, [open, values.length]);

  useLayoutEffect(() => {
    if (open) list.current?.children[active]?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  return <>
    <button ref={trigger} type="button" className="hl-enum-select" role="combobox"
      aria-label={name} aria-expanded={open} aria-haspopup="listbox"
      aria-controls={open ? id : undefined} aria-activedescendant={open ? `${id}-${active}` : undefined}
      onClick={() => open ? setOpen(false) : show()}
      onBlur={() => setOpen(false)}
      onKeyDown={(event) => {
        if (["ArrowDown", "ArrowUp", "Enter", " ", "Escape", "Home", "End"].includes(event.key)) {
          event.preventDefault(); event.stopPropagation();
          if (event.key === "Escape") setOpen(false);
          else if (!open) show();
          else if (event.key === "Enter" || event.key === " ") choose(active);
          else if (event.key === "Home") setActive(0);
          else if (event.key === "End") setActive(values.length - 1);
          else setActive(i => Math.max(0, Math.min(values.length - 1, i + (event.key === "ArrowDown" ? 1 : -1))));
        } else if (event.key === "Tab") setOpen(false);
      }}>
      <span>{String(values[index])}</span><span aria-hidden="true">▾</span>
    </button>
    {open && createPortal(<div ref={list} id={id} role="listbox" aria-label={name}
      className="hl-enum-options" style={position} onMouseDown={event => event.preventDefault()}>
      {values.map((value, i) => <div key={i} id={`${id}-${i}`} role="option"
        aria-selected={i === index} data-active={i === active} title={String(value)}
        onMouseEnter={() => setActive(i)} onClick={() => choose(i)}>
        <span aria-hidden="true">{i === index ? "✓" : ""}</span><span>{String(value)}</span>
      </div>)}
    </div>, document.body)}
  </>;
}
