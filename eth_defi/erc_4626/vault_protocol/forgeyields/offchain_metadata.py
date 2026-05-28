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
- Two-level caching: disk (2-day TTL) + in-process dictionary

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

from web3 import Web3
from eth_typing import HexAddress
from eth_defi.compat import native_datetime_utc_now, native_datetime_utc_fromtimestamp
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
    #: This is the canonical TVL — the on-chain ``convertToAssets(totalSupply())``
    #: on the Ethereum TokenGateway only returns a small residual, not the true AUM.
    tvl_usd: Decimal

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

    tvl_raw = info.get("overallUsdPrice", "0")
    tvl_usd = Decimal(str(tvl_raw))

    return ForgeYieldsVaultMetadata(
        name=raw.get("name", ""),
        symbol=raw.get("symbol", ""),
        tvl_usd=tvl_usd,
        apy=apy,
        ethereum_gateway=ethereum_gateway,
    )


def fetch_forgeyields_strategies(
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = datetime.timedelta(days=2),
) -> dict[str, ForgeYieldsVaultMetadata]:
    """Fetch and cache ForgeYields strategy metadata.

    - Single API call returns all strategies
    - Indexed by lowercased Ethereum gateway address
    - Multiprocess safe via file lock

    :param cache_path:
        Directory for cache files (default ``~/.tradingstrategy/cache/forgeyields/``)

    :param api_base_url:
        ForgeYields API base URL

    :param now_:
        Override current time (for testing)

    :param max_cache_duration:
        How long before refreshing cache (default 2 days)

    :return:
        Dict mapping lowercased Ethereum gateway address to :py:class:`ForgeYieldsVaultMetadata`
    """
    assert isinstance(cache_path, Path), "cache_path must be Path instance"

    cache_path.mkdir(parents=True, exist_ok=True)
    file = cache_path / "forgeyields_strategies.json"
    file = file.resolve()

    file_size = file.stat().st_size if file.exists() else 0

    if not now_:
        now_ = native_datetime_utc_now()

    with wait_other_writers(file):
        if not file.exists() or (now_ - native_datetime_utc_fromtimestamp(file.stat().st_mtime)) > max_cache_duration or file_size == 0:
            logger.info("Re-fetching ForgeYields strategies from %s", api_base_url)

            url = f"{api_base_url}/strategies"
            try:
                resp = requests.get(url, headers={"Content-Type": "application/json"}, timeout=30)
                resp.raise_for_status()
                raw_list = resp.json()
            except (requests.RequestException, JSONDecodeError) as e:
                logger.warning("Failed to fetch ForgeYields strategies from %s: %s", url, e)
                return {}

            result: dict[str, ForgeYieldsVaultMetadata] = {}
            for raw in raw_list:
                entry = _parse_strategy(raw)
                if entry["ethereum_gateway"]:
                    key = entry["ethereum_gateway"].lower()
                    result[key] = entry

            logger.info("Fetched metadata for %d ForgeYields strategies", len(result))

            if not result:
                logger.warning("ForgeYields API returned 0 strategies, skipping cache write to avoid poisoning the cache")
                return {}

            # Serialise — Decimal needs string conversion
            serialisable = {}
            for k, v in result.items():
                sv = dict(v)
                sv["tvl_usd"] = str(sv["tvl_usd"])
                serialisable[k] = sv

            with file.open("wt") as f:
                json.dump(serialisable, f, indent=2)

            logger.info("Wrote ForgeYields cache %s", file)
            assert file.stat().st_size > 0, f"File {file} is empty after writing"
            return result

        else:
            timestamp = datetime.datetime.fromtimestamp(file.stat().st_mtime, tz=None)
            ago = now_ - timestamp
            logger.info("Using cached ForgeYields strategies from %s, last fetched at %s, ago %s", file, timestamp.isoformat(), ago)

            if file_size == 0:
                return {}

            try:
                serialised = json.load(open(file, "rt"))
            except JSONDecodeError as e:
                content = open(file, "rt").read()
                raise RuntimeError(f"Could not parse ForgeYields cache at {file}, length {len(content)}, content starts with {content[:100]!r}") from e

            # Deserialise Decimal strings back
            result = {}
            for k, v in serialised.items():
                v["tvl_usd"] = Decimal(v["tvl_usd"])
                result[k] = v
            return result


def fetch_forgeyields_vault_metadata(vault_address: HexAddress) -> ForgeYieldsVaultMetadata | None:
    """Fetch vault metadata from ForgeYields' offchain strategies API.

    - Uses a two-level cache: in-process dict + disk cache
    - Looks up by the Ethereum gateway address

    :param vault_address:
        Vault contract address (Ethereum TokenGateway)

    :return:
        Metadata dict or None if the address is not a known ForgeYields gateway
    """
    global _cached_strategies

    if _cached_strategies is None:
        _cached_strategies = fetch_forgeyields_strategies()

    key = vault_address.lower()
    return _cached_strategies.get(key)


#: In-process cache of fetched strategies
_cached_strategies: dict[str, ForgeYieldsVaultMetadata] | None = None
