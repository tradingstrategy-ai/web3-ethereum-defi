"""ForgeYields vault offchain metadata.

- ForgeYields is a cross-chain yield aggregator — most TVL sits on Starknet and
  other chains, aggregated into strategies on Ethereum
- The Ethereum TokenGateway contract only holds a small residual balance;
  ``totalAssets()`` reverts and ``convertToAssets(totalSupply())`` returns
  only the gateway's share, not the true cross-chain AUM
- The canonical TVL and APY come from the ForgeYields proprietary API at
  ``https://api.forgeyields.com/strategies``
- We reverse-engineered the API endpoint from the Next.js app at
  ``app.forgeyields.com``
- Offchain fetching is disabled: existing disk metadata is retained without
  expiry or modification because the upstream API is no longer working

API response structure
~~~~~~~~~~~~~~~~~~~~~~

``GET /strategies`` returns a JSON array. Each element has:

.. code-block:: json

    {
        "name": "ForgeYields USDC",
        "symbol": "fyUSDC",
        "token_gateway_per_domain": [
            {"domain": "ethereum", "token_gateway": "0x943109..."},
            {"domain": "starknet", "token_gateway": "0x07fDce..."}
        ],
        "integrationInfo": {
            "overallUsdPrice": "1085984.11",
            "overallApy": "25.07",
            "positionExpositions": [...]
        }
    }

We index strategies by their Ethereum gateway address (lowercased) so the
vault scanner can look up TVL for each known on-chain vault.
"""

import datetime
import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

from eth_typing import HexAddress

from eth_defi.disk_cache import DEFAULT_CACHE_ROOT

#: Where we cache fetched ForgeYields metadata files
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "forgeyields"

#: ForgeYields API base URL, reverse-engineered from their Next.js frontend
DEFAULT_API_BASE_URL = "https://api.forgeyields.com"

logger = logging.getLogger(__name__)


class ForgeYieldsVaultMetadata(TypedDict):
    """Metadata about a ForgeYields vault from the offchain strategies API.

    Fetched from ``api.forgeyields.com/strategies``.
    Discovered by reverse-engineering the ForgeYields Next.js app JavaScript bundles.

    Each strategy has deposit gateways on multiple chains (Ethereum, Starknet, etc.)
    but the ``overallUsdPrice`` and ``overallApy`` represent the cross-chain total.
    """

    #: Strategy name, e.g. ``"ForgeYields USDC"``
    name: str

    #: Share token symbol, e.g. ``"fyUSDC"``
    symbol: str

    #: Total cross-chain TVL in USD.
    #:
    #: Informational only. The pipeline uses ``tvl`` (denomination-token units)
    #: for ``total_assets`` and ``fetch_nav()``.
    tvl_usd: Decimal

    #: Total cross-chain TVL in denomination token units (ETH, USDC, WBTC).
    #:
    #: This is the value that should be written to ``total_assets`` in the
    #: price parquet. Read from the top-level ``tvl`` field in the API response.
    tvl: Decimal

    #: Overall APY as a percentage, e.g. ``25.07`` for 25.07%
    apy: float | None

    #: Ethereum gateway contract address (checksummed)
    ethereum_gateway: str | None


def _read_cached_strategies(file: Path) -> dict[str, ForgeYieldsVaultMetadata]:
    """Load the last successful strategy snapshot without changing its age.

    Scanner metadata and valuation lookups use the same Decimal conversion.
    A missing snapshot returns an empty mapping; corrupt stored data remains
    an explicit error instead of being mistaken for a valid zero-TVL response.

    :param file: Shared JSON snapshot written by the strategy fetcher.
    :return: Strategy metadata keyed by lower-case Ethereum gateway address.
    """
    if not file.exists() or file.stat().st_size == 0:
        return {}
    with file.open() as source:
        serialised = json.load(source)
    for metadata in serialised.values():
        metadata["tvl_usd"] = Decimal(metadata["tvl_usd"])
        metadata["tvl"] = Decimal(metadata.get("tvl", "0"))
    return serialised


def fetch_forgeyields_strategies(
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = datetime.timedelta(days=2),
) -> dict[str, ForgeYieldsVaultMetadata]:
    """Read the retained ForgeYields metadata snapshot without refreshing it.

    The `ForgeYields strategies API <https://api.forgeyields.com/strategies>`__
    and its own app failed on 3 October 2026. Metadata and TVL callers must
    retain the last successful snapshot indefinitely without issuing requests,
    updating its modification time, or overwriting it with empty data. Values
    read here are retained metadata, not fresh API observations. A missing
    snapshot returns an empty mapping and never creates a replacement file.

    :param cache_path: Directory containing ``forgeyields_strategies.json``.
    :param api_base_url: Retained for caller compatibility; fetching is disabled.
    :param now_: Retained for caller compatibility; snapshot age is ignored.
    :param max_cache_duration: Retained for caller compatibility; snapshots do not expire.
    :return: Retained metadata keyed by lower-case Ethereum gateway address.
    """
    del api_base_url, now_, max_cache_duration
    # no longer working: disable offchain refreshes and preserve the existing copy.
    return _read_cached_strategies(cache_path / "forgeyields_strategies.json")


def fetch_forgeyields_vault_metadata(vault_address: HexAddress) -> ForgeYieldsVaultMetadata | None:
    """Look up an Ethereum gateway in the retained metadata snapshot.

    Vault adapters cache the returned entry for their lifetime. No network
    request or cache rewrite occurs, including when the gateway is unknown.

    :param vault_address: Ethereum TokenGateway contract address.
    :return: Retained metadata, or None when no matching snapshot entry exists.
    """
    return fetch_forgeyields_strategies().get(vault_address.lower())


class ForgeYieldsHistoryEntry(TypedDict):
    """A single daily TVL/APR snapshot from the ForgeYields ``historyReports`` array."""

    #: Naive UTC datetime
    timestamp: datetime.datetime

    #: TVL in denomination token units (ETH, USDC, WBTC)
    tvl: float

    #: TVL in USD
    tvl_usd: float

    #: APR at this point (percentage, e.g. 13.14 for 13.14%)
    apr: float

    #: Denomination token USD price at this point
    underlying_price: float


class ForgeYieldsStrategyHistory(TypedDict):
    """Historical data for a single ForgeYields strategy."""

    #: Strategy name, e.g. ``"ForgeYields USDC"``
    name: str

    #: Share token symbol, e.g. ``"fyUSDC"``
    symbol: str

    #: Denomination token symbol, e.g. ``"USDC"``
    underlying_symbol: str

    #: Ethereum gateway address (checksummed)
    ethereum_gateway: str | None

    #: Daily snapshots, oldest first
    history: list[ForgeYieldsHistoryEntry]


def fetch_forgeyields_history(
    api_base_url: str = DEFAULT_API_BASE_URL,
) -> list[ForgeYieldsStrategyHistory]:
    """Reject unavailable offchain history backfills before any data is changed.

    The retained metadata snapshot does not contain the raw ``historyReports``
    needed by the one-shot backfill script. An explicit error keeps existing
    price history intact instead of pretending that an empty response is a
    successful backfill. Re-enable only after the upstream feed is reviewed.

    :param api_base_url: Retained for caller compatibility; no request is issued.
    :return: No history is returned while fetching is disabled.
    :raises RuntimeError: Offchain history fetching is disabled.
    """
    del api_base_url
    # no longer working: do not request the broken offchain history endpoint.
    raise RuntimeError("ForgeYields offchain history is disabled: no longer working; existing metadata and prices are preserved")
