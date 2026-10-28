"""Reproducible preview baseline/tunnel comparison.

Run against two already-running gateways (never starts or restarts services):

  python scripts/benchmark_preview_paths.py \
    --baseline http://gw-a/proxy/node/_thumb/320x180/file-{i}.mp4 \
    --tunnel http://gw-b/proxy/node/_thumb/320x180/file-{i}.mp4 \
    --range-url http://gw-b/proxy/node/_preview/320x180/video.mp4 \
    --out reports/preview-benchmark.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx


async def _one(client: httpx.AsyncClient, url: str, headers: dict[str, str] | None = None) -> float:
    started = time.perf_counter()
    response = await client.get(url, headers=headers)
    response.raise_for_status()
    await response.aread()
    return (time.perf_counter() - started) * 1000


async def _grid(template: str, count: int) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=60) as client:
        started = time.perf_counter()
        samples = await asyncio.gather(*(_one(client, template.format(i=i)) for i in range(count)))
    return {
        "total_ms": (time.perf_counter() - started) * 1000,
        "completion_ms": sorted(samples),
        "p50_ms": statistics.median(samples),
        "p95_ms": sorted(samples)[max(0, int(len(samples) * 0.95) - 1)],
    }


async def _seeks(url: str) -> list[float]:
    async with httpx.AsyncClient(timeout=60) as client:
        return [
            await _one(client, url, {"Range": f"bytes={offset}-{offset + 65535}"})
            for offset in (0, 1_000_000, 250_000, 2_000_000, 500_000)
        ]


async def main(args: argparse.Namespace) -> None:
    # Calling each path twice records cold then warm browser/node/cache state.
    result = {
        "count": args.count,
        "baseline": {
            "cold": await _grid(args.baseline, args.count),
            "warm": await _grid(args.baseline, args.count),
        },
        "tunnel": {
            "cold": await _grid(args.tunnel, args.count),
            "warm": await _grid(args.tunnel, args.count),
        },
    }
    if args.range_url:
        result["range_seek_ms"] = await _seeks(args.range_url)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--baseline",
        required=True,
        help="direct advertised_url gateway URL template containing {i}",
    )
    parser.add_argument(
        "--tunnel", required=True, help="reverse-tunnel gateway URL template containing {i}"
    )
    parser.add_argument("--range-url")
    parser.add_argument("--count", type=int, default=32)
    parser.add_argument("--out", default="reports/preview-benchmark.json")
    asyncio.run(main(parser.parse_args()))
