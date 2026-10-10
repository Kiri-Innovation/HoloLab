import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { NodeTitleTooltipCard } from "./NodeTitleTooltip";
import { tooltipGeometry } from "./tooltipGeometry";

const readContent = (target: HTMLElement) => ({ text: target.dataset.tooltip ?? "",
  title: target.dataset.tooltipTitle, version: target.dataset.tooltipVersion, device: target.dataset.tooltipDevice });

/** Body portal avoids clipping; CSS dimensions follow the anchor's canvas zoom. */
export function TooltipLayer() {
  const id = useId();
  const [tip, setTip] = useState<{ target: HTMLElement } & ReturnType<typeof readContent> | null>(null);
  const bubble = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const hide = () => {
      clearTimeout(timer);
      setTip(null);
    };
    const targetFor = (target: EventTarget | null) =>
      target instanceof Element ? target.closest<HTMLElement>("[data-tooltip]") : null;
    const show = (target: HTMLElement, delay: number) => {
      hide();
      const display = () => {
        if (!target.isConnected || !target.dataset.tooltip) return;
        setTip({ target, ...readContent(target) });
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

  // Restore accessibility metadata even when resize removes truncation while focused.
  const target = tip?.target;
  useLayoutEffect(() => {
    if (!target) return;
    const original = target.getAttribute("aria-describedby");
    target.setAttribute("aria-describedby", [original, id].filter(Boolean).join(" "));
    return () => {
      if (original === null) target.removeAttribute("aria-describedby");
      else target.setAttribute("aria-describedby", original);
    };
  }, [target, id]);

  useLayoutEffect(() => {
    if (!tip || !bubble.current) return;
    const element = bubble.current;
    const viewport = tip.target.closest<HTMLElement>(".react-flow__viewport");
    let frame = 0;
    const update = () => {
      if (!tip.target.isConnected) { setTip(null); return; }
      // Playback may end, or a toggle may change while focus/hover stays put.
      const content = readContent(tip.target);
      if (!content.text) { setTip(null); return; }
      if (content.text !== tip.text || content.title !== tip.title || content.version !== tip.version || content.device !== tip.device) { setTip({ target: tip.target, ...content }); return; }
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
    mutations.observe(tip.target, { attributes: true, attributeFilter: ["data-tooltip", "data-tooltip-title", "data-tooltip-version", "data-tooltip-device"] });
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
    <div ref={bubble} id={id} role="tooltip" className={`hl-tooltip${tip.title ? " hl-tooltip--title" : ""}`}>
      {tip.title ? <NodeTitleTooltipCard name={tip.title} version={tip.version ?? ""} device={tip.device ?? ""} /> : tip.text}
    </div>,
    document.body,
  ) : null;
}
