"""Static token and chain configuration.

Only identifiers that define the tracked assets live here. API credentials are read
from environment variables by the providers.
"""

from __future__ import annotations

from typing import Final

TOKENS: Final[dict[str, dict]] = {
    "eurcv": {
        "name": "EUR CoinVertible",
        "symbol": "EURCV",
        "currency": "EUR",
        "chains": [
            {
                "id": "ethereum",
                "label": "Ethereum",
                "provider": "etherscan",
                "asset": "0x5F7827FDeb7c20b443265Fc2F40845B715385Ff2",
            },
            {
                "id": "solana",
                "label": "Solana",
                "provider": "solscan",
                "asset": "DghpMkatCiUsofbTmid3M3kAbDTPqDwKiYHnudXeGG52",
            },
            {
                "id": "xrpl",
                "label": "XRP Ledger",
                "provider": "xrpl",
                "issuer": "rUNaS5sqRuxZz6V7rBGhoSaZiVYA3ut4UL",
                "currency": "4555524356000000000000000000000000000000",
                "currency_text": "EURCV",
            },
            {
                "id": "stellar",
                "label": "Stellar",
                "provider": "stellar",
                "issuer": "GCEYGIVOLAVBF2TG2RUSGTUJCIN75KEX3NGLMY4VPL4GFE5L355AXW3G",
                "asset_code": "EURCV",
            },
        ],
    },
    "usdcv": {
        "name": "USD CoinVertible",
        "symbol": "USDCV",
        "currency": "USD",
        "chains": [
            {
                "id": "ethereum",
                "label": "Ethereum",
                "provider": "etherscan",
                "asset": "0x5422374B27757da72d5265cC745ea906E0446634",
            },
            {
                "id": "solana",
                "label": "Solana",
                "provider": "solscan",
                "asset": "8smindLdDuySY6i2bStQX9o8DVhALCXCMbNxD98unx35",
            },
        ],
    },
}
