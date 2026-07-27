from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from stablecoin_dashboard.providers import fetch_etherscan  # noqa: E402


class FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeEtherscanSession:
    def __init__(self) -> None:
        self.queries: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        params = dict(kwargs["params"])
        self.queries.append(params)
        action = params["action"]

        if action == "eth_blockNumber":
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": "0x64"})
        if action == "eth_call" and params["data"] == "0x313ce567":
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": "0x6"})
        if action == "eth_call" and params["data"] == "0x18160ddd":
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": hex(1_000_000)})
        if action == "tokentx":
            contract = params["contractaddress"]
            rows = [
                {
                    "blockNumber": "10",
                    "hash": "mint-a",
                    "transactionIndex": "0",
                    "contractAddress": contract,
                    "from": "0x0000000000000000000000000000000000000000",
                    "to": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "value": "600000",
                },
                {
                    "blockNumber": "11",
                    "hash": "mint-b",
                    "transactionIndex": "0",
                    "contractAddress": contract,
                    "from": "0x0000000000000000000000000000000000000000",
                    "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    "value": "400000",
                },
                {
                    "blockNumber": "12",
                    "hash": "move",
                    "transactionIndex": "0",
                    "contractAddress": contract,
                    "from": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    "value": "100000",
                },
            ]
            return FakeResponse({"status": "1", "message": "OK", "result": rows})
        raise AssertionError(params)


def test_etherscan_free_replays_transfers_at_one_pinned_block(monkeypatch) -> None:
    monkeypatch.setenv("ETHERSCAN_API_KEY", "free-key")
    monkeypatch.setenv("ETHERSCAN_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("ETHERSCAN_CONFIRMATIONS", "0")
    monkeypatch.delenv("ETHERSCAN_STATE_DIR", raising=False)
    session = FakeEtherscanSession()

    snapshot = fetch_etherscan(
        session,
        chain_id="ethereum",
        chain_label="Ethereum",
        contract="0xcccccccccccccccccccccccccccccccccccccccc",
    )

    assert snapshot.supply == Decimal("1")
    assert snapshot.holders == 2
    assert snapshot.details["block"] == 100
    assert snapshot.details["transfer_count"] == 3

    actions = [query["action"] for query in session.queries]
    assert "tokenholdercount" not in actions
    assert actions == ["eth_blockNumber", "eth_call", "eth_call", "tokentx"]
    eth_calls = [query for query in session.queries if query["action"] == "eth_call"]
    assert all(query["tag"] == "0x64" for query in eth_calls)
    transfer_query = next(query for query in session.queries if query["action"] == "tokentx")
    assert transfer_query["endblock"] == 100
    assert transfer_query["offset"] == 1000


class IncrementalEtherscanSession:
    def __init__(self, *, latest_block: int, transfers: list[dict[str, str]]) -> None:
        self.latest_block = latest_block
        self.transfers = transfers
        self.queries: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        params = dict(kwargs["params"])
        self.queries.append(params)
        action = params["action"]
        if action == "eth_blockNumber":
            return FakeResponse({"result": hex(self.latest_block)})
        if action == "eth_call" and params["data"] == "0x313ce567":
            return FakeResponse({"result": "0x6"})
        if action == "eth_call" and params["data"] == "0x18160ddd":
            return FakeResponse({"result": hex(1_000_000)})
        if action == "tokentx":
            start = int(params["startblock"])
            end = int(params["endblock"])
            rows = [
                row
                for row in self.transfers
                if start <= int(row["blockNumber"]) <= end
            ]
            return FakeResponse({"status": "1", "message": "OK", "result": rows})
        raise AssertionError(params)


def test_etherscan_checkpoint_scans_only_new_blocks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ETHERSCAN_API_KEY", "free-key")
    monkeypatch.setenv("ETHERSCAN_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("ETHERSCAN_CONFIRMATIONS", "0")
    monkeypatch.setenv("ETHERSCAN_STATE_DIR", str(tmp_path / "state"))
    contract = "0xcccccccccccccccccccccccccccccccccccccccc"

    initial = [
        {
            "blockNumber": "10",
            "hash": "mint-a",
            "logIndex": "0",
            "contractAddress": contract,
            "from": "0x0000000000000000000000000000000000000000",
            "to": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "value": "600000",
        },
        {
            "blockNumber": "11",
            "hash": "mint-b",
            "logIndex": "0",
            "contractAddress": contract,
            "from": "0x0000000000000000000000000000000000000000",
            "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "value": "400000",
        },
    ]
    first = fetch_etherscan(
        IncrementalEtherscanSession(latest_block=100, transfers=initial),
        chain_id="ethereum",
        chain_label="Ethereum",
        contract=contract,
    )
    assert first.details["checkpoint_mode"] == "full"
    assert first.holders == 2

    incremental_transfer = {
        "blockNumber": "105",
        "hash": "move",
        "logIndex": "0",
        "contractAddress": contract,
        "from": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "to": "0xdddddddddddddddddddddddddddddddddddddddd",
        "value": "100000",
    }
    second_session = IncrementalEtherscanSession(
        latest_block=110, transfers=[incremental_transfer]
    )
    second = fetch_etherscan(
        second_session,
        chain_id="ethereum",
        chain_label="Ethereum",
        contract=contract,
    )

    transfer_query = next(q for q in second_session.queries if q["action"] == "tokentx")
    assert transfer_query["startblock"] == 101
    assert transfer_query["endblock"] == 110
    assert second.details["checkpoint_mode"] == "incremental"
    assert second.details["transfers_scanned_this_run"] == 1
    assert second.holders == 3
