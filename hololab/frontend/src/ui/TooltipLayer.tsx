import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { NodeStatusTooltipCard } from "./NodeStatusTooltip";
import { NodeTitleTooltipCard } from "./NodeTitleTooltip";
import { tooltipGeometry } from "./tooltipGeometry";

const readContent = (target: HTMLElement) => ({ typeName: target.dataset.tooltipType, declared: target.dataset.tooltipDeclared, actual: target.dataset.tooltipActual, text: target.dataset.tooltip ?? "",
  status: target.dataset.tooltipStatus, reason: target.dataset.tooltipReason, action: target.dataset.tooltipAction,
  title: target.dataset.tooltipTitle, version: target.dataset.tooltipVersion, device: target.dataset.tooltipDevice });

/** Body portal avoids clipping; CSS dimensions follow the anchor's canvas zoom. */
export function TooltipLayer() {
  const id = useId();
  const [tip, setTip] = useState<{ target: HTMLElement } & ReturnType<typeof readContent> | null>(null);
  const bubble = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let pointerDown = false;
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
        if (pointerDown || !target.isConnected || !target.dataset.tooltip) return;
        setTip({ target, ...readContent(target) });
      };
      if (delay) timer = setTimeout(display, delay);
      else display();
    };
    const over = (event: PointerEvent) => {
      if (event.buttons) return;
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
    const down = () => { pointerDown = true; hide(); };
    const up = () => { pointerDown = false; };
    document.addEventListener("pointerup", up, true);
    window.addEventListener("blur", up);
    document.addEventListener("pointercancel", up, true);
    document.addEventListener("pointerover", over);
    document.addEventListener("pointerout", out);
    document.addEventListener("focusin", focus);
    document.addEventListener("focusout", hide);
    document.addEventListener("pointerdown", down, true);
    document.addEventListener("keydown", key);
    return () => {
      hide();
      document.removeEventListener("pointerup", up, true);
      document.removeEventListener("pointercancel", up, true);
      window.removeEventListener("blur", up);
      document.removeEventListener("pointerover", over);
      document.removeEventListener("pointerout", out);
      document.removeEventListener("focusin", focus);
      document.removeEventListener("focusout", hide);
      document.removeEventListener("pointerdown", down, true);
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
      if (content.typeName !== tip.typeName || content.declared !== tip.declared || content.actual !== tip.actual || content.text !== tip.text || content.title !== tip.title || content.version !== tip.version || content.device !== tip.device || content.status !== tip.status || content.reason !== tip.reason || content.action !== tip.action) { setTip({ target: tip.target, ...content }); return; }
      const matrix = viewport ? new DOMMatrixReadOnly(getComputedStyle(viewport).transform) : null;
      const zoom = matrix ? Math.hypot(matrix.a, matrix.b) : 1;
      // Resize actual CSS text metrics, not a rasterized transform layer.
      // Pure following is intentional: the tooltip belongs to its node.
      element.style.setProperty("--tooltip-scale", String(zoom));
      const rect = element.getBoundingClientRect();
      // Title cards leave a full space-2 between the tail tip and the title.
      const style = getComputedStyle(element);
      const titleGap = parseFloat(style.getPropertyValue("--space-2")) + 5;
      // Computed padding/border already include zoom; do not scale this inset again.
      const textInset = parseFloat(style.paddingLeft) + parseFloat(style.borderLeftWidth);
      const position = tooltipGeometry(tip.target.getBoundingClientRect(), rect,
        { width: window.innerWidth, height: window.innerHeight }, zoom,
        tip.title ? { align: "left", gap: titleGap, textInset } : undefined);
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
    mutations.observe(tip.target, { attributes: true, attributeFilter: ["data-tooltip-type", "data-tooltip-declared", "data-tooltip-actual", "data-tooltip", "data-tooltip-title", "data-tooltip-version", "data-tooltip-device", "data-tooltip-status", "data-tooltip-reason", "data-tooltip-action"] });
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
    <div ref={bubble} id={id} role="tooltip" className={`hl-tooltip${!tip.typeName && !tip.title && !tip.status && tip.text.includes("\n") ? " hl-tooltip--multiline" : ""}${tip.typeName ? " hl-tooltip--type" : tip.title ? " hl-tooltip--title" : tip.status ? " hl-tooltip--status" : ""}`}>
      {tip.typeName ? <div className="hl-type-tooltip-content">
        <strong>{tip.typeName}</strong>
        <span>{tip.declared}</span>
        <span>{tip.actual}</span>
      </div> : tip.status ? <NodeStatusTooltipCard conclusion={tip.status} reason={tip.reason ?? ""} action={tip.action ?? ""} /> : tip.title ? <NodeTitleTooltipCard name={tip.title} version={tip.version ?? ""} device={tip.device ?? ""} /> : tip.text}
    </div>,
    document.body,
  ) : null;
}
