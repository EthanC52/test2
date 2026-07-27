from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from stablecoin_dashboard.providers import fetch_solscan, fetch_stellar, fetch_xrpl  # noqa: E402


class FakeResponse:
    def __init__(self, payload: dict[str, Any], headers: dict[str, str] | None = None):
        self._payload = payload
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSolscanSession:
    def __init__(self) -> None:
        self.last_headers: dict[str, str] | None = None

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.last_headers = kwargs.get("headers")
        return FakeResponse(
            {
                "success": True,
                "data": {
                    "decimals": 6,
                    "supply": "123456789",
                    "holder": 42,
                },
            }
        )


def test_solscan_uses_one_meta_snapshot_and_scales_base_units(monkeypatch) -> None:
    monkeypatch.setenv("SOLSCAN_API_KEY", "secret")
    session = FakeSolscanSession()
    snapshot = fetch_solscan(
        session, chain_id="solana", chain_label="Solana", mint="mint"
    )
    assert snapshot.supply == Decimal("123.456789")
    assert snapshot.holders == 42
    assert session.last_headers == {"token": "secret"}


class FakeXrplSession:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        params = kwargs["json"]["params"][0]
        self.requests.append(params)
        if "marker" not in params:
            return FakeResponse(
                {
                    "result": {
                        "status": "success",
                        "ledger_hash": "ABC",
                        "ledger_index": 123,
                        "lines": [
                            {"currency": "EURCV", "balance": "-5"},
                            {"currency": "EURCV", "balance": "0"},
                            {"currency": "USD", "balance": "-99"},
                        ],
                        "marker": {"page": 2},
                    }
                }
            )
        return FakeResponse(
            {
                "result": {
                    "status": "success",
                    "ledger_hash": "ABC",
                    "ledger_index": 123,
                    "lines": [
                        {
                            "currency": "4555524356000000000000000000000000000000",
                            "balance": "-1.25",
                        },
                        {"currency": "EURCV", "balance": "2"},
                    ],
                }
            }
        )


def test_xrpl_pins_validated_ledger_and_counts_only_active_peers(monkeypatch) -> None:
    monkeypatch.setenv("XRPL_RPC_URL", "https://example.invalid")
    session = FakeXrplSession()
    snapshot = fetch_xrpl(
        session,
        chain_id="xrpl",
        chain_label="XRP Ledger",
        issuer="issuer",
        currency_hex="4555524356000000000000000000000000000000",
        currency_text="EURCV",
    )
    assert snapshot.supply == Decimal("6.25")
    assert snapshot.holders == 2
    assert session.requests[0]["ledger_index"] == "validated"
    assert session.requests[1]["ledger_hash"] == "ABC"
    assert "ignore_default" not in session.requests[0]


class FakeStellarSession:
    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        if url.endswith("/assets"):
            return FakeResponse(
                {
                    "_embedded": {
                        "records": [
                            {
                                "asset_code": "EURCV",
                                "asset_issuer": "issuer",
                                "balances": {
                                    "authorized": "100",
                                    "unauthorized": "2",
                                },
                                "claimable_balances_amount": "3",
                                "contracts_amount": "4",
                                "liquidity_pools_amount": "5",
                                "num_contracts": 2,
                                "num_liquidity_pools": 1,
                            }
                        ]
                    }
                },
                {"X-Last-Ledger": "1000"},
            )
        if url.endswith("/accounts"):
            return FakeResponse(
                {
                    "_embedded": {
                        "records": [
                            {
                                "account_id": "A",
                                "balances": [
                                    {
                                        "asset_code": "EURCV",
                                        "asset_issuer": "issuer",
                                        "balance": "7",
                                    }
                                ],
                            },
                            {
                                "account_id": "B",
                                "balances": [
                                    {
                                        "asset_code": "EURCV",
                                        "asset_issuer": "issuer",
                                        "balance": "0",
                                    }
                                ],
                            },
                        ]
                    },
                    "_links": {"next": {"href": "https://horizon.example/page-2"}},
                },
                {"X-Last-Ledger": "1001"},
            )
        if url.endswith("page-2"):
            return FakeResponse(
                {"_embedded": {"records": []}, "_links": {}},
                {"X-Last-Ledger": "1001"},
            )
        raise AssertionError(url)


def test_stellar_supply_is_complete_and_zero_trustlines_are_not_holders(monkeypatch) -> None:
    monkeypatch.setenv("STELLAR_HORIZON_URL", "https://horizon.example")
    snapshot = fetch_stellar(
        FakeStellarSession(),
        chain_id="stellar",
        chain_label="Stellar",
        issuer="issuer",
        asset_code="EURCV",
    )
    assert snapshot.supply == Decimal("114")
    # One positive classic account + two contracts + one liquidity pool.
    assert snapshot.holders == 4
    assert snapshot.details["claimable_balances_in_holder_count"] is False
