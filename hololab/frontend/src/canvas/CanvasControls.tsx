import { useLayoutEffect, useRef, type ComponentProps } from "react";
import { Controls, useStore } from "@xyflow/react";

/** Keep xyflow's actions/icons; adapt its built-in native titles to our tooltip. */
export function CanvasControls(props: ComponentProps<typeof Controls>) {
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
  return <div ref={host} style={{ display: "contents" }}><Controls {...props} /></div>;
}
