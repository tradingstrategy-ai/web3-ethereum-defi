"""Mellow vault offchain metadata.

Mellow publishes a public vault catalogue API at
``https://points.mellow.finance/v1/vaults``. The adapter uses this metadata
only for enrichment values that are not part of the canonical on-chain vault
accounting path, such as public names, symbols and base-token hints.

The current TVL value returned by the API is USD-denominated and
point-in-time. It is useful for manual mapping diagnostics, but it is not
written as production ``NAV`` or ``total_assets``. Mellow current and
historical denomination-token TVL is read from on-chain vault state as
``share_price * total_supply``.

API response structure
~~~~~~~~~~~~~~~~~~~~~~

``GET /v1/vaults`` returns a JSON array. Each element is expected to include:

.. code-block:: json

    {
        "chain_id": 1,
        "address": "0x014e6DA8F283C4aF65B2AA0f201438680A004452",
        "symbol": "earnUSD",
        "name": "Lido Earn USD",
        "layer": "mellow",
        "tvl_usd": "21897383.50",
        "base_token": {
            "symbol": "USDC",
            "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        }
    }

The module follows the same two-level cache pattern used by other offchain
vault metadata integrations: a disk cache for multiprocess scanner runs and an
in-process dictionary for adapter lookups.
"""

import datetime
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from json import JSONDecodeError
from pathlib import Path
from typing import Any

import requests
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.utils import wait_other_writers

#: Where we cache fetched Mellow API metadata files.
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "mellow"

#: Mellow public API base URL documented at https://docs.mellow.finance/resources/api
DEFAULT_API_BASE_URL = "https://points.mellow.finance"

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class MellowApiVaultMetadata:
    """Optional current Mellow API metadata attached to a vault.

    The Mellow API is an enrichment source. Its USD TVL is intentionally kept
    separate from on-chain denomination-token TVL so current scan records remain
    comparable with ERC-4626 and other smart-contract vault protocols.
    """

    #: EVM chain id.
    chain_id: int | None = None

    #: Canonical Mellow Vault address.
    address: HexAddress | None = None

    #: Public vault name.
    name: str | None = None

    #: Public vault symbol.
    symbol: str | None = None

    #: Mellow API layer field, e.g. ``mellow`` or ``symbiotic``.
    layer: str | None = None

    #: Current USD TVL from the public Mellow API, for diagnostics only.
    #:
    #: Do not use this value as canonical production NAV. ``MellowVault`` reads
    #: comparable denomination-token TVL from on-chain share price and
    #: ``ShareManager.totalSupply()``.
    tvl_usd: Decimal | None = None

    #: Base token address from API/configuration, if known.
    base_token_address: HexAddress | None = None

    #: Base token symbol from API/configuration, if known.
    base_token_symbol: str | None = None

    #: Raw API fields kept for diagnostics.
    raw: dict[str, object] = field(default_factory=dict)


def _parse_optional_checksum_address(address: str | None) -> HexAddress | None:
    """Parse an optional API address.

    :param address:
        Raw address string from the API.

    :return:
        Checksummed address or ``None``.
    """

    if not address:
        return None
    return HexAddress(Web3.to_checksum_address(address))


def _parse_api_vault(item: dict[str, Any]) -> MellowApiVaultMetadata:
    """Parse one vault entry from the Mellow API.

    :param item:
        Raw JSON object from ``/v1/vaults``.

    :return:
        Parsed Mellow API metadata.
    """

    base_token = item.get("base_token") or {}
    tvl_usd_raw = item.get("tvl_usd")

    return MellowApiVaultMetadata(
        chain_id=int(item["chain_id"]) if item.get("chain_id") is not None else None,
        address=_parse_optional_checksum_address(item.get("address")),
        name=item.get("name"),
        symbol=item.get("symbol"),
        layer=item.get("layer"),
        tvl_usd=Decimal(str(tvl_usd_raw)) if tvl_usd_raw is not None else None,
        base_token_address=_parse_optional_checksum_address(base_token.get("address")),
        base_token_symbol=base_token.get("symbol"),
        raw=item,
    )


def _parse_api_vaults(raw_list: list[dict[str, Any]]) -> dict[tuple[int, str], MellowApiVaultMetadata]:
    """Parse and index the Mellow API vault list.

    :param raw_list:
        Raw JSON list from the Mellow API or cache file.

    :return:
        Mapping ``(chain_id, lower-case vault address) -> metadata``.
    """

    api_vaults: dict[tuple[int, str], MellowApiVaultMetadata] = {}
    for item in raw_list:
        api_vault = _parse_api_vault(item)
        if api_vault.chain_id is None or api_vault.address is None:
            logger.debug("Skipping Mellow API vault without chain/address: %s", item)
            continue
        api_vaults[api_vault.chain_id, api_vault.address.lower()] = api_vault

    return api_vaults


def _load_cached_vaults(file: Path) -> dict[tuple[int, str], MellowApiVaultMetadata]:
    """Load Mellow vault metadata from a cache file.

    :param file:
        Cache file path.

    :return:
        Parsed API metadata mapping.
    """

    try:
        with file.open("rt") as inp:
            raw_list = json.load(inp)
    except JSONDecodeError as e:
        content = file.read_text()
        raise RuntimeError(f"Could not parse Mellow cache at {file}, length {len(content)}, content starts with {content[:100]!r}") from e

    if not isinstance(raw_list, list):
        raise RuntimeError(f"Mellow cache at {file} must contain a JSON list, got {type(raw_list)}")

    return _parse_api_vaults(raw_list)


def fetch_mellow_api_vaults(
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = datetime.timedelta(days=2),
) -> dict[tuple[int, str], MellowApiVaultMetadata]:
    """Fetch and cache public Mellow API vault data.

    The public API is used only as an enrichment layer. Hypersync factory
    events remain the source of truth for newly-created Core Vault leads, and
    on-chain share price/supply reads remain the source of truth for
    denomination-token TVL.

    :param cache_path:
        Directory for cache files.

    :param api_base_url:
        Mellow API base URL.

    :param now_:
        Override current time for tests.

    :param max_cache_duration:
        How long before refreshing cache.

    :return:
        Mapping ``(chain_id, lower-case vault address) -> metadata``.
    """

    assert isinstance(cache_path, Path), "cache_path must be Path instance"

    cache_path.mkdir(parents=True, exist_ok=True)
    file = (cache_path / "mellow_vaults.json").resolve()
    file_size = file.stat().st_size if file.exists() else 0

    if not now_:
        now_ = native_datetime_utc_now()

    with wait_other_writers(file):
        if file.exists() and file_size > 0 and (now_ - native_datetime_utc_fromtimestamp(file.stat().st_mtime)) <= max_cache_duration:
            timestamp = native_datetime_utc_fromtimestamp(file.stat().st_mtime)
            ago = now_ - timestamp
            logger.info("Using cached Mellow vault metadata from %s, last fetched at %s, ago %s", file, timestamp.isoformat(), ago)
            return _load_cached_vaults(file)

        url = f"{api_base_url}/v1/vaults"
        logger.info("Fetching Mellow vault metadata from %s", url)

        try:
            response = requests.get(url, headers={"Accept": "application/json"}, timeout=30)
            response.raise_for_status()
            raw_list = response.json()
        except (requests.RequestException, JSONDecodeError, ValueError) as e:
            logger.warning("Failed to fetch Mellow vault metadata from %s: %s", url, e)
            if file.exists() and file.stat().st_size > 0:
                logger.info("Using stale Mellow cache at %s after API failure", file)
                return _load_cached_vaults(file)
            return {}

        if not isinstance(raw_list, list):
            logger.warning("Mellow API returned %s instead of list, skipping cache write", type(raw_list))
            return {}

        result = _parse_api_vaults(raw_list)
        logger.info("Fetched metadata for %d Mellow API vaults", len(result))

        if not result:
            logger.warning("Mellow API returned 0 usable vaults, skipping cache write to avoid poisoning the cache")
            return {}

        with file.open("wt") as out:
            json.dump(raw_list, out, indent=2)

        assert file.stat().st_size > 0, f"File {file} is empty after writing"
        return result


def fetch_mellow_api_vault_metadata(
    chain_id: int,
    vault_address: HexAddress,
) -> MellowApiVaultMetadata | None:
    """Fetch one vault metadata entry from the Mellow offchain API.

    :param chain_id:
        EVM chain id.

    :param vault_address:
        Mellow Vault address.

    :return:
        Metadata entry or ``None`` if the API has no matching vault.
    """

    global _cached_vaults  # noqa: PLW0603 - Same in-process offchain metadata cache pattern as ForgeYields.

    if _cached_vaults is None:
        _cached_vaults = fetch_mellow_api_vaults()

    return _cached_vaults.get((chain_id, vault_address.lower()))


#: In-process cache of fetched Mellow API vaults.
_cached_vaults: dict[tuple[int, str], MellowApiVaultMetadata] | None = None
