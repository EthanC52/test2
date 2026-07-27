"""Orchestrate source adapters into a resilient compact data contract."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

import requests

from .config import TOKENS
from .models import ChainSnapshot, decimal_to_string
from .providers import fetch_etherscan, fetch_solscan, fetch_stellar, fetch_xrpl
from .storage import append_supply_snapshot, write_json_atomic

Provider = Callable[..., ChainSnapshot]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fetch_chain(session: requests.Session, chain: dict) -> ChainSnapshot:
    common = {"session": session, "chain_id": chain["id"], "chain_label": chain["label"]}
    provider = chain["provider"]
    if provider == "etherscan":
        return fetch_etherscan(**common, contract=chain["asset"])
    if provider == "solscan":
        return fetch_solscan(**common, mint=chain["asset"])
    if provider == "xrpl":
        return fetch_xrpl(
            **common,
            issuer=chain["issuer"],
            currency_hex=chain["currency"],
            currency_text=chain["currency_text"],
        )
    if provider == "stellar":
        return fetch_stellar(
            **common,
            issuer=chain["issuer"],
            asset_code=chain["asset_code"],
        )
    raise ValueError(f"Unknown provider: {provider}")


def _previous_snapshot(
    previous_payload: dict | None,
    token_id: str,
    chain_id: str,
) -> ChainSnapshot | None:
    try:
        chain_payload = previous_payload["tokens"][token_id]["chains"][chain_id]
    except (KeyError, TypeError):
        return None
    try:
        return ChainSnapshot.from_json(chain_id, chain_payload)
    except ValueError:
        return None


def _error_message(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return f"{exc.__class__.__name__}: {message}"[:1000]


def build_current_payload(
    session: requests.Session,
    previous_payload: dict | None = None,
) -> dict:
    started_at = _utc_now_iso()
    result: dict = {
        "schema_version": 2,
        "started_at": started_at,
        "generated_at": started_at,
        "holder_aggregation": "sum_across_chains",
        "run_status": "unavailable",
        "source_errors": [],
        "tokens": {},
    }

    total_fresh = 0
    total_available = 0
    total_configured = 0

    for token_id, token in TOKENS.items():
        snapshots: list[ChainSnapshot] = []
        missing_chains: list[str] = []
        token_errors: dict[str, str] = {}
        configured_count = len(token["chains"])
        total_configured += configured_count

        for chain in token["chains"]:
            chain_id = chain["id"]
            try:
                snapshot = _fetch_chain(session, chain)
                snapshot.validate()
                snapshots.append(snapshot)
                total_fresh += 1
            except Exception as exc:  # isolate every upstream and serialization failure
                message = _error_message(exc)
                token_errors[chain_id] = message
                result["source_errors"].append(
                    {
                        "token": token_id,
                        "chain": chain_id,
                        "provider": chain["provider"],
                        "error": message,
                    }
                )
                previous = _previous_snapshot(previous_payload, token_id, chain_id)
                if previous is not None:
                    snapshots.append(previous.as_stale(message))
                else:
                    missing_chains.append(chain_id)

        total_available += len(snapshots)
        fresh_count = sum(snapshot.status == "fresh" for snapshot in snapshots)
        stale_count = sum(snapshot.status == "stale" for snapshot in snapshots)

        if not snapshots:
            result["tokens"][token_id] = {
                "name": token["name"],
                "symbol": token["symbol"],
                "currency": token["currency"],
                "status": "unavailable",
                "supply": None,
                "holders": None,
                "largest_holder_chain": None,
                "fresh_chain_count": 0,
                "stale_chain_count": 0,
                "configured_chain_count": configured_count,
                "history_ready": False,
                "missing_chains": missing_chains,
                "errors": token_errors,
                "chains": {},
            }
            continue

        total_supply = sum((snapshot.supply for snapshot in snapshots), Decimal(0))
        total_holders = sum(snapshot.holders for snapshot in snapshots)
        largest = max(snapshots, key=lambda item: item.holders)

        if fresh_count == configured_count:
            status = "complete"
        elif fresh_count > 0:
            status = "partial"
        else:
            status = "stale"

        # A history point is safe only when every configured chain has either a fresh
        # value or a prior last-known value, and at least one source actually refreshed.
        history_ready = len(snapshots) == configured_count and fresh_count > 0

        result["tokens"][token_id] = {
            "name": token["name"],
            "symbol": token["symbol"],
            "currency": token["currency"],
            "status": status,
            "supply": decimal_to_string(total_supply),
            "holders": total_holders,
            "largest_holder_chain": {
                "id": largest.chain_id,
                "label": largest.chain_label,
                "holders": largest.holders,
                "status": largest.status,
            },
            "fresh_chain_count": fresh_count,
            "stale_chain_count": stale_count,
            "configured_chain_count": configured_count,
            "history_ready": history_ready,
            "missing_chains": missing_chains,
            "errors": token_errors,
            "chains": {snapshot.chain_id: snapshot.to_json() for snapshot in snapshots},
        }

    if total_fresh == total_configured:
        result["run_status"] = "complete"
    elif total_fresh > 0:
        result["run_status"] = "partial"
    elif total_available > 0:
        result["run_status"] = "stale"
    else:
        result["run_status"] = "unavailable"

    result["generated_at"] = _utc_now_iso()
    return result


def persist_payload(repository_root: Path, payload: dict) -> list[str]:
    """Persist current data first, then independently update each safe history.

    A malformed or unwritable history file must not prevent `current.json` or the other
    token history from being updated. Returned messages are warnings for the caller.
    """
    data_dir = repository_root / "data"
    write_json_atomic(data_dir / "current.json", payload)

    warnings: list[str] = []
    for token_id, token in payload.get("tokens", {}).items():
        if not token.get("history_ready") or token.get("supply") is None:
            warnings.append(
                f"history {token_id} skipped: aggregate supply is incomplete or no source refreshed"
            )
            continue
        try:
            append_supply_snapshot(
                data_dir / "history" / f"{token_id}.json",
                token_symbol=token["symbol"],
                currency=token["currency"],
                supply=Decimal(str(token["supply"])),
                timestamp=str(payload.get("generated_at") or _utc_now_iso()),
            )
        except Exception as exc:
            warnings.append(f"history {token_id} not updated: {_error_message(exc)}")
    return warnings
