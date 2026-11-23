import { useLayoutEffect, useRef, type MouseEventHandler, type ReactNode } from "react";
import { createPortal } from "react-dom";
import "./workflowEvidence.css";

export function WorkflowAddButton({ label, expanded, controls, onClick }: {
  label: string; expanded: boolean; controls: string; onClick: MouseEventHandler<HTMLButtonElement>;
}) {
  return <button type="button" className="hl-workflow-add-button" aria-label={label} title={label}
    aria-haspopup="dialog" aria-expanded={expanded} aria-controls={expanded ? controls : undefined}
    onClick={onClick}><span aria-hidden="true">+</span></button>;
}

export function WorkflowPopover({ id, name, anchor, closeLabel, onClose, children }: {
  id: string; name: string; anchor: HTMLButtonElement; closeLabel: string;
  onClose: () => void; children: (close: () => void) => ReactNode;
}) {
  const root = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const popup = root.current;
    if (!popup) return;
    const row = anchor.parentElement ?? anchor;
    const place = () => {
      const rect = row.getBoundingClientRect();
      if (!rect.width) { onClose(); return; }
      const { width, height } = popup.getBoundingClientRect();
      const trigger = anchor.getBoundingClientRect();
      const gap = 8;
      const right = rect.right + gap;
      const bottom = trigger.bottom + gap;
      const fitsRight = right + width + gap <= window.innerWidth;
      const top = !fitsRight && bottom + height + gap <= window.innerHeight ? bottom : trigger.top;
      popup.style.left = `${Math.max(gap, Math.min(right, window.innerWidth - width - gap))}px`;
      popup.style.top = `${Math.max(gap, Math.min(top, window.innerHeight - height - gap))}px`;
    };
    const outside = (event: PointerEvent) => {
      if (!anchor.contains(event.target as Node)) onClose();
    };
    place();
    if (!popup.contains(document.activeElement)) popup.focus({ preventScroll: true });
    const observer = new ResizeObserver(place);
    observer.observe(row);
    observer.observe(popup);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    document.addEventListener("pointerdown", outside);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      document.removeEventListener("pointerdown", outside);
    };
  }, [anchor, onClose]);

  function close() {
    onClose();
    anchor.focus({ preventScroll: true });
  }
  return createPortal(<section ref={root} id={id} tabIndex={-1} className="hl-variable-popover hl-evidence-panel"
    role="dialog" aria-labelledby={`${id}-title`}
    onPointerDown={event => event.stopPropagation()}
    onKeyDown={event => {
      // React events from the path browser's portals also bubble through here.
      if (event.key !== "Escape" || !event.currentTarget.contains(event.target as Node)) return;
      event.preventDefault();
      event.stopPropagation();
      close();
    }}>
    <header><strong id={`${id}-title`} title={name}>{name}</strong>
      <button type="button" aria-label={closeLabel} onClick={close}>×</button></header>
    {children(close)}
  </section>, document.body);
}

