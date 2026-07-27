#!/usr/bin/env python3
"""Collapse legacy per-chain JSON histories without losing existing snapshots.

Run once inside the existing repository before deleting old files:
    python scripts/migrate_legacy_data.py --clean
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from stablecoin_dashboard.models import decimal_to_string  # noqa: E402
from stablecoin_dashboard.storage import write_json_atomic  # noqa: E402

LEGACY_HISTORY_FILES = {
    "eurcv": {
        "symbol": "EURCV",
        "currency": "EUR",
        "files": [
            "marketcap.json",
            "sol_marketcap.json",
            "xrpl_marketcap.json",
            "stellar_marketcap.json",
        ],
    },
    "usdcv": {
        "symbol": "USDCV",
        "currency": "USD",
        "files": [
            "usdcv_eth_marketcap.json",
            "usdcv_sol_marketcap.json",
        ],
    },
}

LEGACY_TOKEN_DATA_FILES = {
    "eurcv": {
        "eurcv_eth_state.json",
        "holders.json",
        "marketcap.json",
        "sol_holders.json",
        "sol_known_holders.json",
        "sol_marketcap.json",
        "stellar_holders.json",
        "stellar_marketcap.json",
        "stellar_state.json",
        "xrpl_holders.json",
        "xrpl_marketcap.json",
        "xrpl_state.json",
    },
    "usdcv": {
        "usdcv_eth_holders.json",
        "usdcv_eth_marketcap.json",
        "usdcv_eth_state.json",
        "usdcv_sol_holders.json",
        "usdcv_sol_known_holders.json",
        "usdcv_sol_marketcap.json",
    },
}

LEGACY_GLOBAL_DATA_FILES = {"eurusd_rates.json"}
LEGACY_DIRECTORIES = {".claude", "img"}
LEGACY_SCRIPTS = {
    "fetch_data.py",
    "fetch_eurusd_rates.py",
    "fetch_sol_data.py",
    "fetch_stellar_data.py",
    "fetch_usdcv_eth_data.py",
    "fetch_usdcv_sol_data.py",
    "fetch_xrpl_data.py",
    "migrate_add_supply.py",
}


def _decimal_supply(value: Any, path: Path) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid supply in {path}: {value!r}") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"Invalid supply in {path}: {value!r}")
    return result


def load_supply_series(path: Path) -> dict[str, Decimal]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError(f"Legacy history must be a list: {path}")

    series: dict[str, Decimal] = {}
    for item in payload:
        if not isinstance(item, dict) or "date" not in item or "supply" not in item:
            continue
        date = str(item["date"])
        if not date:
            continue
        series[date] = _decimal_supply(item["supply"], path)
    return series


def load_existing_history(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("snapshots"), list):
        raise ValueError(f"Existing history has an invalid schema: {path}")

    snapshots: list[dict[str, str]] = []
    for item in payload["snapshots"]:
        if not isinstance(item, dict) or "supply" not in item:
            raise ValueError(f"Invalid snapshot in {path}: {item!r}")
        key_name = "timestamp" if "timestamp" in item else "date" if "date" in item else None
        if key_name is None:
            raise ValueError(f"Snapshot has no date or timestamp in {path}: {item!r}")
        snapshots.append(
            {
                key_name: str(item[key_name]),
                "supply": decimal_to_string(_decimal_supply(item["supply"], path)),
            }
        )
    return snapshots


def _snapshot_key(item: dict[str, str]) -> str:
    return str(item.get("timestamp") or item.get("date") or "")


def aggregate_with_forward_fill(series_list: list[dict[str, Decimal]]) -> list[dict[str, str]]:
    """Sum chain supplies on each known date, carrying each chain's latest value."""
    dates = sorted({date for series in series_list for date in series})
    latest: list[Decimal | None] = [None] * len(series_list)
    snapshots: list[dict[str, str]] = []

    for date in dates:
        for index, series in enumerate(series_list):
            if date in series:
                latest[index] = series[date]
        if not any(value is not None for value in latest):
            continue
        total = sum((value or Decimal(0) for value in latest), Decimal(0))
        snapshots.append({"date": date, "supply": decimal_to_string(total)})
    return snapshots


def merge_snapshots(
    migrated: list[dict[str, str]], existing: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Merge old daily and new intraday points without deleting either format."""
    by_key: dict[str, dict[str, str]] = {}
    for item in migrated:
        by_key[_snapshot_key(item)] = dict(item)
    for item in existing:
        by_key[_snapshot_key(item)] = dict(item)
    return [by_key[key] for key in sorted(by_key) if key]


def _valid_history_output(path: Path) -> bool:
    try:
        return bool(load_existing_history(path))
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def remove_legacy_files(root: Path, ready_tokens: set[str]) -> None:
    for token_id in sorted(ready_tokens):
        for filename in sorted(LEGACY_TOKEN_DATA_FILES[token_id]):
            path = root / "data" / filename
            if path.exists():
                path.unlink()
                print(f"removed {path.relative_to(root)}")

    # Global rates and obsolete fetchers are safe to remove once every detected token
    # has a valid compact history. This avoids deleting the only usable history after a
    # partial migration failure.
    if ready_tokens == set(LEGACY_HISTORY_FILES):
        for filename in sorted(LEGACY_GLOBAL_DATA_FILES):
            path = root / "data" / filename
            if path.exists():
                path.unlink()
                print(f"removed {path.relative_to(root)}")
        for filename in sorted(LEGACY_SCRIPTS):
            path = root / "scripts" / filename
            if path.exists():
                path.unlink()
                print(f"removed {path.relative_to(root)}")

    for dirname in sorted(LEGACY_DIRECTORIES):
        path = root / dirname
        if path.exists() and path.is_dir():
            shutil.rmtree(path)
            print(f"removed {path.relative_to(root)}/")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete legacy JSONs/scripts and .claude after successful migration.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=SCRIPT_DIR.parent,
        help="Repository root (defaults to this script's parent repository).",
    )
    args = parser.parse_args()
    root = args.root.resolve()

    ready_tokens: set[str] = set()
    for token_id, config in LEGACY_HISTORY_FILES.items():
        output = root / "data" / "history" / f"{token_id}.json"
        existing = load_existing_history(output)
        series = [load_supply_series(root / "data" / name) for name in config["files"]]
        non_empty = [item for item in series if item]

        if non_empty:
            migrated = aggregate_with_forward_fill(series)
            snapshots = merge_snapshots(migrated, existing)
            payload = {
                "schema_version": 2,
                "token": config["symbol"],
                "currency": config["currency"],
                "snapshots": snapshots,
                "migration": {
                    "method": "sum per-chain supply with forward fill",
                    "sources": config["files"],
                    "existing_compact_history_preserved": bool(existing),
                },
            }
            write_json_atomic(output, payload)
            print(f"wrote {output.relative_to(root)} ({len(snapshots)} snapshots)")
        elif existing:
            print(f"kept {output.relative_to(root)} ({len(existing)} snapshots)")
        else:
            print(f"warning: no supply history found for {config['symbol']}")

        if _valid_history_output(output):
            ready_tokens.add(token_id)

    if not ready_tokens:
        raise SystemExit("No valid token history exists; data cleanup aborted.")

    if args.clean:
        remove_legacy_files(root, ready_tokens)
        missing = set(LEGACY_HISTORY_FILES) - ready_tokens
        if missing:
            print(
                "warning: cleanup kept global legacy scripts/rates because histories are missing for: "
                + ", ".join(sorted(missing))
            )
    else:
        print("legacy files kept; rerun with --clean after reviewing the compact histories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
