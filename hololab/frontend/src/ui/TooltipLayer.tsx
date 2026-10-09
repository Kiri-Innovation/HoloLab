import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/** One unscaled, unclipped DOM tooltip for the existing data-tooltip convention. */
export function TooltipLayer() {
  const id = useId();
  const [tip, setTip] = useState<{ target: HTMLElement; text: string } | null>(null);
  const [position, setPosition] = useState({ left: 0, top: 0 });
  const bubble = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let active: HTMLElement | null = null;
    let originalDescription: string | null = null;
    const hide = () => {
      clearTimeout(timer);
      if (active) {
        if (originalDescription === null) active.removeAttribute("aria-describedby");
        else active.setAttribute("aria-describedby", originalDescription);
      }
      active = null;
      setTip(null);
    };
    const targetFor = (target: EventTarget | null) =>
      target instanceof Element ? target.closest<HTMLElement>("[data-tooltip]") : null;
    const show = (target: HTMLElement, delay: number) => {
      hide();
      const display = () => {
        if (!target.isConnected || !target.dataset.tooltip) return;
        active = target;
        originalDescription = target.getAttribute("aria-describedby");
        target.setAttribute("aria-describedby", [originalDescription, id].filter(Boolean).join(" "));
        setTip({ target, text: target.dataset.tooltip });
      };
      if (delay) timer = setTimeout(display, delay);
      else display();
    };
    const over = (event: PointerEvent) => {
      const target = targetFor(event.target);
      if (target && !target.contains(event.relatedTarget as Node | null)) show(target, 350);
    };
    const out = (event: PointerEvent) => {
      const target = targetFor(event.target);
      if (target && !target.contains(event.relatedTarget as Node | null)) hide();
    };
    const focus = (event: FocusEvent) => {
      const target = targetFor(event.target);
      if (target?.matches(":focus-visible")) show(target, 0);
    };
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") hide(); };
    document.addEventListener("pointerover", over);
    document.addEventListener("pointerout", out);
    document.addEventListener("focusin", focus);
    document.addEventListener("focusout", hide);
    document.addEventListener("pointerdown", hide, true);
    document.addEventListener("keydown", key);
    document.addEventListener("scroll", hide, true);
    document.addEventListener("wheel", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      hide();
      document.removeEventListener("pointerover", over);
      document.removeEventListener("pointerout", out);
      document.removeEventListener("focusin", focus);
      document.removeEventListener("focusout", hide);
      document.removeEventListener("pointerdown", hide, true);
      document.removeEventListener("keydown", key);
      document.removeEventListener("scroll", hide, true);
      document.removeEventListener("wheel", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, [id]);

  useLayoutEffect(() => {
    if (!tip || !bubble.current) return;
    const anchor = tip.target.getBoundingClientRect();
    const rect = bubble.current.getBoundingClientRect();
    const margin = 8;
    const above = anchor.top - rect.height - margin;
    setPosition({
      left: Math.max(margin, Math.min(anchor.left + (anchor.width - rect.width) / 2, window.innerWidth - rect.width - margin)),
      top: Math.max(margin, Math.min(above >= margin ? above : anchor.bottom + margin, window.innerHeight - rect.height - margin)),
    });
  }, [tip]);

  return tip ? createPortal(
    <div ref={bubble} id={id} role="tooltip" className="hl-tooltip" style={position}>{tip.text}</div>,
    document.body,
  ) : null;
}
