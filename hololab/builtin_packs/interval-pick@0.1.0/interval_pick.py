"""Standard-library, index-only selection from a materialized array directory."""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def pick(source: Path, output: Path, interval: int = 2, start: int = 0) -> dict:
    if type(interval) is not int or interval < 1:
        raise ValueError('interval must be an integer >= 1 (index stride)')
    if type(start) is not int or start < 0:
        raise ValueError('start must be an integer >= 0 (zero-based index)')
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_dir():
        raise ValueError(f'input must be an array directory: {source}')
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('input and output directories must not overlap')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('output must be an empty directory')
    entries = sorted((p for p in source.iterdir() if not p.name.startswith('.')),
                     key=lambda p: os.fsencode(p.name))
    chosen = entries[start::interval]
    targets = [(p.name, p.resolve(strict=True)) for p in chosen]
    output.mkdir(parents=True, exist_ok=True)
    for name, target in targets:
        (output / name).symlink_to(target, target_is_directory=target.is_dir())
    return dict(input_count=len(entries), output_count=len(chosen),
                interval=interval, start=start)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--interval', type=int, default=2)
    parser.add_argument('--start', type=int, default=0)
    args = parser.parse_args()
    try:
        result = pick(args.input, args.output, args.interval, args.start)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'ERROR: {exc}\n')
    print(f"interval-pick: picked {result['output_count']}/{result['input_count']} "
          f"elements; interval={args.interval}, start={args.start} (indexes)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
