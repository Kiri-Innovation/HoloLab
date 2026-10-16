import { useEffect } from "react";
import { useReactFlow } from "@xyflow/react";

/** Frame the Inspector's node without stealing text-entry or modal shortcuts. */
export function FrameSelectedShortcut({ nodeId }: { nodeId: string | null }) {
  const { fitView, getNode } = useReactFlow();
  useEffect(() => {
    const frame = (event: KeyboardEvent) => {
      if (!nodeId || event.defaultPrevented || event.isComposing ||
          event.key.toLowerCase() !== "f" || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
      const target = event.composedPath()[0];
      if (target instanceof HTMLElement && (target.isContentEditable || target.closest(
        "input, textarea, select, [role=combobox], [role=listbox], [role=textbox], [role=menu], dialog, [role=dialog]",
      ))) return;
      if (document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return;
      if (!getNode(nodeId)) return;
      event.preventDefault();
      // React Flow uses measured bounds and the canvas's current min/max zoom.
      void fitView({ nodes: [{ id: nodeId }], padding: 0.2, duration: 250 });
    };
    window.addEventListener("keydown", frame);
    return () => window.removeEventListener("keydown", frame);
  }, [nodeId, fitView, getNode]);
  return null;
}
