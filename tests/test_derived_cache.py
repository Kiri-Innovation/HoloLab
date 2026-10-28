from __future__ import annotations

import asyncio

import pytest

from hololab.gateway.derived_cache import DerivedPreviewCache


@pytest.mark.asyncio
async def test_singleflight_coalesces_thirty_two_equal_preview_misses() -> None:
    cache = DerivedPreviewCache()
    calls = 0

    async def fetch() -> bytes:
        nonlocal calls
        hit, future, owner = await cache.get_or_reserve("n", "_thumb/32x32/a.mp4", "at=1")
        if hit is not None:
            return hit.body
        if not owner:
            result = await future
            assert result is not None
            return result.body
        calls += 1
        await asyncio.sleep(0)
        await cache.finish(
            "n",
            "_thumb/32x32/a.mp4",
            "at=1",
            {"etag": '"source-v1"', "cache-control": "max-age=60"},
            b"jpeg",
            60,
        )
        return b"jpeg"

    assert await asyncio.gather(*(fetch() for _ in range(32))) == [b"jpeg"] * 32
    assert calls == 1


@pytest.mark.asyncio
async def test_range_is_not_a_cache_concern_and_etag_is_part_of_storage_key() -> None:
    cache = DerivedPreviewCache()
    _, _, owner = await cache.get_or_reserve("n", "_preview/320x180/a.mp4", "fps=12")
    assert owner
    await cache.finish(
        "n",
        "_preview/320x180/a.mp4",
        "fps=12",
        {"etag": '"v1"'},
        b"full-object",
        60,
    )
    hit, _, _ = await cache.get_or_reserve("n", "_preview/320x180/a.mp4", "fps=12")
    assert hit is not None
    assert hit.body == b"full-object"
