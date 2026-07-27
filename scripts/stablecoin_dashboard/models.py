"""Internal data models and deterministic JSON serialization helpers."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from typing import Any


def decimal_to_string(value: Decimal) -> str:
    """Serialize a Decimal without exponent notation or meaningless zeroes."""
    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


@dataclass(frozen=True)
class ChainSnapshot:
    chain_id: str
    chain_label: str
    supply: Decimal
    holders: int
    source: str
    reference: str
    observed_at: str
    details: dict[str, Any] = field(default_factory=dict)
    status: str = "fresh"
    error: str | None = None

    def validate(self) -> None:
        if not self.supply.is_finite() or self.supply < 0:
            raise ValueError(f"Invalid supply for {self.chain_id}: {self.supply}")
        if self.holders < 0:
            raise ValueError(f"Negative holder count for {self.chain_id}: {self.holders}")
        if self.status not in {"fresh", "stale"}:
            raise ValueError(f"Invalid snapshot status for {self.chain_id}: {self.status}")
        if not self.observed_at:
            raise ValueError(f"Missing observed_at for {self.chain_id}")

    def as_stale(self, error: str) -> "ChainSnapshot":
        return replace(self, status="stale", error=error)

    @classmethod
    def from_json(cls, chain_id: str, payload: dict[str, Any]) -> "ChainSnapshot":
        if not isinstance(payload, dict):
            raise ValueError(f"Previous chain payload for {chain_id} is not an object")
        try:
            supply = Decimal(str(payload["supply"]))
            holders = int(payload["holders"])
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid previous chain payload for {chain_id}: {payload}") from exc

        snapshot = cls(
            chain_id=chain_id,
            chain_label=str(payload.get("label") or chain_id),
            supply=supply,
            holders=holders,
            source=str(payload.get("source") or "previous current.json"),
            reference=str(payload.get("reference") or "unknown"),
            observed_at=str(payload.get("observed_at") or "unknown"),
            details=dict(payload.get("details") or {}),
            status=str(payload.get("status") or "fresh"),
            error=str(payload["error"]) if payload.get("error") else None,
        )
        snapshot.validate()
        return snapshot

    def to_json(self) -> dict[str, Any]:
        self.validate()
        payload: dict[str, Any] = {
            "label": self.chain_label,
            "supply": decimal_to_string(self.supply),
            "holders": self.holders,
            "source": self.source,
            "reference": self.reference,
            "observed_at": self.observed_at,
            "status": self.status,
        }
        if self.error:
            payload["error"] = self.error
        if self.details:
            payload["details"] = self.details
        return payload
