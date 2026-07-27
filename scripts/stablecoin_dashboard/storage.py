"""Atomic JSON persistence and supply-history maintenance."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import decimal_to_string


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _snapshot_key(item: dict[str, Any]) -> str | None:
    value = item.get("timestamp") or item.get("date")
    if value is None:
        return None
    key = str(value).strip()
    return key or None


def append_supply_snapshot(
    path: Path,
    *,
    token_symbol: str,
    currency: str,
    supply: Decimal,
    timestamp: str | None = None,
) -> None:
    """Append one successful run while preserving all legacy date-only snapshots.

    New points use an ISO-8601 UTC timestamp, so a workflow running every 30 minutes
    genuinely extends the chart instead of replacing the sole point for the day.
    """
    snapshot_timestamp = timestamp or (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    existing = read_json(
        path,
        {
            "schema_version": 2,
            "token": token_symbol,
            "currency": currency,
            "snapshots": [],
        },
    )
    if not isinstance(existing, dict):
        raise ValueError(f"Invalid history file: {path}")
    snapshots = existing.get("snapshots", [])
    if not isinstance(snapshots, list):
        raise ValueError(f"Invalid history file: {path}")

    by_key: dict[str, dict[str, str]] = {}
    for item in snapshots:
        if not isinstance(item, dict) or "supply" not in item:
            continue
        key = _snapshot_key(item)
        if key is None:
            continue
        normalized = {"supply": str(item["supply"])}
        if "timestamp" in item:
            normalized["timestamp"] = key
        else:
            normalized["date"] = key
        by_key[key] = normalized

    by_key[snapshot_timestamp] = {
        "timestamp": snapshot_timestamp,
        "supply": decimal_to_string(supply),
    }
    existing.update(
        {
            "schema_version": 2,
            "token": token_symbol,
            "currency": currency,
            "snapshot_cadence": "one point per successful fetch run",
            "snapshots": [by_key[key] for key in sorted(by_key)],
        }
    )
    write_json_atomic(path, existing)


def append_daily_supply_snapshot(
    path: Path,
    *,
    token_symbol: str,
    currency: str,
    supply: Decimal,
    date: str | None = None,
) -> None:
    """Backward-compatible helper retained for old tests and one-off scripts."""
    snapshot_date = date or datetime.now(timezone.utc).date().isoformat()
    existing = read_json(
        path,
        {
            "schema_version": 1,
            "token": token_symbol,
            "currency": currency,
            "snapshots": [],
        },
    )
    snapshots = existing.get("snapshots", [])
    if not isinstance(snapshots, list):
        raise ValueError(f"Invalid history file: {path}")

    by_date = {
        str(item["date"]): {"date": str(item["date"]), "supply": str(item["supply"])}
        for item in snapshots
        if isinstance(item, dict) and "date" in item and "supply" in item
    }
    by_date[snapshot_date] = {
        "date": snapshot_date,
        "supply": decimal_to_string(supply),
    }
    existing.update(
        {
            "schema_version": 1,
            "token": token_symbol,
            "currency": currency,
            "snapshots": [by_date[key] for key in sorted(by_date)],
        }
    )
    write_json_atomic(path, existing)
