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
- Shared disk cache: two-day metadata TTL, one-hour TVL TTL, and a one-hour
  failed-refresh cooldown across scanner workers and restarts

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
from json import JSONDecodeError
from pathlib import Path
from typing import TypedDict

import requests
from atomicwrites import atomic_write
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.utils import wait_other_writers

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


def _parse_strategy(raw: dict) -> ForgeYieldsVaultMetadata:
    """Parse a single strategy entry from the API response.

    :param raw:
        Raw JSON dict from ``/strategies``
    """
    # Find the Ethereum gateway address
    ethereum_gateway = None
    for gw in raw.get("token_gateway_per_domain", []):
        if gw.get("domain") == "ethereum":
            addr = gw.get("token_gateway")
            if addr and len(addr) == 42:
                ethereum_gateway = Web3.to_checksum_address(addr)
            break

    info = raw.get("integrationInfo", {})

    apy_raw = info.get("overallApy")
    apy = float(apy_raw) if apy_raw is not None else None

    tvl_usd_raw = info.get("overallUsdPrice", "0")
    tvl_usd = Decimal(str(tvl_usd_raw))

    tvl_raw = raw.get("tvl", "0")
    tvl = Decimal(str(tvl_raw))

    return ForgeYieldsVaultMetadata(
        name=raw.get("name", ""),
        symbol=raw.get("symbol", ""),
        tvl_usd=tvl_usd,
        tvl=tvl,
        apy=apy,
        ethereum_gateway=ethereum_gateway,
    )


def _read_cached_strategies(file: Path) -> dict[str, ForgeYieldsVaultMetadata]:
    """Load the last successful strategy snapshot without changing its age.

    Both normal reads and outage fallback use the same Decimal conversion.
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
    retry_cooldown: datetime.timedelta = datetime.timedelta(hours=1),
) -> dict[str, ForgeYieldsVaultMetadata]:
    """Fetch all ForgeYields strategies with shared outage backoff.

    ``ForgeYieldsVault.fetch_tvl()`` calls this for each historical valuation,
    using a one-hour success TTL; metadata callers use two days. An HTTP 500
    previously left the success snapshot expired, so every subsequent row
    retried the same failing endpoint. The separate retry deadline bounds
    failures without touching the successful snapshot's modification time or
    making stale TVL appear freshly fetched. The existing file lock protects
    both records across threads, processes and scanner restarts.

    The source is the `ForgeYields strategies API
    <https://api.forgeyields.com/strategies>`__. During an outage, callers receive
    the last successful snapshot or an empty mapping if none exists. Neither
    an empty API response nor a failed refresh overwrites successful data.

    :param cache_path: Directory containing the strategy snapshot and retry deadline.
    :param api_base_url: API origin; the shared request reads ``/strategies``.
    :param now_: Naive UTC clock override for deterministic tests.
    :param max_cache_duration: Maximum successful snapshot age; default two days.
    :param retry_cooldown: Time between failed refresh attempts; default one hour.
    :return: Metadata keyed by lower-case Ethereum gateway address.
    """
    assert isinstance(cache_path, Path), "cache_path must be Path instance"
    assert retry_cooldown > datetime.timedelta(0), "retry_cooldown must be positive"
    cache_path.mkdir(parents=True, exist_ok=True)
    file = (cache_path / "forgeyields_strategies.json").resolve()
    retry_file = file.with_suffix(".retry-after")
    now_ = now_ if now_ is not None else native_datetime_utc_now()

    with wait_other_writers(file):
        # Inspect freshness after acquiring the lock: another worker may have
        # refreshed the snapshot while this caller was waiting for its turn.
        if file.exists() and file.stat().st_size > 0 and now_ - native_datetime_utc_fromtimestamp(file.stat().st_mtime) <= max_cache_duration:
            return _read_cached_strategies(file)
        if retry_file.exists() and now_ < datetime.datetime.fromisoformat(retry_file.read_text()):
            logger.debug("ForgeYields refresh deferred until %s", retry_file.read_text())
            return _read_cached_strategies(file)

        result: dict[str, ForgeYieldsVaultMetadata] = {}
        url = f"{api_base_url}/strategies"
        try:
            response = requests.get(url, headers={"Content-Type": "application/json"}, timeout=30)
            response.raise_for_status()
            for raw in response.json():
                entry = _parse_strategy(raw)
                if entry["ethereum_gateway"]:
                    result[entry["ethereum_gateway"].lower()] = entry
        except (requests.RequestException, JSONDecodeError) as error:
            logger.warning("ForgeYields refresh failed; retry after %s: %s", now_ + retry_cooldown, error)
        else:
            if not result:
                logger.warning("ForgeYields returned no Ethereum strategies; retaining the previous snapshot and retrying after %s", now_ + retry_cooldown)

        if not result:
            # Empty responses are unavailable observations too. Persist only
            # the deadline, including on a cold cache, so waiting workers and
            # restarted scanners cannot turn one outage into a request burst.
            with atomic_write(retry_file, overwrite=True) as output:
                output.write((now_ + retry_cooldown).isoformat())
            logger.info("Using the last successful ForgeYields snapshot from %s if available", file)
            return _read_cached_strategies(file)

        serialisable = {key: {**entry, "tvl_usd": str(entry["tvl_usd"]), "tvl": str(entry["tvl"])} for key, entry in result.items()}
        with atomic_write(file, overwrite=True) as output:
            json.dump(serialisable, output, indent=2)
        retry_file.unlink(missing_ok=True)
        logger.info("Cached %d ForgeYields strategies at %s", len(result), file)
        return result


def fetch_forgeyields_vault_metadata(vault_address: HexAddress) -> ForgeYieldsVaultMetadata | None:
    """Look up an Ethereum gateway using the expiring shared strategies cache.

    Vault adapters memoise this metadata for their own lifetime. Avoid a
    process-global dictionary here: looped scanners previously retained an
    empty initial response forever, even after the API recovered. The shared
    disk TTL and failed-refresh deadline already bound network requests.

    :param vault_address: Ethereum TokenGateway contract address.
    :return: Strategy metadata, or None when the gateway is unavailable.
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
    """Fetch historical TVL/APR data from the ForgeYields API.

    The ``/strategies`` response includes a ``historyReports`` array with
    ~30 daily snapshots per strategy. Each entry has TVL in both
    denomination token units and USD, plus APR.

    This is a direct API call with no caching — intended for one-shot
    backfill scripts.

    :param api_base_url:
        ForgeYields API base URL.

    :return:
        List of strategy histories, one per vault.
    """
    url = f"{api_base_url}/strategies"
    logger.info("Fetching ForgeYields history from %s", url)
    resp = requests.get(url, headers={"Content-Type": "application/json"}, timeout=30)
    resp.raise_for_status()
    raw_list = resp.json()

    results = []
    for raw in raw_list:
        # Find Ethereum gateway
        ethereum_gateway = None
        for gw in raw.get("token_gateway_per_domain", []):
            if gw.get("domain") == "ethereum":
                addr = gw.get("token_gateway")
                if addr and len(addr) == 42:
                    ethereum_gateway = Web3.to_checksum_address(addr)
                break

        # Parse history entries
        history = []
        for entry in raw.get("historyReports", []):
            ts_str = entry.get("timestamp", "")
            # Parse ISO 8601 to naive UTC datetime
            ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
            history.append(
                ForgeYieldsHistoryEntry(
                    timestamp=ts,
                    tvl=float(entry.get("tvl", 0)),
                    tvl_usd=float(entry.get("tvlUSD", 0)),
                    apr=float(entry.get("apr", 0)),
                    underlying_price=float(entry.get("underlyingPrice", 0)),
                )
            )

        results.append(
            ForgeYieldsStrategyHistory(
                name=raw.get("name", ""),
                symbol=raw.get("symbol", ""),
                underlying_symbol=raw.get("underlyingSymbol", ""),
                ethereum_gateway=ethereum_gateway,
                history=history,
            )
        )

    logger.info("Fetched history for %d ForgeYields strategies", len(results))
    return results
