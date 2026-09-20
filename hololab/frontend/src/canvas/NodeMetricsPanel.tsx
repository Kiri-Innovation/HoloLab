// Bottom inspector's empty-state view: one card per compute node
// showing live CPU / memory / GPU-utilisation / VRAM plus a small
// sparkline for each metric. Sits behind an unselected canvas so the
// operator can eyeball cluster load while planning the next run.
//
// Data flow: App.tsx owns ``metricsByNode`` (seeded from the gateway's
// /api/nodes/metrics/history and topped up via WS ``node_metrics``
// frames). This component is a pure render of that map crossed with
// the currently-online ``ComputeNode`` list — nodes that appeared in
// the history buffer but aren't currently connected surface as
// offline cards with their last known reading.
//
// Charting is deliberately hand-rolled SVG polylines: no chart lib
// dependency and easy to reason about at 30-plus samples wide. All
// colours resolve to CSS variables so both themes work without a
// second stylesheet.

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import type { ComputeNode, GpuMetricSample, NodeMetrics } from "../wire";

// Cap on retained samples per node. Mirrors ``DEFAULT_BUFFER_SIZE`` in
// hololab/gateway/metrics.py so the client and server agree on the
// x-axis span (~1 h at the node's 3 s cadence). Exported so App.tsx
// can enforce the same bound when appending WS frames.
export const METRICS_HISTORY_LIMIT = 1200;

// Sparkline geometry — tuned to fit the card at typical widths without
// squinting. Height stays fixed; width flexes with the flexbox parent.
const CHART_HEIGHT = 34;
const CHART_MIN_WIDTH = 120;

export interface NodeMetricsPanelProps {
  nodes: ComputeNode[];
  metricsByNode: Record<string, NodeMetrics[]>;
}

interface NodeCardModel {
  node_id: string;
  node_name: string;
  online: boolean;
  history: NodeMetrics[];
  latest: NodeMetrics | null;
  gpuName: string | null;
  totalVramGb: number | null;
}

export function NodeMetricsPanel({ nodes, metricsByNode }: NodeMetricsPanelProps) {
  const cards = useMemo<NodeCardModel[]>(() => {
    const online = new Map<string, ComputeNode>(nodes.map((n) => [n.node_id, n]));
    const historyIds = Object.keys(metricsByNode);
    const seen = new Set<string>();
    const rows: NodeCardModel[] = [];
    // Online nodes first so the operator sees the live cluster at the
    // top; offline-with-history follows so a recently-disconnected box
    // stays visible but visually dimmed.
    for (const cn of nodes) {
      seen.add(cn.node_id);
      const hist = metricsByNode[cn.node_id] ?? [];
      rows.push({
        node_id: cn.node_id,
        node_name: cn.node_name,
        online: true,
        history: hist,
        latest: hist.length > 0 ? hist[hist.length - 1] : null,
        gpuName: cn.gpu.count > 0 ? cn.gpu.name ?? null : null,
        totalVramGb: cn.gpu.total_vram_gb ?? null,
      });
    }
    for (const nid of historyIds) {
      if (seen.has(nid)) continue;
      const hist = metricsByNode[nid] ?? [];
      const latest = hist.length > 0 ? hist[hist.length - 1] : null;
      const firstGpu = latest?.gpus?.[0];
      rows.push({
        node_id: nid,
        node_name: online.get(nid)?.node_name ?? nid.slice(0, 8),
        online: false,
        history: hist,
        latest,
        gpuName: firstGpu?.name ?? null,
        totalVramGb:
          firstGpu?.mem_total_mb != null ? firstGpu.mem_total_mb / 1024 : null,
      });
    }
    return rows;
  }, [nodes, metricsByNode]);

  return (
    <div
      className="hl-node-metrics"
      style={{
        padding: "14px 18px 20px",
        overflow: "auto",
        height: "100%",
        fontSize: "var(--fs-sm)",
        color: "var(--text-body)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          marginBottom: 10,
        }}
      >
        <h3
          style={{
            fontSize: 10,
            fontWeight: 600,
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            color: "var(--text-subtle)",
            margin: 0,
          }}
        >
          Compute node pulse
        </h3>
        <span style={{ fontSize: "var(--fs-xs)", color: "var(--text-muted)" }}>
          Nothing selected · live CPU / memory / GPU
        </span>
      </div>

      {cards.length === 0 ? (
        <div style={{ color: "var(--text-muted)", fontSize: "var(--fs-xs)" }}>
          No compute nodes connected yet.
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
            gap: 12,
          }}
        >
          {cards.map((c) => (
            <NodeCard key={c.node_id} card={c} />
          ))}
        </div>
      )}
    </div>
  );
}

// -- one card ---------------------------------------------------------------

const CARD_STYLE: CSSProperties = {
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-md)",
  padding: "10px 12px 12px",
  background: "var(--surface-2)",
};

function NodeCard({ card }: { card: NodeCardModel }) {
  const { history, latest, online } = card;

  // For multi-GPU rigs we sparkline the first GPU (index 0) — most
  // HoloLab boxes are single-GPU and multi-GPU jobs are typically
  // driven by a scheduler that keeps one card busy at a time. If we
  // ever run 2× A100 rigs regularly this card can grow one row per
  // GPU without changing the wire format.
  const gpuSeries = useMemo(() => extractGpuSeries(history, 0), [history]);
  const gpuLatest: GpuMetricSample | null = latest?.gpus?.[0] ?? null;
  const memPercent = memPercentOf(latest);
  const vramPercent = vramPercentOf(gpuLatest);

  const cpuSeries = useMemo(
    () => history.map((s) => s.cpu_percent),
    [history],
  );
  const memSeries = useMemo(
    () => history.map((s) => memPercentOf(s)),
    [history],
  );

  return (
    <div
      style={{
        ...CARD_STYLE,
        opacity: online ? 1 : 0.62,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 8,
        }}
      >
        <span
          aria-hidden
          style={{
            width: 8,
            height: 8,
            borderRadius: "var(--radius-pill)",
            background: online ? "var(--success)" : "var(--text-muted)",
            flexShrink: 0,
          }}
        />
        <span
          style={{
            fontWeight: 600,
            color: "var(--text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={card.node_id}
        >
          {card.node_name}
        </span>
        {!online && (
          <span
            style={{
              fontSize: "var(--fs-micro)",
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              color: "var(--text-muted)",
              padding: "1px 6px",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
            }}
          >
            Offline
          </span>
        )}
        <span
          style={{
            marginLeft: "auto",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--fs-micro)",
            color: "var(--text-subtle)",
          }}
        >
          {card.node_id.slice(0, 8)}
        </span>
      </div>

      {(card.gpuName || card.totalVramGb != null) && (
        <div
          style={{
            fontSize: "var(--fs-micro)",
            color: "var(--text-muted)",
            marginBottom: 8,
            display: "flex",
            gap: 6,
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          {card.gpuName && <span>{card.gpuName}</span>}
          {card.totalVramGb != null && (
            <>
              {card.gpuName && (
                <span style={{ color: "var(--text-subtle)" }}>·</span>
              )}
              <span style={{ fontVariantNumeric: "tabular-nums" }}>
                {card.totalVramGb.toFixed(1)} GiB VRAM
              </span>
            </>
          )}
        </div>
      )}

      <MetricRow
        label="CPU"
        value={formatPercent(latest?.cpu_percent ?? null)}
        subtitle={null}
        series={cpuSeries}
      />
      <MetricRow
        label="MEM"
        value={formatPercent(memPercent)}
        subtitle={
          latest?.mem_used_gb != null && latest.mem_total_gb != null
            ? `${latest.mem_used_gb.toFixed(1)} / ${latest.mem_total_gb.toFixed(1)} GiB`
            : null
        }
        series={memSeries}
      />
      <MetricRow
        label="GPU"
        value={formatPercent(gpuLatest?.util_percent ?? null)}
        subtitle={null}
        series={gpuSeries.util}
      />
      <MetricRow
        label="VRAM"
        value={formatPercent(vramPercent)}
        subtitle={
          gpuLatest?.mem_used_mb != null && gpuLatest.mem_total_mb != null
            ? `${(gpuLatest.mem_used_mb / 1024).toFixed(1)} / ${(gpuLatest.mem_total_mb / 1024).toFixed(1)} GiB`
            : null
        }
        series={gpuSeries.vramPercent}
      />
    </div>
  );
}

// -- metric row -------------------------------------------------------------

interface MetricRowProps {
  label: string;
  value: string;
  subtitle: string | null;
  series: (number | null)[];
}

function MetricRow({ label, value, subtitle, series }: MetricRowProps) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "40px 82px 1fr",
        alignItems: "center",
        columnGap: 10,
        padding: "4px 0",
      }}
    >
      <div
        style={{
          fontSize: "var(--fs-micro)",
          letterSpacing: "0.08em",
          fontWeight: 600,
          color: "var(--text-subtle)",
        }}
      >
        {label}
      </div>
      <div style={{ minWidth: 0 }}>
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontVariantNumeric: "tabular-nums",
            fontSize: "var(--fs-sm)",
            color: "var(--text)",
          }}
        >
          {value}
        </div>
        {subtitle && (
          <div
            style={{
              fontSize: "var(--fs-micro)",
              color: "var(--text-muted)",
              fontVariantNumeric: "tabular-nums",
              lineHeight: 1.2,
            }}
          >
            {subtitle}
          </div>
        )}
      </div>
      <Sparkline series={series} label={label} />
    </div>
  );
}

// -- sparkline (inline SVG polyline + hover cursor) -------------------------

function Sparkline({
  series,
  label,
}: {
  series: (number | null)[];
  label: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const [chartWidth, setChartWidth] = useState<number>(CHART_MIN_WIDTH);

  // Track container width via ResizeObserver so sparklines reflow when
  // the docked panel is dragged narrower / wider. One observer per
  // sparkline is fine at the fleet sizes we care about.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setChartWidth(Math.max(CHART_MIN_WIDTH, el.clientWidth));
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) {
        setChartWidth(Math.max(CHART_MIN_WIDTH, e.contentRect.width));
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const pts = useMemo(
    () => samplesToPoints(series, chartWidth, CHART_HEIGHT),
    [series, chartWidth],
  );

  const displayIdx = hoverIdx ?? series.length - 1;
  const hoverVal =
    displayIdx >= 0 && displayIdx < series.length ? series[displayIdx] : null;

  return (
    <div
      ref={ref}
      role="img"
      aria-label={`${label} history sparkline`}
      style={{ position: "relative", minWidth: CHART_MIN_WIDTH, height: CHART_HEIGHT }}
      onMouseMove={(evt) => {
        const rect = (evt.currentTarget as HTMLDivElement).getBoundingClientRect();
        const x = evt.clientX - rect.left;
        const idx = pointerToIndex(x, chartWidth, series.length);
        setHoverIdx(idx);
      }}
      onMouseLeave={() => setHoverIdx(null)}
    >
      <svg
        width={chartWidth}
        height={CHART_HEIGHT}
        viewBox={`0 0 ${chartWidth} ${CHART_HEIGHT}`}
        preserveAspectRatio="none"
        style={{ display: "block" }}
      >
        <line
          x1={0}
          y1={CHART_HEIGHT - 0.5}
          x2={chartWidth}
          y2={CHART_HEIGHT - 0.5}
          stroke="var(--border)"
          strokeWidth={1}
        />
        {pts.length >= 2 && (
          <polyline
            points={pts.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ")}
            fill="none"
            stroke="var(--accent)"
            strokeWidth={1.4}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        )}
        {pts.length === 1 && (
          <circle
            cx={pts[0].x}
            cy={pts[0].y}
            r={1.6}
            fill="var(--accent)"
          />
        )}
        {hoverIdx != null && hoverIdx >= 0 && hoverIdx < pts.length && (
          <g>
            <line
              x1={pts[hoverIdx].x}
              x2={pts[hoverIdx].x}
              y1={0}
              y2={CHART_HEIGHT}
              stroke="var(--text-subtle)"
              strokeWidth={0.75}
              strokeDasharray="2,2"
            />
            <circle
              cx={pts[hoverIdx].x}
              cy={pts[hoverIdx].y}
              r={2.2}
              fill="var(--accent)"
            />
          </g>
        )}
      </svg>
      {hoverIdx != null && hoverVal != null && (
        <div
          style={{
            position: "absolute",
            top: -18,
            right: 0,
            fontFamily: "var(--font-mono)",
            fontVariantNumeric: "tabular-nums",
            fontSize: "var(--fs-micro)",
            color: "var(--text-muted)",
            pointerEvents: "none",
            background: "var(--surface)",
            padding: "1px 4px",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-sm)",
          }}
        >
          {formatPercent(hoverVal)}
        </div>
      )}
    </div>
  );
}

// -- helpers ---------------------------------------------------------------

function memPercentOf(sample: NodeMetrics | null | undefined): number | null {
  if (!sample) return null;
  if (sample.mem_used_gb == null || sample.mem_total_gb == null) return null;
  if (sample.mem_total_gb <= 0) return null;
  return clampPercent((sample.mem_used_gb / sample.mem_total_gb) * 100);
}

function vramPercentOf(g: GpuMetricSample | null): number | null {
  if (!g) return null;
  if (g.mem_used_mb == null || g.mem_total_mb == null) return null;
  if (g.mem_total_mb <= 0) return null;
  return clampPercent((g.mem_used_mb / g.mem_total_mb) * 100);
}

function clampPercent(v: number): number {
  if (!Number.isFinite(v)) return 0;
  return Math.max(0, Math.min(100, v));
}

function formatPercent(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v.toFixed(1)}%`;
}

interface GpuSeries {
  util: (number | null)[];
  vramPercent: (number | null)[];
}

function extractGpuSeries(history: NodeMetrics[], gpuIdx: number): GpuSeries {
  const util: (number | null)[] = [];
  const vramPercent: (number | null)[] = [];
  for (const s of history) {
    const g = s.gpus?.[gpuIdx] ?? null;
    util.push(g?.util_percent ?? null);
    vramPercent.push(vramPercentOf(g));
  }
  return { util, vramPercent };
}

interface Point {
  x: number;
  y: number;
}

// Map a percent series to SVG coordinates. Fixed 0..100 y-range keeps
// the visual comparison honest across sparklines (a CPU spike to 30%
// looks the same size no matter what else is going on).
function samplesToPoints(
  series: (number | null)[],
  width: number,
  height: number,
): Point[] {
  if (series.length === 0) return [];
  const inner = Math.max(1, width - 2);
  const step = series.length > 1 ? inner / (series.length - 1) : 0;
  const pts: Point[] = [];
  for (let i = 0; i < series.length; i++) {
    const v = series[i];
    if (v == null || !Number.isFinite(v)) continue;
    const x = 1 + i * step;
    const y = height - 1 - (clampPercent(v) / 100) * (height - 2);
    pts.push({ x, y });
  }
  return pts;
}

function pointerToIndex(
  x: number,
  width: number,
  length: number,
): number | null {
  if (length <= 0) return null;
  if (length === 1) return 0;
  const inner = Math.max(1, width - 2);
  const step = inner / (length - 1);
  const idx = Math.round((x - 1) / step);
  if (idx < 0 || idx >= length) return null;
  return idx;
}
