import { type CSSProperties, type ReactNode } from "react";

/** Title metadata is available on hover and focus, regardless of truncation. */
export function NodeTitleTooltip({ name, version, device, style, children }: {
  name: string; version: string; device: string; style: CSSProperties; children: ReactNode;
}) {
  return <div style={style} data-hl-node-title=""
    tabIndex={0}
    aria-label={`${name} @${version} · ${device}`}
    data-tooltip={`${name} @${version} · ${device}`}
    data-tooltip-title={name}
    data-tooltip-version={version}
    data-tooltip-device={device}>
    {children}
  </div>;
}

export function NodeTitleTooltipCard({ name, version, device }: { name: string; version: string; device: string }) {
  return <div className="hl-title-tooltip-content">
    <div className="hl-title-tooltip-heading">
      <strong className="hl-title-tooltip-name">{name}</strong>{" "}
      <span className="hl-title-tooltip-version">@{version}</span>
    </div>
    <span className="hl-title-tooltip-device">{device}</span>
  </div>;
}
