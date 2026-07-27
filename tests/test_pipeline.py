from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from migrate_legacy_data import aggregate_with_forward_fill  # noqa: E402
from stablecoin_dashboard.models import ChainSnapshot, decimal_to_string  # noqa: E402
from stablecoin_dashboard.providers import (  # noqa: E402
    _matches_xrpl_currency,
    _stellar_supply,
)
from stablecoin_dashboard.storage import append_daily_supply_snapshot  # noqa: E402


def test_decimal_to_string_is_plain_and_trimmed() -> None:
    assert decimal_to_string(Decimal("123.450000")) == "123.45"
    assert decimal_to_string(Decimal("0.0000010")) == "0.000001"
    assert decimal_to_string(Decimal("0")) == "0"


def test_chain_snapshot_validation() -> None:
    snapshot = ChainSnapshot(
        chain_id="ethereum",
        chain_label="Ethereum",
        supply=Decimal("10.5"),
        holders=4,
        source="test",
        reference="asset",
        observed_at="2026-01-01T00:00:00Z",
    )
    assert snapshot.to_json()["supply"] == "10.5"


def test_legacy_aggregation_forward_fills_each_chain() -> None:
    chain_a = {"2026-01-01": Decimal("10"), "2026-01-03": Decimal("12")}
    chain_b = {"2026-01-02": Decimal("5")}
    assert aggregate_with_forward_fill([chain_a, chain_b]) == [
        {"date": "2026-01-01", "supply": "10"},
        {"date": "2026-01-02", "supply": "15"},
        {"date": "2026-01-03", "supply": "17"},
    ]


def test_xrpl_currency_accepts_hex_or_text() -> None:
    encoded = "4555524356000000000000000000000000000000"
    assert _matches_xrpl_currency(encoded.lower(), encoded, "EURCV")
    assert _matches_xrpl_currency("EURCV", encoded, "EURCV")
    assert not _matches_xrpl_currency("USD", encoded, "EURCV")


def test_stellar_supply_includes_non_account_holding_forms() -> None:
    asset = {
        "balances": {"authorized": "100", "unauthorized": "2.5"},
        "claimable_balances_amount": "3",
        "contracts_amount": "4",
        "liquidity_pools_amount": "5.5",
    }
    assert _stellar_supply(asset) == Decimal("115")


def test_daily_snapshot_replaces_same_day(tmp_path: Path) -> None:
    path = tmp_path / "eurcv.json"
    append_daily_supply_snapshot(
        path,
        token_symbol="EURCV",
        currency="EUR",
        supply=Decimal("10"),
        date="2026-01-01",
    )
    append_daily_supply_snapshot(
        path,
        token_symbol="EURCV",
        currency="EUR",
        supply=Decimal("11.25"),
        date="2026-01-01",
    )
    payload = json.loads(path.read_text())
    assert payload["snapshots"] == [{"date": "2026-01-01", "supply": "11.25"}]


def test_intraday_snapshot_preserves_legacy_dates_and_appends_each_run(tmp_path: Path) -> None:
    from stablecoin_dashboard.storage import append_supply_snapshot

    path = tmp_path / "eurcv.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "token": "EURCV",
                "currency": "EUR",
                "snapshots": [{"date": "2026-07-26", "supply": "9"}],
            }
        ),
        encoding="utf-8",
    )
    append_supply_snapshot(
        path,
        token_symbol="EURCV",
        currency="EUR",
        supply=Decimal("10"),
        timestamp="2026-07-27T10:07:00Z",
    )
    append_supply_snapshot(
        path,
        token_symbol="EURCV",
        currency="EUR",
        supply=Decimal("11"),
        timestamp="2026-07-27T10:37:00Z",
    )

    payload = json.loads(path.read_text())
    assert payload["schema_version"] == 2
    assert payload["snapshots"] == [
        {"date": "2026-07-26", "supply": "9"},
        {"timestamp": "2026-07-27T10:07:00Z", "supply": "10"},
        {"timestamp": "2026-07-27T10:37:00Z", "supply": "11"},
    ]
