from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from migrate_legacy_data import merge_snapshots, remove_legacy_files  # noqa: E402
from stablecoin_dashboard.models import ChainSnapshot  # noqa: E402
from stablecoin_dashboard.pipeline import build_current_payload, persist_payload  # noqa: E402


def _snapshot(chain: dict, *, supply: str = "1", holders: int = 1) -> ChainSnapshot:
    return ChainSnapshot(
        chain_id=chain["id"],
        chain_label=chain["label"],
        supply=Decimal(supply),
        holders=holders,
        source="test",
        reference=str(chain.get("asset") or chain.get("issuer")),
        observed_at="2026-07-27T12:00:00Z",
    )


def test_failed_source_reuses_previous_chain_and_other_sources_refresh(monkeypatch) -> None:
    previous = {
        "tokens": {
            "eurcv": {
                "chains": {
                    "solana": {
                        "label": "Solana",
                        "supply": "9",
                        "holders": 90,
                        "source": "previous",
                        "reference": "mint",
                        "observed_at": "2026-07-26T12:00:00Z",
                    }
                }
            }
        }
    }

    def fake_fetch(_session, chain):
        if chain["id"] == "solana" and chain.get("asset", "").startswith("Dghp"):
            raise RuntimeError("Solscan temporarily unavailable")
        return _snapshot(chain)

    monkeypatch.setattr("stablecoin_dashboard.pipeline._fetch_chain", fake_fetch)
    payload = build_current_payload(object(), previous_payload=previous)

    eurcv = payload["tokens"]["eurcv"]
    assert payload["run_status"] == "partial"
    assert eurcv["status"] == "partial"
    assert eurcv["history_ready"] is True
    assert eurcv["chains"]["solana"]["status"] == "stale"
    assert eurcv["chains"]["solana"]["supply"] == "9"
    assert eurcv["chains"]["ethereum"]["status"] == "fresh"
    assert eurcv["supply"] == "12"
    assert payload["tokens"]["usdcv"]["status"] == "complete"


def test_one_broken_history_does_not_block_current_or_other_history(tmp_path: Path) -> None:
    history_dir = tmp_path / "data" / "history"
    history_dir.mkdir(parents=True)
    (history_dir / "eurcv.json").write_text("not json", encoding="utf-8")

    payload = {
        "tokens": {
            "eurcv": {
                "symbol": "EURCV",
                "currency": "EUR",
                "supply": "10",
                "history_ready": True,
            },
            "usdcv": {
                "symbol": "USDCV",
                "currency": "USD",
                "supply": "20",
                "history_ready": True,
            },
        }
    }

    warnings = persist_payload(tmp_path, payload)

    assert (tmp_path / "data" / "current.json").exists()
    assert any("eurcv" in warning for warning in warnings)
    usdcv = json.loads((history_dir / "usdcv.json").read_text(encoding="utf-8"))
    assert usdcv["snapshots"][-1]["supply"] == "20"
    assert (history_dir / "eurcv.json").read_text(encoding="utf-8") == "not json"


def test_migration_preserves_existing_compact_snapshot_on_same_date() -> None:
    migrated = [
        {"date": "2026-07-26", "supply": "10"},
        {"date": "2026-07-27", "supply": "11"},
    ]
    existing = [
        {"date": "2026-07-27", "supply": "12"},
        {"date": "2026-07-28", "supply": "13"},
    ]
    assert merge_snapshots(migrated, existing) == [
        {"date": "2026-07-26", "supply": "10"},
        {"date": "2026-07-27", "supply": "12"},
        {"date": "2026-07-28", "supply": "13"},
    ]


def test_clean_removes_claude_directory(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "scripts").mkdir()

    remove_legacy_files(tmp_path, {"eurcv", "usdcv"})

    assert not (tmp_path / ".claude").exists()
