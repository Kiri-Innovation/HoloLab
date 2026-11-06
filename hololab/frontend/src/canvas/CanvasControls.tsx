import { useLayoutEffect, useRef, type ComponentProps } from "react";
import { ControlButton, Controls, useStore } from "@xyflow/react";

/** Keep xyflow's actions/icons; adapt its built-in native titles to our tooltip. */
export function CanvasControls({ trackpadNavigation, onToggleTrackpadNavigation, ...props }: ComponentProps<typeof Controls> & {
  trackpadNavigation?: boolean;
  onToggleTrackpadNavigation?: () => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const interactive = useStore(s => s.nodesDraggable || s.nodesConnectable || s.elementsSelectable);
  useLayoutEffect(() => {
    const labels: Record<string, string> = {
      zoomin: "放大画布",
      zoomout: "缩小画布",
      fitview: "适应视图",
      interactive: interactive ? "锁定节点交互" : "解锁节点交互",
    };
    for (const [name, label] of Object.entries(labels)) {
      const button = host.current?.querySelector<HTMLButtonElement>(`.react-flow__controls-${name}`);
      if (!button) continue;
      button.removeAttribute("title");
      button.dataset.tooltip = label;
      button.setAttribute("aria-label", label);
    }
  }, [interactive, props.showZoom, props.showFitView, props.showInteractive]);
  return <div ref={host} style={{ display: "contents" }}><Controls {...props}>
    {props.children}
    {onToggleTrackpadNavigation && <ControlButton
      className="hl-trackpad-navigation"
      aria-label="触控板导航"
      aria-pressed={trackpadNavigation ?? false}
      data-tooltip={trackpadNavigation
        ? "触控板导航：已开启，双指滑动平移、捏合缩放；点击切回鼠标模式"
        : "触控板导航：已关闭；点击开启双指滑动平移、捏合缩放"}
      onClick={onToggleTrackpadNavigation}
    >
      <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
        <g fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="3" />
          <path d="M9 15V8m6 7V8M7 10l2-2 2 2m2 0 2-2 2 2M4 17h16" />
        </g>
      </svg>
    </ControlButton>}
  </Controls></div>;
}
