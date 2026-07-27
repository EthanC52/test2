from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from stablecoin_dashboard.providers import _decimal  # noqa: E402


def active_issuer_side(lines: list[dict]) -> tuple[Decimal, int]:
    supply = Decimal(0)
    holders = 0
    for line in lines:
        balance = _decimal(line["balance"], "balance")
        if balance < 0:
            supply += -balance
            holders += 1
    return supply, holders


def test_zero_and_positive_issuer_balances_are_not_holders() -> None:
    supply, holders = active_issuer_side(
        [
            {"balance": "-12.5"},
            {"balance": "0"},
            {"balance": "3"},
            {"balance": "-0.25"},
        ]
    )
    assert supply == Decimal("12.75")
    assert holders == 2
