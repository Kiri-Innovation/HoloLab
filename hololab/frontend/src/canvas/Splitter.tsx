import { useCallback, useRef, useState, type PointerEvent } from "react";
import "./Splitter.css";

type SplitterOrientation = "vertical" | "horizontal";

interface SplitterProps {
  /** A vertical splitter adjusts columns; a horizontal splitter adjusts rows. */
  orientation: SplitterOrientation;
  /** Receives the pointer movement in CSS pixels since the previous event. */
  onResize: (delta: number) => void;
  /** Restores the region's default size. */
  onReset: () => void;
  /** A concise accessible label for the separator. */
  label: string;
  /** Flip the sign when the controlled region is after the divider. */
  reverse?: boolean;
}

/**
 * A deliberately small, pointer-event based resize affordance. The visual
 * rule is one pixel wide, while the surrounding six pixels remain draggable.
 */
export function Splitter({
  orientation,
  onResize,
  onReset,
  label,
  reverse = false,
}: SplitterProps) {
  const startPosition = useRef(0);
  const [dragging, setDragging] = useState(false);

  const positionFor = useCallback(
    (event: Pick<PointerEvent<HTMLDivElement>, "clientX" | "clientY">) =>
      orientation === "vertical" ? event.clientX : event.clientY,
    [orientation],
  );

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    startPosition.current = positionFor(event);
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  };

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!dragging || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
    const currentPosition = positionFor(event);
    const delta = currentPosition - startPosition.current;
    startPosition.current = currentPosition;
    onResize(reverse ? -delta : delta);
  };

  const stopDragging = (event: PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDragging(false);
  };

  return (
    <div
      aria-label={label}
      aria-orientation={orientation === "vertical" ? "vertical" : "horizontal"}
      className={`hl-splitter hl-splitter--${orientation}${dragging ? " is-dragging" : ""}`}
      onDoubleClick={onReset}
      onPointerCancel={stopDragging}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={stopDragging}
      role="separator"
      title="Drag to resize. Double-click to reset."
    />
  );
}
