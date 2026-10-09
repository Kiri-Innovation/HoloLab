import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { tooltipGeometry } from "./tooltipGeometry";

/** Body portal avoids clipping; CSS dimensions follow the anchor's canvas zoom. */
export function TooltipLayer() {
  const id = useId();
  const [tip, setTip] = useState<{ target: HTMLElement; text: string } | null>(null);
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
    return () => {
      hide();
      document.removeEventListener("pointerover", over);
      document.removeEventListener("pointerout", out);
      document.removeEventListener("focusin", focus);
      document.removeEventListener("focusout", hide);
      document.removeEventListener("pointerdown", hide, true);
      document.removeEventListener("keydown", key);
    };
  }, [id]);

  useLayoutEffect(() => {
    if (!tip || !bubble.current) return;
    const element = bubble.current;
    const viewport = tip.target.closest<HTMLElement>(".react-flow__viewport");
    let frame = 0;
    const update = () => {
      if (!tip.target.isConnected) { setTip(null); return; }
      const matrix = viewport ? new DOMMatrixReadOnly(getComputedStyle(viewport).transform) : null;
      const zoom = matrix ? Math.hypot(matrix.a, matrix.b) : 1;
      // Resize actual CSS text metrics, not a rasterized transform layer.
      // Pure following is intentional: the tooltip belongs to its node.
      element.style.setProperty("--tooltip-scale", String(zoom));
      const rect = element.getBoundingClientRect();
      const position = tooltipGeometry(tip.target.getBoundingClientRect(), rect,
        { width: window.innerWidth, height: window.innerHeight }, zoom);
      element.style.left = `${position.left}px`;
      element.style.top = `${position.top}px`;
      element.style.setProperty("--tooltip-arrow-x", `${position.arrowX - element.clientLeft}px`);
      element.dataset.placement = position.placement;
    };
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(update);
    };
    update();
    // Follow live zoom/pan and node movement while hover/focus stays active.
    const mutations = new MutationObserver(schedule);
    if (viewport) mutations.observe(viewport, { attributes: true, attributeFilter: ["style"] });
    const node = tip.target.closest(".react-flow__node");
    if (node) mutations.observe(node, { attributes: true, attributeFilter: ["style"] });
    const sizes = new ResizeObserver(schedule);
    sizes.observe(tip.target);
    sizes.observe(element);
    window.addEventListener("resize", schedule);
    document.addEventListener("scroll", schedule, true);
    return () => {
      cancelAnimationFrame(frame);
      mutations.disconnect();
      sizes.disconnect();
      window.removeEventListener("resize", schedule);
      document.removeEventListener("scroll", schedule, true);
    };
  }, [tip]);

  return tip ? createPortal(
    <div ref={bubble} id={id} role="tooltip" className="hl-tooltip">{tip.text}</div>,
    document.body,
  ) : null;
}
