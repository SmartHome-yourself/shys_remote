#!/usr/bin/env python3
"""Analyze protocol usage in the bundled Flipper-IRDB index.

Downloads a sample of .ir files and reports which parsed protocols appear,
how many signals are importable with the current parser, and what is skipped.

Examples:
  python analyze_irdb_protocols.py
  python analyze_irdb_protocols.py --sample-size 200
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

import aiohttp

SCRIPT_DIR = Path(__file__).resolve().parent
COMPONENT_DIR = SCRIPT_DIR.parent
INDEX_PATH = COMPONENT_DIR / "data" / "irdb_index.json"
IRDB_RAW_URL = (
    "https://raw.githubusercontent.com/Lucaslhm/Flipper-IRDB/main/{path}"
)

if str(COMPONENT_DIR) not in sys.path:
    sys.path.insert(0, str(COMPONENT_DIR))

from flipper_ir import parse_flipper_ir, signals_to_command_map


def _load_paths() -> list[str]:
    payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    paths = payload.get("paths", [])
    return [path for path in paths if isinstance(path, str) and path.endswith(".ir")]


async def _fetch_remote(
    session: aiohttp.ClientSession, path: str
) -> tuple[str, str | None]:
    url = IRDB_RAW_URL.format(path=path)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status != 200:
                return path, None
            return path, await response.text()
    except (TimeoutError, aiohttp.ClientError):
        return path, None


async def _analyze(sample_size: int, seed: int) -> int:
    paths = _load_paths()
    if not paths:
        print("No paths found in bundled index.", file=sys.stderr)
        return 1

    rng = random.Random(seed)
    sample = paths if sample_size >= len(paths) else rng.sample(paths, sample_size)

    protocol_counter: Counter[str] = Counter()
    signal_type_counter: Counter[str] = Counter()
    unsupported_protocols: Counter[str] = Counter()
    remotes_with_zero_supported: list[str] = []
    remotes_partial: list[tuple[str, int, int]] = []
    category_protocols: dict[str, Counter[str]] = defaultdict(Counter)

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *(_fetch_remote(session, path) for path in sample)
        )

    for path, content in results:
        if not content:
            continue

        signals = parse_flipper_ir(content)
        if not signals:
            continue

        commands, skipped = signals_to_command_map(signals)
        imported = len(commands)
        total = imported + skipped
        category = path.split("/", 1)[0]

        if imported == 0 and total > 0:
            remotes_with_zero_supported.append(path)
        elif skipped:
            remotes_partial.append((path, imported, skipped))

        for signal in signals:
            signal_type = signal.get("type", "unknown").lower()
            signal_type_counter[signal_type] += 1
            if signal_type in {"parsed", "parsed_array"}:
                protocol = str(signal.get("protocol", "unknown"))
                protocol_counter[protocol] += 1
                category_protocols[category][protocol] += 1
                command_data = None
                from flipper_ir import signal_to_command_data

                if signal_to_command_data(signal) is None:
                    unsupported_protocols[protocol] += 1

    print(f"Sample size: {len(sample)} remotes")
    print(f"Signal types: {dict(signal_type_counter)}")
    print("\nParsed protocols:")
    for protocol, count in protocol_counter.most_common():
        unsupported = unsupported_protocols.get(protocol, 0)
        supported = count - unsupported
        print(
            f"  {protocol:16} total={count:5} supported={supported:5} skipped={unsupported:5}"
        )

    print("\nTop categories with Kaseikyo:")
    for category, counter in sorted(category_protocols.items()):
        if counter.get("Kaseikyo"):
            print(f"  {category}: {counter['Kaseikyo']}")

    print(f"\nRemotes with zero supported signals: {len(remotes_with_zero_supported)}")
    for path in remotes_with_zero_supported[:20]:
        print(f"  - {path}")
    if len(remotes_with_zero_supported) > 20:
        print(f"  ... and {len(remotes_with_zero_supported) - 20} more")

    print(f"\nRemotes partially imported: {len(remotes_partial)}")
    for path, imported, skipped in remotes_partial[:10]:
        print(f"  - {path}: {imported} imported, {skipped} skipped")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample-size",
        type=int,
        default=150,
        help="Number of remotes to sample from the bundled index",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()
    return asyncio.run(_analyze(args.sample_size, args.seed))


if __name__ == "__main__":
    raise SystemExit(main())
