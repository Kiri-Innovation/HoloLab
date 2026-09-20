"""Compute-node resource sampler.

Reads CPU utilisation, memory pressure, and per-GPU utilisation +
VRAM usage every :data:`METRICS_INTERVAL_SECONDS` and emits a
``node_metrics`` frame over the existing gateway WS. Deliberately
built without new dependencies:

    * CPU     — two ``/proc/stat`` snapshots delta'd over a short
                sleep. Non-Linux hosts return None.
    * Memory  — ``/proc/meminfo`` (MemTotal + MemAvailable, so page
                cache doesn't inflate the "used" number).
    * GPUs    — ``nvidia-smi --query-gpu=…`` in a subprocess, guarded
                by a short timeout. Missing binary → empty list.

Every probe is best-effort; a failure yields None / [] for that
field only, so a partial sample still reaches the frontend and the
sparkline just skips that dot rather than freezing on stale data.

Not part of the workflow contract — the sample cadence, buffer size,
and rebroadcast path are internal to the "server pulse" panel and
can evolve independently of pack/job semantics.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time

from hololab.logging import get_logger
from hololab.protocol.messages import GpuMetricSample, NodeMetrics

log = get_logger("node.metrics")

# Cadence chosen to trade off two concerns: (1) short enough that a
# 30-second CUDA spike shows up as an actual bump on the sparkline
# rather than a single flat sample; (2) long enough that the CPU-
# delta window + nvidia-smi launch stay well under 5% of one core on
# the smallest workstation we care about.
METRICS_INTERVAL_SECONDS = 3.0

# CPU sampling reads /proc/stat twice with a small delay to compute
# active-vs-total delta. 300 ms is short enough that a spike within
# one interval still shows up on the sparkline; longer windows blur
# transient bursts.
_CPU_DELTA_WINDOW_S = 0.3

# Cap the nvidia-smi subprocess. On a healthy driver it returns in
# under 100 ms; a hung kernel module will otherwise block the metrics
# task and drop samples until the interval catches up.
_NVIDIA_SMI_TIMEOUT_S = 3.0


def _read_cpu_totals() -> tuple[int, int] | None:
    """Parse the aggregate ``cpu`` line into (idle, total) jiffies."""

    try:
        with open("/proc/stat", encoding="ascii") as f:
            first = f.readline()
    except OSError:
        return None
    if not first.startswith("cpu "):
        return None
    try:
        nums = [int(v) for v in first.split()[1:]]
    except ValueError:
        return None
    # Fields per proc(5): user, nice, system, idle, iowait, irq,
    # softirq, steal, guest, guest_nice. iowait counts as idle for
    # utilisation purposes so a disk-bound machine isn't reported at
    # 100% CPU.
    if len(nums) < 4:
        return None
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
    total = sum(nums)
    return idle, total


def sample_cpu_percent() -> float | None:
    """Return whole-machine CPU utilisation in ``[0, 100]`` or None."""

    a = _read_cpu_totals()
    if a is None:
        return None
    time.sleep(_CPU_DELTA_WINDOW_S)
    b = _read_cpu_totals()
    if b is None:
        return None
    idle_delta = b[0] - a[0]
    total_delta = b[1] - a[1]
    if total_delta <= 0:
        return None
    used = 1.0 - (idle_delta / total_delta)
    return max(0.0, min(100.0, used * 100.0))


def sample_memory() -> tuple[float | None, float | None]:
    """Return (used_gib, total_gib) using MemAvailable as the free floor."""

    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            lines = f.read().splitlines()
    except OSError:
        return (None, None)
    total_kb: int | None = None
    avail_kb: int | None = None
    for line in lines:
        if line.startswith("MemTotal:"):
            try:
                total_kb = int(line.split()[1])
            except (IndexError, ValueError):
                total_kb = None
        elif line.startswith("MemAvailable:"):
            try:
                avail_kb = int(line.split()[1])
            except (IndexError, ValueError):
                avail_kb = None
        if total_kb is not None and avail_kb is not None:
            break
    if total_kb is None or avail_kb is None:
        return (None, None)
    used_gib = max(0, total_kb - avail_kb) / (1024 * 1024)
    total_gib = total_kb / (1024 * 1024)
    return (used_gib, total_gib)


def _parse_gpu_number(raw: str) -> float | None:
    """``nvidia-smi`` uses ``[Not Supported]`` and ``[N/A]`` for gaps."""

    try:
        return float(raw)
    except ValueError:
        return None


def sample_gpus() -> list[GpuMetricSample]:
    """Query every GPU via ``nvidia-smi``. Empty list when unavailable."""

    if shutil.which("nvidia-smi") is None:
        return []
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,utilization.gpu,memory.used,memory.total,name",
                "--format=csv,noheader,nounits",
            ],
            timeout=_NVIDIA_SMI_TIMEOUT_S,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    samples: list[GpuMetricSample] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            idx = int(parts[0])
        except ValueError:
            continue
        samples.append(
            GpuMetricSample(
                index=idx,
                util_percent=_parse_gpu_number(parts[1]),
                mem_used_mb=_parse_gpu_number(parts[2]),
                mem_total_mb=_parse_gpu_number(parts[3]),
                name=parts[4] or None,
            )
        )
    return samples


def _sample_blocking() -> NodeMetrics:
    """Do the full sample synchronously — meant for ``asyncio.to_thread``."""

    cpu = sample_cpu_percent()
    used, total = sample_memory()
    return NodeMetrics(
        ts=time.time(),
        cpu_percent=cpu,
        mem_used_gb=used,
        mem_total_gb=total,
        gpus=sample_gpus(),
    )


async def sample_once() -> NodeMetrics:
    """Return one sample, keeping the event loop free."""

    return await asyncio.to_thread(_sample_blocking)
