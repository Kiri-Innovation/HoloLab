"""Reproducible direct-file-server versus reverse-tunnel preview benchmark.

Example (discovers 32 real PNGs under the workspace):

  python scripts/benchmark_preview_paths.py \
    --workspace-root /srv/hololab/output --min-bytes 2000000 --count 32 \
    --baseline-base http://127.0.0.1:8829 \
    --tunnel-base http://127.0.0.1:8828/proxy/<node-id> \
    --concurrency 1,4,8,16,32 --out /tmp/preview-benchmark.json

The process never starts or restarts services. Its client pool is explicitly
64 connections, so the requested gradient isn't limited by the client.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from urllib.parse import quote

import httpx

_CLIENT_CONNECTIONS = 64


async def _one(client: httpx.AsyncClient, url: str) -> dict[str, object]:
    started = time.perf_counter()
    try:
        response = await client.get(url)
        await response.aread()
        response.raise_for_status()
    except Exception as exc:
        return {
            "ok": False,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "error": repr(exc),
        }
    return {"ok": True, "elapsed_ms": (time.perf_counter() - started) * 1000}


async def _grid(urls: list[str], concurrency: int) -> dict[str, object]:
    semaphore = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(
        max_connections=_CLIENT_CONNECTIONS,
        max_keepalive_connections=_CLIENT_CONNECTIONS,
    )
    async with httpx.AsyncClient(limits=limits, timeout=60, trust_env=False) as client:

        async def limited(url: str) -> dict[str, object]:
            async with semaphore:
                return await _one(client, url)

        started = time.perf_counter()
        samples = await asyncio.gather(*(limited(url) for url in urls))
    elapsed = (time.perf_counter() - started) * 1000
    good = sorted(float(sample["elapsed_ms"]) for sample in samples if sample["ok"])
    failures = [str(sample["error"]) for sample in samples if not sample["ok"]]
    return {
        "total_ms": elapsed,
        "completion_ms": good,
        "p50_ms": statistics.median(good) if good else None,
        "p95_ms": good[max(0, int(len(good) * 0.95) - 1)] if good else None,
        "failures": len(failures),
        "errors": failures[:3],
    }


def _workspace_paths(root: Path, count: int, minimum_bytes: int) -> list[str]:
    paths = [
        file.relative_to(root).as_posix()
        for file in sorted(root.rglob("*.png"))
        if file.is_file() and file.stat().st_size >= minimum_bytes
    ]
    if len(paths) < count:
        raise ValueError(
            f"only found {len(paths)} PNG files >= {minimum_bytes} bytes; need {count}"
        )
    return paths[:count]


def _urls(base: str, paths: list[str]) -> list[str]:
    return [base.rstrip("/") + "/" + quote(path, safe="/") for path in paths]


async def main(args: argparse.Namespace) -> None:
    paths = _workspace_paths(Path(args.workspace_root), args.count, args.min_bytes)
    concurrencies = [int(value) for value in args.concurrency.split(",")]
    if not concurrencies or any(
        value < 1 or value > _CLIENT_CONNECTIONS for value in concurrencies
    ):
        raise ValueError(f"--concurrency must contain values from 1 to {_CLIENT_CONNECTIONS}")

    result: dict[str, object] = {
        "count": len(paths),
        "min_bytes": args.min_bytes,
        "concurrency": concurrencies,
        "paths": paths,
        "baseline": {},
        "tunnel": {},
    }
    for label, base in (("baseline", args.baseline_base), ("tunnel", args.tunnel_base)):
        by_concurrency: dict[str, object] = result[label]  # type: ignore[assignment]
        urls = _urls(base, paths)
        for concurrency in concurrencies:
            measurement = await _grid(urls, concurrency)
            by_concurrency[str(concurrency)] = measurement
            print(
                f"{label} conc={concurrency} total={measurement['total_ms']:.1f}ms "
                f"p50={measurement['p50_ms']!s} fails={measurement['failures']}",
                flush=True,
            )

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--baseline-base", required=True, help="file-server base URL")
    parser.add_argument("--tunnel-base", required=True, help="gateway /proxy/<node-id> base URL")
    parser.add_argument("--concurrency", default="1,4,8,16,32")
    parser.add_argument("--count", type=int, default=32)
    parser.add_argument("--min-bytes", type=int, default=2_000_000)
    parser.add_argument("--out", default="/tmp/preview-benchmark.json")
    asyncio.run(main(parser.parse_args()))
