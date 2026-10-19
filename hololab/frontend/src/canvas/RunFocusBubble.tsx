import { useLayoutEffect, useRef } from "react";
import { createPortal } from "react-dom";
import "./run-focus-bubble.css";

/** A persistent, row-anchored notice outside the list's overflow clipping. */
export function RunFocusBubble({ target, onClose, onBack }: { target: HTMLElement; onClose: () => void; onBack: () => void }) {
  const bubble = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = bubble.current!;
    const scroller = target.parentElement!;
    const position = () => {
      if (!target.isConnected) { onClose(); return; }
      const row = target.getBoundingClientRect();
      const clip = scroller.getBoundingClientRect();
      // Keep the notice persistent, but don't float over unrelated rows when
      // its anchor is scrolled out. It returns when the row returns.
      const visible = row.bottom > clip.top && row.top < clip.bottom && row.width > 0;
      element.style.visibility = visible ? "visible" : "hidden";
      if (!visible) return;
      const { width, height } = element.getBoundingClientRect();
      const center = (Math.max(row.top, clip.top) + Math.min(row.bottom, clip.bottom)) / 2;
      let side = "right";
      let left = row.left - width - 10;
      let top = center - height / 2;
      if (left < 8) {
        if (row.right + width + 10 <= window.innerWidth - 8) {
          left = row.right + 10;
          side = "left";
        } else {
          // Narrow dock: above the row, leaving its action buttons usable.
          left = Math.max(8, Math.min(row.left + 12, window.innerWidth - width - 8));
          top = Math.max(row.top, clip.top) - height - 10;
          side = "bottom";
          if (top < 8) { top = Math.min(row.bottom, clip.bottom) + 10; side = "top"; }
        }
      }
      top = Math.max(8, Math.min(top, window.innerHeight - height - 8));
      element.dataset.arrowSide = side;
      element.style.left = `${left}px`;
      element.style.top = `${top}px`;
      element.style.setProperty("--run-arrow-y", `${Math.max(8, Math.min(center - top, height - 8))}px`);
    };
    position();
    const resize = new ResizeObserver(position);
    resize.observe(scroller);
    // Expanded notes above the anchor can move it without resizing it.
    for (const row of scroller.children) resize.observe(row);
    resize.observe(element);
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    return () => {
      resize.disconnect();
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
    };
  }, [target, onClose]);

  return createPortal(
    <div ref={bubble} className="hl-run-focus-bubble" data-hl-run-focus={target.dataset.hlRunId}>
      <span className="hl-run-focus-arrow" aria-hidden="true" />
      <button type="button" aria-label="返回 Inspector" title="返回 Inspector" onClick={onBack}>←</button>
      <span role="status"><code>{target.dataset.hlRunId?.slice(0, 8)}</code> · 来自 Inspector 预览</span>
      <button type="button" aria-label="关闭 run 定位提示" onClick={onClose}>×</button>
    </div>, document.body,
  );
}
