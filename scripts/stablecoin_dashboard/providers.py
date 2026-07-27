"""Current-state adapters for the four explicitly allowed data sources."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

import requests

from .http import DEFAULT_TIMEOUT_SECONDS
from .models import ChainSnapshot
from .storage import write_json_atomic

ETHERSCAN_API_URL = "https://api.etherscan.io/v2/api"
SOLSCAN_PLAYGROUND_URL = "https://pro-api.solscan.io/playground/token/meta"
DEFAULT_XRPL_RPC_URL = "https://xrplcluster.com/"
DEFAULT_STELLAR_HORIZON_URL = "https://horizon.stellar.org"

ETHEREUM_CHAIN_ID = "1"
ETHERSCAN_FREE_PAGE_SIZE = 1_000
ETHERSCAN_DEFAULT_MIN_INTERVAL_SECONDS = 0.36
ETHERSCAN_MAX_API_ATTEMPTS = 5
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
ERC20_DECIMALS_SELECTOR = "0x313ce567"
ERC20_TOTAL_SUPPLY_SELECTOR = "0x18160ddd"


class ProviderError(RuntimeError):
    """Raised when an upstream source returns incomplete or inconsistent data."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ProviderError(f"Missing required environment variable: {name}")
    return value


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProviderError(f"Invalid decimal value for {field}: {value!r}") from exc
    if not result.is_finite():
        raise ProviderError(f"Non-finite decimal value for {field}: {value!r}")
    return result


def _integer(value: Any, field: str, *, base: int = 10) -> int:
    try:
        result = int(str(value), base)
    except (TypeError, ValueError) as exc:
        raise ProviderError(f"Invalid integer value for {field}: {value!r}") from exc
    return result


def _safe_error_body(response: requests.Response) -> str:
    try:
        body = response.text.strip()
    except Exception:  # pragma: no cover - defensive against unusual response mocks
        return ""
    return body[:500]


def _get_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[dict[str, Any], requests.Response]:
    try:
        response = session.get(
            url,
            params=params,
            headers=headers,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        response = getattr(exc, "response", None)
        suffix = f"; response={_safe_error_body(response)}" if response is not None else ""
        raise ProviderError(f"HTTP GET failed for {url}: {exc}{suffix}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError(f"Non-JSON response from {url}: {_safe_error_body(response)}") from exc
    if not isinstance(payload, dict):
        raise ProviderError(f"Unexpected JSON shape from {url}: expected object")
    return payload, response


class _EtherscanClient:
    """Small Etherscan V2 client compatible with Free-tier limits.

    Etherscan may return rate-limit errors inside a HTTP 200 JSON response, so retries
    must happen above the requests/urllib3 layer as well. The default delay stays under
    the documented three-calls-per-second Free-tier ceiling.
    """

    def __init__(self, session: requests.Session, api_key: str) -> None:
        self.session = session
        self.api_key = api_key
        try:
            self.min_interval = max(
                0.0,
                float(
                    os.getenv(
                        "ETHERSCAN_MIN_INTERVAL_SECONDS",
                        str(ETHERSCAN_DEFAULT_MIN_INTERVAL_SECONDS),
                    )
                ),
            )
        except ValueError as exc:
            raise ProviderError("ETHERSCAN_MIN_INTERVAL_SECONDS must be numeric") from exc
        self._last_call_at = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    def call(self, *, allow_no_transactions: bool = False, **params: Any) -> dict[str, Any]:
        query = {"chainid": ETHEREUM_CHAIN_ID, "apikey": self.api_key, **params}
        last_message = "unknown Etherscan error"

        for attempt in range(ETHERSCAN_MAX_API_ATTEMPTS):
            self._throttle()
            payload, _ = _get_json(self.session, ETHERSCAN_API_URL, params=query)
            self._last_call_at = time.monotonic()

            if "result" not in payload:
                raise ProviderError(f"Malformed Etherscan response: {payload}")

            status = str(payload.get("status", "1"))
            message = f"{payload.get('message', '')} {payload.get('result', '')}".strip()
            lowered = message.lower()

            if status != "0":
                return payload
            if allow_no_transactions and "no transactions found" in lowered:
                return {**payload, "result": []}

            last_message = message or "Etherscan returned status=0"
            is_rate_limit = any(
                marker in lowered
                for marker in ("rate limit", "max rate", "too many requests", "throttle")
            )
            if is_rate_limit and attempt + 1 < ETHERSCAN_MAX_API_ATTEMPTS:
                time.sleep(min(8.0, 1.0 * (2**attempt)))
                continue
            raise ProviderError(f"Etherscan API error: {last_message}")

        raise ProviderError(f"Etherscan API error after retries: {last_message}")


def _etherscan_hex_result(payload: dict[str, Any], field: str) -> int:
    raw = payload.get("result")
    if not isinstance(raw, str) or not raw.startswith("0x") or raw == "0x":
        raise ProviderError(f"Invalid Etherscan hex result for {field}: {raw!r}")
    return _integer(raw[2:], field, base=16)


def _iter_etherscan_transfers(
    client: _EtherscanClient,
    *,
    contract: str,
    start_block: int,
    end_block: int,
) -> Iterator[dict[str, Any]]:
    """Yield ERC-20 transfers in a fixed block range using Free-tier pagination."""
    if start_block < 0 or end_block < start_block:
        return

    page_size = ETHERSCAN_FREE_PAGE_SIZE
    page = 1
    previous_fingerprint: tuple[str, str, str] | None = None
    max_pages = _integer(os.getenv("ETHERSCAN_MAX_TRANSFER_PAGES", "10000"), "max pages")

    while page <= max_pages:
        payload = client.call(
            allow_no_transactions=True,
            module="account",
            action="tokentx",
            contractaddress=contract,
            startblock=start_block,
            endblock=end_block,
            page=page,
            offset=page_size,
            sort="asc",
        )
        rows = payload.get("result")
        if not isinstance(rows, list):
            raise ProviderError(f"Etherscan tokentx result is not a list: {rows!r}")
        if not rows:
            return

        last = rows[-1]
        if not isinstance(last, dict):
            raise ProviderError("Etherscan tokentx returned a non-object transfer")
        fingerprint = (
            str(last.get("hash", "")),
            str(last.get("logIndex", last.get("transactionIndex", ""))),
            str(last.get("blockNumber", "")),
        )
        if fingerprint == previous_fingerprint:
            raise ProviderError(f"Etherscan pagination repeated page {page} for {contract}")
        previous_fingerprint = fingerprint

        for row in rows:
            if not isinstance(row, dict):
                raise ProviderError("Etherscan tokentx returned a non-object transfer")
            row_contract = str(row.get("contractAddress", "")).lower()
            if row_contract and row_contract != contract.lower():
                raise ProviderError(
                    f"Etherscan returned transfer for another contract: {row_contract}"
                )
            block_number = _integer(row.get("blockNumber"), "transfer block number")
            if block_number < start_block or block_number > end_block:
                raise ProviderError(
                    f"Etherscan returned transfer from block {block_number} outside "
                    f"{start_block}..{end_block}"
                )
            yield row

        if len(rows) < page_size:
            return
        page += 1

    raise ProviderError(
        f"Etherscan transfer pagination exceeded {max_pages} pages for {contract}; "
        "raise ETHERSCAN_MAX_TRANSFER_PAGES only after investigating"
    )


def _address_key(address: str) -> str:
    """Store a stable pseudonymous key instead of publishing wallet addresses in git."""
    return hashlib.sha256(address.lower().encode("ascii")).hexdigest()


def _apply_erc20_transfers(
    balances: dict[str, int],
    transfers: Iterator[dict[str, Any]],
) -> int:
    transfer_count = 0
    for transfer in transfers:
        sender = str(transfer.get("from", "")).lower()
        recipient = str(transfer.get("to", "")).lower()
        if not sender.startswith("0x") or not recipient.startswith("0x"):
            raise ProviderError(f"Malformed Etherscan transfer addresses: {transfer}")
        value = _integer(transfer.get("value"), "ERC-20 transfer value")
        if value < 0:
            raise ProviderError(f"Negative ERC-20 transfer value: {value}")

        if sender != ZERO_ADDRESS:
            key = _address_key(sender)
            balances[key] = balances.get(key, 0) - value
        if recipient != ZERO_ADDRESS:
            key = _address_key(recipient)
            balances[key] = balances.get(key, 0) + value
        transfer_count += 1

    negative = {key: balance for key, balance in balances.items() if balance < 0}
    if negative:
        key, balance = next(iter(negative.items()))
        raise ProviderError(
            f"Negative reconstructed ERC-20 balance for state key {key}: {balance}; "
            "transfer data is incomplete or inconsistent"
        )
    for key in [key for key, balance in balances.items() if balance == 0]:
        del balances[key]
    return transfer_count


def _replay_erc20_balances(
    transfers: Iterator[dict[str, Any]],
) -> tuple[dict[str, int], int]:
    """Compatibility helper used by tests and full replays."""
    balances: dict[str, int] = {}
    transfer_count = _apply_erc20_transfers(balances, transfers)
    return balances, transfer_count


def _etherscan_state_path(contract: str) -> Path | None:
    root = os.getenv("ETHERSCAN_STATE_DIR", "").strip()
    if not root:
        return None
    return Path(root) / f"{contract.lower()}.json"


def _load_etherscan_state(path: Path | None, contract: str) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        if payload.get("schema_version") != 1:
            return None
        if str(payload.get("contract", "")).lower() != contract.lower():
            return None
        block = _integer(payload.get("block"), "Etherscan state block")
        decimals = _integer(payload.get("decimals"), "Etherscan state decimals")
        raw_balances = payload.get("balances")
        if block < 0 or not 0 <= decimals <= 255 or not isinstance(raw_balances, dict):
            return None
        balances: dict[str, int] = {}
        for key, value in raw_balances.items():
            balance = _integer(value, "Etherscan cached balance")
            if len(str(key)) != 64 or balance <= 0:
                return None
            balances[str(key)] = balance
        return {
            "block": block,
            "decimals": decimals,
            "balances": balances,
            "transfer_count": max(0, _integer(payload.get("transfer_count", 0), "transfer count")),
        }
    except (OSError, ValueError, json.JSONDecodeError, ProviderError):
        # A corrupt checkpoint is only an optimisation failure: rebuild from block zero.
        return None


def _write_etherscan_state(
    path: Path | None,
    *,
    contract: str,
    block: int,
    decimals: int,
    balances: dict[str, int],
    transfer_count: int,
) -> None:
    if path is None:
        return
    write_json_atomic(
        path,
        {
            "schema_version": 1,
            "contract": contract.lower(),
            "block": block,
            "decimals": decimals,
            "transfer_count": transfer_count,
            "updated_at": utc_now_iso(),
            "address_storage": "sha256(lowercase_address)",
            "balances": {key: str(value) for key, value in sorted(balances.items())},
        },
    )


def _full_etherscan_replay(
    client: _EtherscanClient,
    *,
    contract: str,
    target_block: int,
) -> tuple[dict[str, int], int]:
    return _replay_erc20_balances(
        _iter_etherscan_transfers(
            client,
            contract=contract,
            start_block=0,
            end_block=target_block,
        )
    )


def fetch_etherscan(
    session: requests.Session,
    *,
    chain_id: str,
    chain_label: str,
    contract: str,
) -> ChainSnapshot:
    """Fetch a coherent current ERC-20 snapshot with an Etherscan Free-tier key.

    Holder endpoints are PRO-only. The first run reconstructs balances from Transfer
    events; later runs reuse a committed pseudonymous checkpoint and request only the
    new block range. A cache mismatch triggers one automatic full rebuild.
    """
    client = _EtherscanClient(session, _required_env("ETHERSCAN_API_KEY"))

    latest_payload = client.call(module="proxy", action="eth_blockNumber")
    latest_block = _etherscan_hex_result(latest_payload, "latest Ethereum block")
    confirmations = max(
        0,
        _integer(os.getenv("ETHERSCAN_CONFIRMATIONS", "12"), "Etherscan confirmations"),
    )
    target_block = max(0, latest_block - confirmations)
    block_tag = hex(target_block)

    decimals_payload = client.call(
        module="proxy",
        action="eth_call",
        to=contract,
        data=ERC20_DECIMALS_SELECTOR,
        tag=block_tag,
    )
    decimals = _etherscan_hex_result(decimals_payload, f"decimals() for {contract}")
    if decimals < 0 or decimals > 255:
        raise ProviderError(f"Implausible ERC-20 decimals for {contract}: {decimals}")

    supply_payload = client.call(
        module="proxy",
        action="eth_call",
        to=contract,
        data=ERC20_TOTAL_SUPPLY_SELECTOR,
        tag=block_tag,
    )
    raw_supply = _etherscan_hex_result(supply_payload, f"totalSupply() for {contract}")

    state_path = _etherscan_state_path(contract)
    state = _load_etherscan_state(state_path, contract)
    mode = "full"
    scanned_from = 0
    scanned_transfer_count = 0

    if state is not None and state["block"] <= target_block and state["decimals"] == decimals:
        mode = "incremental"
        balances = dict(state["balances"])
        scanned_from = state["block"] + 1
        if scanned_from <= target_block:
            scanned_transfer_count = _apply_erc20_transfers(
                balances,
                _iter_etherscan_transfers(
                    client,
                    contract=contract,
                    start_block=scanned_from,
                    end_block=target_block,
                ),
            )
        total_transfer_count = state["transfer_count"] + scanned_transfer_count
    else:
        balances, total_transfer_count = _full_etherscan_replay(
            client, contract=contract, target_block=target_block
        )
        scanned_transfer_count = total_transfer_count

    reconstructed_supply = sum(balances.values())
    if reconstructed_supply != raw_supply and mode == "incremental":
        # State can become stale after an interrupted write, manual edit, or rare reorg.
        # A full replay is expensive but restores correctness without blocking forever.
        mode = "full_rebuild_after_mismatch"
        scanned_from = 0
        balances, total_transfer_count = _full_etherscan_replay(
            client, contract=contract, target_block=target_block
        )
        scanned_transfer_count = total_transfer_count
        reconstructed_supply = sum(balances.values())

    if reconstructed_supply != raw_supply:
        raise ProviderError(
            f"Etherscan transfer replay mismatch for {contract} at block {target_block}: "
            f"balances={reconstructed_supply}, totalSupply={raw_supply}"
        )

    # Avoid rewriting the large checkpoint on quiet runs. Keeping the previous scanned
    # block only causes one cheap empty-range query next time and greatly reduces git churn.
    should_write_state = state is None or mode != "incremental" or scanned_transfer_count > 0
    if should_write_state:
        _write_etherscan_state(
            state_path,
            contract=contract,
            block=target_block,
            decimals=decimals,
            balances=balances,
            transfer_count=total_transfer_count,
        )

    holders = sum(1 for balance in balances.values() if balance > 0)
    supply = Decimal(raw_supply) / (Decimal(10) ** decimals)

    return ChainSnapshot(
        chain_id=chain_id,
        chain_label=chain_label,
        supply=supply,
        holders=holders,
        source="Etherscan API V2 (Free tier)",
        reference=contract,
        observed_at=utc_now_iso(),
        details={
            "block": target_block,
            "latest_block_at_run": latest_block,
            "confirmations": confirmations,
            "supply_method": "proxy.eth_call totalSupply()",
            "holders_method": "account.tokentx Transfer replay with incremental checkpoint",
            "decimals_method": "proxy.eth_call decimals()",
            "decimals": decimals,
            "checkpoint_mode": mode,
            "scanned_from_block": scanned_from,
            "transfers_scanned_this_run": scanned_transfer_count,
            "transfer_count": total_transfer_count,
            "page_size": ETHERSCAN_FREE_PAGE_SIZE,
        },
    )


def fetch_solscan(
    session: requests.Session,
    *,
    chain_id: str,
    chain_label: str,
    mint: str,
) -> ChainSnapshot:
    """Fetch one current Solana snapshot from the Solscan free playground."""
    api_key = _required_env("SOLSCAN_API_KEY")
    payload, _ = _get_json(
        session,
        SOLSCAN_PLAYGROUND_URL,
        params={"address": mint},
        headers={"token": api_key},
    )
    if payload.get("success") is not True or not isinstance(payload.get("data"), dict):
        raise ProviderError(f"Solscan API error for {mint}: {payload}")

    data = payload["data"]
    decimals = _integer(data.get("decimals"), "Solscan decimals")
    holders = _integer(data.get("holder"), "Solscan holder count")
    raw_supply = _decimal(data.get("supply"), "Solscan token supply")
    if decimals < 0 or decimals > 255:
        raise ProviderError(f"Implausible Solscan decimals for {mint}: {decimals}")
    if raw_supply < 0 or holders < 0:
        raise ProviderError(f"Negative Solscan token metadata for {mint}: {data}")

    supply = raw_supply / (Decimal(10) ** decimals)
    return ChainSnapshot(
        chain_id=chain_id,
        chain_label=chain_label,
        supply=supply,
        holders=holders,
        source="Solscan API Playground (Free tier)",
        reference=mint,
        observed_at=utc_now_iso(),
        details={
            "method": "playground/token/meta",
            "decimals": decimals,
        },
    )


def _xrpl_call(
    session: requests.Session,
    endpoint: str,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    try:
        response = session.post(
            endpoint,
            json={"method": method, "params": [params]},
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        response = getattr(exc, "response", None)
        suffix = f"; response={_safe_error_body(response)}" if response is not None else ""
        raise ProviderError(f"XRPL request failed for {endpoint}: {exc}{suffix}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError(f"Non-JSON response from XRPL endpoint {endpoint}") from exc

    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        raise ProviderError(f"Malformed XRPL response: {payload}")
    if result.get("status") == "error" or result.get("error"):
        raise ProviderError(
            f"XRPL {method} error: {result.get('error_message') or result.get('error')}"
        )
    return result


def _matches_xrpl_currency(value: str, currency_hex: str, currency_text: str) -> bool:
    normalized = value.upper()
    return normalized in {currency_hex.upper(), currency_text.upper()}


def fetch_xrpl(
    session: requests.Session,
    *,
    chain_id: str,
    chain_label: str,
    issuer: str,
    currency_hex: str,
    currency_text: str,
) -> ChainSnapshot:
    """Count positive peer balances from issuer-side trust lines at one ledger."""
    endpoint = os.getenv("XRPL_RPC_URL", DEFAULT_XRPL_RPC_URL).strip()
    if not endpoint:
        raise ProviderError("XRPL_RPC_URL is empty")

    marker: Any | None = None
    ledger_hash: str | None = None
    ledger_index: int | str | None = None
    supply = Decimal(0)
    holder_accounts: set[str] = set()
    seen_lines: set[tuple[str, str]] = set()
    seen_markers: set[str] = set()
    page_count = 0

    while True:
        page_count += 1
        if page_count > 10_000:
            raise ProviderError("XRPL pagination exceeded 10,000 pages")

        params: dict[str, Any] = {"account": issuer, "limit": 400}
        if ledger_hash:
            params["ledger_hash"] = ledger_hash
        elif ledger_index is not None:
            params["ledger_index"] = ledger_index
        else:
            params["ledger_index"] = "validated"
        if marker is not None:
            params["marker"] = marker

        result = _xrpl_call(session, endpoint, "account_lines", params)
        result_hash = result.get("ledger_hash")
        result_index = result.get("ledger_index")
        if ledger_hash is None:
            ledger_hash = str(result_hash) if result_hash else None
            ledger_index = result_index
            if ledger_hash is None and ledger_index is None:
                raise ProviderError("XRPL response did not identify the validated ledger")
        elif result_hash and str(result_hash) != ledger_hash:
            raise ProviderError("XRPL ledger changed during account_lines pagination")

        lines = result.get("lines", [])
        if not isinstance(lines, list):
            raise ProviderError("XRPL account_lines returned a non-list `lines` value")

        for line_index, line in enumerate(lines):
            if not isinstance(line, dict):
                raise ProviderError("XRPL account_lines returned a non-object line")
            currency = str(line.get("currency", ""))
            if not _matches_xrpl_currency(currency, currency_hex, currency_text):
                continue
            account = str(line.get("account", "")).strip()
            identity = (account or f"missing:{page_count}:{line_index}", currency.upper())
            if identity in seen_lines:
                continue
            seen_lines.add(identity)

            balance = _decimal(line.get("balance", "0"), "XRPL trust-line balance")
            if balance < 0:
                supply += -balance
                holder_accounts.add(account or f"unknown:{len(holder_accounts)}")

        marker = result.get("marker")
        if marker is None:
            break
        marker_key = repr(marker)
        if marker_key in seen_markers:
            raise ProviderError("XRPL pagination marker repeated")
        seen_markers.add(marker_key)

    ledger_reference = ledger_hash or str(ledger_index)
    return ChainSnapshot(
        chain_id=chain_id,
        chain_label=chain_label,
        supply=supply,
        holders=len(holder_accounts),
        source="XRPL public JSON-RPC (XRPLCluster)",
        reference=f"{issuer}:{currency_text}",
        observed_at=utc_now_iso(),
        details={
            "method": "account_lines",
            "ledger": ledger_reference,
            "pages": page_count,
            "holder_rule": "issuer-side balance < 0",
        },
    )


def _stellar_asset_record(
    session: requests.Session,
    endpoint: str,
    asset_code: str,
    issuer: str,
) -> tuple[dict[str, Any], str | None]:
    payload, response = _get_json(
        session,
        f"{endpoint}/assets",
        params={"asset_code": asset_code, "asset_issuer": issuer, "limit": 10},
    )
    embedded = payload.get("_embedded")
    records = embedded.get("records", []) if isinstance(embedded, dict) else []
    if not isinstance(records, list):
        raise ProviderError("Horizon assets response has no records list")
    exact = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("asset_code") == asset_code
        and record.get("asset_issuer") == issuer
    ]
    if len(exact) != 1:
        raise ProviderError(
            f"Expected one Horizon asset record for {asset_code}:{issuer}; found {len(exact)}"
        )
    return exact[0], response.headers.get("X-Last-Ledger")


def _stellar_supply(asset: dict[str, Any]) -> Decimal:
    balances = asset.get("balances", {})
    if not isinstance(balances, dict):
        raise ProviderError("Horizon asset `balances` is not an object")

    supply = sum(
        (_decimal(value, "Stellar asset balance") for value in balances.values()), Decimal(0)
    )
    for field in (
        "claimable_balances_amount",
        "contracts_amount",
        "liquidity_pools_amount",
    ):
        supply += _decimal(asset.get(field, "0"), f"Stellar {field}")
    if supply < 0:
        raise ProviderError("Horizon returned a negative aggregate asset supply")
    return supply


def _stellar_positive_account_holders(
    session: requests.Session,
    endpoint: str,
    asset_code: str,
    issuer: str,
) -> tuple[int, list[str]]:
    """Count classic accounts with a strictly positive balance for the asset."""
    url = f"{endpoint}/accounts"
    params: dict[str, Any] | None = {
        "asset": f"{asset_code}:{issuer}",
        "limit": 200,
        "order": "asc",
    }
    positive_accounts: set[str] = set()
    ledgers: list[str] = []
    seen_urls: set[str] = set()
    page_count = 0

    while url:
        page_count += 1
        if page_count > 10_000:
            raise ProviderError("Horizon accounts pagination exceeded 10,000 pages")
        request_key = f"{url}|{params!r}"
        if request_key in seen_urls:
            raise ProviderError("Horizon accounts pagination repeated a page")
        seen_urls.add(request_key)

        payload, response = _get_json(session, url, params=params)
        if response.headers.get("X-Last-Ledger"):
            ledgers.append(response.headers["X-Last-Ledger"])
        embedded = payload.get("_embedded")
        records = embedded.get("records", []) if isinstance(embedded, dict) else []
        if not isinstance(records, list):
            raise ProviderError("Horizon accounts response has no records list")

        for account in records:
            if not isinstance(account, dict):
                raise ProviderError("Horizon accounts response contains a non-object record")
            account_id = account.get("account_id") or account.get("id")
            if not account_id:
                continue
            balances = account.get("balances", [])
            if not isinstance(balances, list):
                raise ProviderError("Horizon account balances is not a list")
            for balance in balances:
                if not isinstance(balance, dict):
                    continue
                if (
                    balance.get("asset_code") == asset_code
                    and balance.get("asset_issuer") == issuer
                    and _decimal(balance.get("balance", "0"), "Stellar account balance") > 0
                ):
                    positive_accounts.add(str(account_id))
                    break

        next_url = payload.get("_links", {}).get("next", {}).get("href")
        if not next_url or not records:
            break
        url = str(next_url)
        params = None

    return len(positive_accounts), ledgers


def fetch_stellar(
    session: requests.Session,
    *,
    chain_id: str,
    chain_label: str,
    issuer: str,
    asset_code: str,
) -> ChainSnapshot:
    """Fetch official Horizon aggregate supply and current positive holders."""
    endpoint = os.getenv("STELLAR_HORIZON_URL", DEFAULT_STELLAR_HORIZON_URL).rstrip("/")
    if not endpoint:
        raise ProviderError("STELLAR_HORIZON_URL is empty")

    asset, asset_ledger = _stellar_asset_record(session, endpoint, asset_code, issuer)
    supply = _stellar_supply(asset)
    account_holders, account_ledgers = _stellar_positive_account_holders(
        session, endpoint, asset_code, issuer
    )

    contract_holders = _integer(asset.get("num_contracts", 0) or 0, "Stellar contracts")
    liquidity_pool_holders = _integer(
        asset.get("num_liquidity_pools", 0) or 0, "Stellar liquidity pools"
    )
    if contract_holders < 0 or liquidity_pool_holders < 0:
        raise ProviderError("Horizon returned a negative holder-object count")
    holders = account_holders + contract_holders + liquidity_pool_holders

    ledger_values = [value for value in [asset_ledger, *account_ledgers] if value]
    ledger_range = None
    if ledger_values:
        numeric = [_integer(value, "Stellar ledger") for value in ledger_values]
        ledger_range = f"{min(numeric)}-{max(numeric)}"

    return ChainSnapshot(
        chain_id=chain_id,
        chain_label=chain_label,
        supply=supply,
        holders=holders,
        source="Stellar Horizon (SDF mainnet)",
        reference=f"{asset_code}:{issuer}",
        observed_at=utc_now_iso(),
        details={
            "asset_method": "GET /assets",
            "account_holder_method": "GET /accounts?asset=...",
            "ledger_range": ledger_range,
            "classic_account_holders": account_holders,
            "contract_holders": contract_holders,
            "liquidity_pool_holders": liquidity_pool_holders,
            "claimable_balances_in_supply": True,
            "claimable_balances_in_holder_count": False,
        },
    )
