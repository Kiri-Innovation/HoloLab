"""Render the ``rig_timeline`` compatibility bundle from grid grouping data."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from statistics import median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import plotly.graph_objects as go

COLORS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#469990", "#f032e6"]


def _best_zoom(groups, width: int = 30) -> tuple[int, int]:
    first = groups[0].grid_index
    last = groups[-1].grid_index + 1
    if last - first <= width:
        return first, last
    counts = {group.grid_index: len(group.members) for group in groups}
    maximum = max(counts.values())
    middle = (first + last) / 2
    candidates = []
    for start in range(first, last - width + 1):
        values = [counts.get(index, 0) for index in range(start, start + width)]
        key = (
            sum(value == maximum for value in values),
            sum(values),
            -abs(start + width / 2 - middle),
        )
        candidates.append((key, start))
    start = max(candidates)[1]
    return start, start + width


def _window_records(
    records: list[dict], origin_ns: int, grid_ns: int, lo: int, hi: int
) -> list[dict]:
    return [record for record in records if lo <= (record["t_ns"] - origin_ns) // grid_ns < hi]


def _render_png(records, devices, origin_ns, grid_ns, lo, hi, path: Path, title: str) -> None:
    fig, axis = plt.subplots(figsize=(14, max(3.2, 0.58 * len(devices) + 1.8)), dpi=130)
    x0_ns = origin_ns + lo * grid_ns
    for row, device in enumerate(devices):
        color = COLORS[row % len(COLORS)]
        selected = [record for record in records if record["device_id"] == device]
        for record in selected:
            start = (record["wall_clock_epoch_ns"] - x0_ns) / 1e9
            end = start + record["exposure_ns"] / 1e9
            axis.plot([start, end], [row, row], color=color, linewidth=3, alpha=0.8)
            axis.scatter([(record["t_ns"] - x0_ns) / 1e9], [row], color=color, s=7)
    for index in range(lo, hi + 1):
        axis.axvline((index - lo) * grid_ns / 1e9, color="#777", linewidth=0.35, alpha=0.35)
    axis.set_xlim(0, (hi - lo) * grid_ns / 1e9)
    axis.set_yticks(range(len(devices)), devices)
    axis.set_xlabel(f"seconds from {x0_ns} ns (exposure segment; dot = midpoint)")
    axis.set_title(title)
    axis.grid(axis="y", alpha=0.15)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _render_html(records, devices, origin_ns, grid_ns, lo, hi, path: Path, title: str) -> None:
    x0_ns = origin_ns + lo * grid_ns
    figure = go.Figure()
    for row, device in enumerate(devices):
        selected = [record for record in records if record["device_id"] == device]
        xs: list[float | None] = []
        ys: list[str | None] = []
        midpoint_x = []
        hover = []
        for record in selected:
            start = (record["wall_clock_epoch_ns"] - x0_ns) / 1e9
            end = start + record["exposure_ns"] / 1e9
            xs.extend([start, end, None])
            ys.extend([device, device, None])
            midpoint_x.append((record["t_ns"] - x0_ns) / 1e9)
            hover.append(
                f"{device} frame={record['decoded_index']}<br>midpoint={record['t_ns']} ns"
                f"<br>exposure={record['exposure_ns']} ns"
            )
        color = COLORS[row % len(COLORS)]
        figure.add_trace(
            go.Scatter(x=xs, y=ys, mode="lines", line={"color": color, "width": 4}, name=device)
        )
        figure.add_trace(
            go.Scatter(
                x=midpoint_x,
                y=[device] * len(midpoint_x),
                mode="markers",
                marker={"color": color, "size": 6},
                text=hover,
                hovertemplate="%{text}<extra></extra>",
                showlegend=False,
            )
        )
    for index in range(lo, hi + 1):
        x = (index - lo) * grid_ns / 1e9
        figure.add_vline(x=x, line_width=0.5, line_color="#888", opacity=0.35)
    figure.update_layout(
        title=title,
        xaxis_title=f"seconds from {x0_ns} ns (exposure segment; dot = midpoint)",
        yaxis_title="camera",
        template="plotly_white",
        hovermode="closest",
    )
    figure.write_html(path, include_plotlyjs="cdn", full_html=True)


def render_exposure_timeline(records: list[dict], grouping, output: Path) -> None:
    """Write the five-file bundle consumed by HoloLab's ``rig_timeline`` viewer."""
    if not grouping.groups:
        raise ValueError("cannot render an exposure timeline for zero retained groups")
    output.mkdir(parents=True, exist_ok=True)
    devices = sorted({record["device_id"] for record in records})
    full_lo = grouping.groups[0].grid_index
    full_hi = grouping.groups[-1].grid_index + 1
    zoom_lo, zoom_hi = _best_zoom(grouping.groups)
    full_records = _window_records(records, grouping.origin_ns, grouping.grid_ns, full_lo, full_hi)
    zoom_records = _window_records(records, grouping.origin_ns, grouping.grid_ns, zoom_lo, zoom_hi)
    title = f"Exposure timeline · {len(devices)} cameras · colmap4d fixed grid"
    _render_png(
        full_records,
        devices,
        grouping.origin_ns,
        grouping.grid_ns,
        full_lo,
        full_hi,
        output / "exposure_timeline_full.png",
        title,
    )
    _render_html(
        full_records,
        devices,
        grouping.origin_ns,
        grouping.grid_ns,
        full_lo,
        full_hi,
        output / "exposure_timeline_full.html",
        title,
    )
    _render_png(
        zoom_records,
        devices,
        grouping.origin_ns,
        grouping.grid_ns,
        zoom_lo,
        zoom_hi,
        output / "exposure_timeline_zoom.png",
        f"{title} · zoom",
    )
    _render_html(
        zoom_records,
        devices,
        grouping.origin_ns,
        grouping.grid_ns,
        zoom_lo,
        zoom_hi,
        output / "exposure_timeline_zoom.html",
        f"{title} · zoom",
    )

    sizes = Counter(len(group.members) for group in grouping.groups)
    lines = [
        "# Exposure timeline",
        "",
        "This `rig_timeline` bundle is derived from the same exposure-midpoint records and",
        "`colmap4d.groups.group_by_grid` result as the materialized `groups` output.",
        "",
        "## Grouping",
        "",
        f"- grid_ns: `{grouping.grid_ns}`",
        f"- grid_origin_ns: `{grouping.origin_ns}`",
        "- timestamp convention: `wallClockEpochNs + exposureNs // 2` (exposure midpoint)",
        f"- retained grid range: `{full_lo}..{full_hi - 1}` ({len(grouping.groups)} groups)",
        f"- group-size histogram: `{dict(sorted(sizes.items()))}`",
        "",
        "## Cameras",
        "",
        "| camera | frames | exposure ns min / median / max |",
        "|---|---:|---|",
    ]
    for device in devices:
        exposures = [record["exposure_ns"] for record in records if record["device_id"] == device]
        lines.append(
            f"| {device} | {len(exposures)} | {min(exposures)} / {int(median(exposures))} / {max(exposures)} |"
        )
    lines.extend(
        [
            "",
            "## Viewer files",
            "",
            "- `exposure_timeline_full.png` / `.html`",
            "- `exposure_timeline_zoom.png` / `.html`",
            "",
        ]
    )
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
