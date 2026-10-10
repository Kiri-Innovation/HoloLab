import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

/** Only truncated title lines opt into the shared tooltip layer. */
export function NodeTitleTooltip({ name, version, device, style, children }: {
  name: string; version: string; device: string; style: CSSProperties; children: ReactNode;
}) {
  const anchor = useRef<HTMLDivElement>(null);
  const [truncated, setTruncated] = useState(false);
  useLayoutEffect(() => {
    const el = anchor.current!;
    let disposed = false;
    const measure = () => {
      if (!disposed) setTruncated(Array.from(el.children).some(child => child.scrollWidth > child.clientWidth));
    };
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    Array.from(el.children).forEach(child => observer.observe(child));
    measure();
    void document.fonts.ready.then(measure);
    return () => { disposed = true; observer.disconnect(); };
  }, [name, version, device]);
  return <div ref={anchor} style={style} data-hl-node-title=""
    tabIndex={truncated ? 0 : undefined}
    aria-label={truncated ? `${name} @${version} · ${device}` : undefined}
    data-tooltip={truncated ? `${name} @${version} · ${device}` : undefined}
    data-tooltip-title={truncated ? name : undefined}
    data-tooltip-version={truncated ? version : undefined}
    data-tooltip-device={truncated ? device : undefined}>
    {children}
  </div>;
}

export function NodeTitleTooltipCard({ name, version, device }: { name: string; version: string; device: string }) {
  return <div className="hl-title-tooltip-content">
    <strong className="hl-title-tooltip-name">{name}</strong>
    <span className="hl-title-tooltip-version">@{version}</span>
    <span className="hl-title-tooltip-device">设备 · {device}</span>
  </div>;
}
