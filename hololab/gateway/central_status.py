"""Best-effort, cached comparison of offered packs against registry mirrors."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.request import urlopen

from hololab.pack_registry import SourceUnavailable, read_index, source_url
from hololab.package_identity import canonical_name, parse_spec, validate_name

CACHE_TTL_S = 600
REQUEST_TIMEOUT_S = 5


@dataclass
class CentralStatusCache:
    catalogs: dict[str, tuple[float, set[str]]] = field(default_factory=dict)
    details: dict[tuple[str, str], tuple[float, object]] = field(default_factory=dict)
    # Per-pack comparison results are presentation data.  They are distinct
    # from registry metadata cache entries because a local code hash is part
    # of a comparison result.
    comparisons: dict[tuple[tuple[str, ...], str], tuple[float, dict]] = field(default_factory=dict)

    def clear(self) -> None:
        self.catalogs.clear()
        self.details.clear()
        self.comparisons.clear()

    def comparison_snapshot(
        self, keys: list[str], sources: list[str]
    ) -> tuple[dict[str, dict], set[str]]:
        """Return cached comparison display records and keys needing refresh."""

        source_key = tuple(sorted(dict.fromkeys(source_url(s) for s in sources)))
        now = time.monotonic()
        values: dict[str, dict] = {}
        missing: set[str] = set()
        for key in keys:
            cached = self.comparisons.get((source_key, key))
            if cached is None:
                missing.add(key)
                continue
            checked_mono, result = cached
            value = dict(result)
            value["stale"] = now - checked_mono >= CACHE_TTL_S
            values[key] = value
            if value["stale"]:
                missing.add(key)
        return values, missing

    def store_comparisons(
        self, keys: list[str], sources: list[str], results: dict
    ) -> dict[str, dict]:
        """Store results with a wall-clock display timestamp and return them."""

        source_key = tuple(sorted(dict.fromkeys(source_url(s) for s in sources)))
        checked_mono = time.monotonic()
        checked_at = time.time()
        stored: dict[str, dict] = {}
        for key in keys:
            value = dict(results.get(key, {"state": "unknown", "reason": "comparison missing"}))
            value["checked_at"] = checked_at
            value["stale"] = False
            self.comparisons[(source_key, key)] = (checked_mono, value)
            stored[key] = value
        return stored

    def names(self, source: str, *, refresh: bool) -> set[str]:
        source = source_url(source)
        cached = self.catalogs.get(source)
        if not refresh and cached and time.monotonic() - cached[0] < CACHE_TTL_S:
            return cached[1]
        try:
            with urlopen(source + "packs/", timeout=REQUEST_TIMEOUT_S) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("registry catalog exceeds 16 MiB")
            data = json.loads(raw)
            names = {canonical_name(validate_name(item["name"])) for item in data["projects"]}
        except Exception as exc:
            raise SourceUnavailable(f"源不可达: {source}: {exc}") from exc
        self.catalogs[source] = (time.monotonic(), names)
        return names

    def index(self, source: str, name: str, *, refresh: bool):
        key = (source_url(source), name)
        cached = self.details.get(key)
        if not refresh and cached and time.monotonic() - cached[0] < CACHE_TTL_S:
            return cached[1]
        # Registry metadata is intentionally fetched only after ``names`` has
        # established that this package exists.
        index = read_index(key[0], name, timeout=REQUEST_TIMEOUT_S)
        self.details[key] = (time.monotonic(), index)
        return index


def compare(
    keys: list[str],
    catalog: list[dict],
    sources: list[str],
    cache: CentralStatusCache,
    *,
    refresh: bool,
) -> dict:
    local = {f"{canonical_name(p['name'])}@{p['version']}": p.get("code_sha256") for p in catalog}
    results: dict[str, dict] = {}
    normalized_sources = list(dict.fromkeys(source_url(s) for s in sources))
    parsed: list[tuple[str, str, str, str]] = []
    for raw_key in keys:
        try:
            name, version = parse_spec(raw_key)
            key = f"{canonical_name(name)}@{version}"
        except ValueError:
            results[raw_key] = {"state": "unknown", "reason": "invalid package key"}
            continue
        parsed.append((raw_key, key, canonical_name(name), version))
    if not normalized_sources:
        for raw_key, _key, _name, _version in parsed:
            results[raw_key] = {"state": "unknown", "reason": "no registry source configured"}
        return results

    names_by_source: dict[str, set[str]] = {}
    unavailable_by_source: dict[str, str] = {}
    for source in normalized_sources:
        try:
            names_by_source[source] = cache.names(source, refresh=refresh)
        except Exception as exc:
            unavailable_by_source[source] = str(exc)

    # One metadata request per central package, in parallel.  A 74-pack local
    # inventory against a 15-pack registry therefore remains bounded by the
    # slowest detail request rather than 15 serial round trips.
    candidates = {
        (source, name)
        for _raw_key, _key, name, _version in parsed
        for source, names in names_by_source.items()
        if name in names
    }
    detail_results: dict[tuple[str, str], object] = {}
    detail_errors: dict[tuple[str, str], str] = {}
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(candidates)))) as pool:
        futures = {
            (source, name): pool.submit(cache.index, source, name, refresh=refresh)
            for source, name in candidates
        }
        for candidate, future in futures.items():
            try:
                detail_results[candidate] = future.result()
            except Exception as exc:
                detail_errors[candidate] = str(exc)

    for raw_key, key, name, _version in parsed:
        unavailable: list[str] = list(unavailable_by_source.values())
        found = None
        for source in normalized_sources:
            if name not in names_by_source.get(source, set()):
                continue
            index = detail_results.get((source, name))
            if index is None:
                unavailable.append(
                    detail_errors.get((source, name), "registry metadata unavailable")
                )
                continue
            release = index.releases.get(key)
            if release is not None:
                found = (source, release)
                break
        if found is None:
            if unavailable:
                results[raw_key] = {"state": "unknown", "reason": "; ".join(unavailable)}
            else:
                results[raw_key] = {
                    "state": "absent",
                    "reason": "not present in configured registry",
                }
            continue
        source, release = found
        local_hash = local.get(key)
        state = "identical" if local_hash == release.code_sha256 else "differs"
        if local_hash is None:
            state = "unknown"
        results[raw_key] = {
            "state": state,
            "central_code_sha256": release.code_sha256,
            "yanked": release.yanked,
            "source": source,
            "reason": "local content identity unavailable" if local_hash is None else None,
        }
    return results
