#!/usr/bin/env python3
"""Refresh every available source without letting one failure block the others."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Allow direct execution from the repository root without packaging the module.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from stablecoin_dashboard.http import build_session  # noqa: E402
from stablecoin_dashboard.pipeline import build_current_payload, persist_payload  # noqa: E402


def _load_previous_current(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: cannot reuse {path}: {exc}", file=sys.stderr)
        return None
    if not isinstance(payload, dict):
        print(f"warning: cannot reuse {path}: root is not a JSON object", file=sys.stderr)
        return None
    return payload


def main() -> int:
    repository_root = SCRIPT_DIR.parent
    os.environ.setdefault(
        "ETHERSCAN_STATE_DIR", str(repository_root / ".state" / "etherscan")
    )
    previous = _load_previous_current(repository_root / "data" / "current.json")

    with build_session() as session:
        payload = build_current_payload(session, previous_payload=previous)

    warnings = persist_payload(repository_root, payload)

    print(f"run_status={payload['run_status']}")
    for token in payload["tokens"].values():
        largest = token.get("largest_holder_chain")
        if token.get("supply") is None or token.get("holders") is None:
            print(f"{token['symbol']}: unavailable")
            continue
        largest_text = (
            f"{largest['label']} ({largest['holders']}, {largest['status']})"
            if largest
            else "unknown"
        )
        print(
            f"{token['symbol']}: status={token['status']}, "
            f"supply={token['supply']} {token['currency']}, "
            f"holders={token['holders']}, largest={largest_text}"
        )

    for error in payload.get("source_errors", []):
        print(
            f"warning: {error['token']}/{error['chain']} via {error['provider']}: "
            f"{error['error']}",
            file=sys.stderr,
        )
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    # Partial/stale runs are valid outputs: successful sources and last-known values
    # have still been persisted. Only an actual persistence exception exits non-zero.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
