"""Ember vault offchain metadata.

- Ember stores vault descriptions, fees, and manager info in their web app, not on-chain
- We reverse-engineered the Vite+React SPA at ``ember.so/earn`` and discovered
  API endpoints served by Bluefin's infrastructure
- The vault listing endpoint ``/api/v2/vaults?chain=ethereum`` returns all EVM vaults
  with metadata including descriptions, fee parameters, APY, and manager info
- We fetch and cache this data locally to avoid repeated API calls
- Two-level caching: disk (2-day TTL) + in-process dictionary

API base: ``https://vaults.api.sui-prod.bluefin.io``

Numeric encoding convention:

- ``E9`` suffix: value multiplied by 10^9
- ``E18`` suffix: value multiplied by 10^18
- ``weeklyPerformanceFeeBpsE9``: bps value in E9 format
"""

import datetime
import json
import logging
from json import JSONDecodeError
from pathlib import Path
from typing import TypedDict

import requests

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now, native_datetime_utc_fromtimestamp
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.utils import wait_other_writers


#: Where we cache fetched Ember metadata files
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "ember"

#: Ember vault API base URL, reverse-engineered from the Vite+React SPA at ember.so
DEFAULT_API_BASE_URL = "https://vaults.api.sui-prod.bluefin.io/api/v2"

logger = logging.getLogger(__name__)


class EmberVaultMetadata(TypedDict):
    """Metadata about an Ember vault from offchain source.

    Fetched from the Bluefin vaults API at ``vaults.api.sui-prod.bluefin.io``.
    Discovered by reverse-engineering the Vite+React SPA at ``ember.so/earn``.

    - Listing endpoint: ``GET /api/v2/vaults?chain=ethereum``
    """

    #: Vault name, e.g. ``Crosschain USD Vault``
    name: str

    #: Full vault strategy description
    description: str | None

    #: Strategy type, e.g. ``Stablecoin Strategy``
    strategy: str | None

    #: Vault logo URL
    logo_url: str | None

    #: Annual management fee as fraction (e.g. 0.02 = 2%)
    management_fee: float | None

    #: Weekly performance fee as fraction (e.g. 0.0001 = 0.01% per week)
    weekly_performance_fee: float | None

    #: Withdrawal lock period in days
    withdrawal_period_days: int | None

    #: Current reported APY as fraction (e.g. 0.08 = 8%)
    reported_apy: float | None

    #: Total vault TVL in USD
    tvl_usd: float | None

    #: Curator/manager name, e.g. ``Third Eye``
    manager_name: str | None

    #: Curator/manager website URL
    manager_url: str | None


def _fetch_ember_vaults(
    api_base_url: str = DEFAULT_API_BASE_URL,
) -> list[dict]:
    """Fetch all Ethereum vaults from the Ember API.

    Single GET request, no pagination needed (~9 EVM vaults).

    :param api_base_url:
        API base URL

    :return:
        List of raw vault dicts from the API
    """
    url = f"{api_base_url}/vaults?chain=ethereum"
    logger.debug("Fetching Ember vault listing from %s", url)
    try:
        resp = requests.get(url, timeout=30, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, JSONDecodeError) as e:
        logger.warning("Failed to fetch Ember vault listing from %s: %s", url, e)
        return []


def _parse_e_value(value: str | None, divisor: float) -> float | None:
    """Parse an E-encoded numeric string from the Ember API.

    Some vaults return empty strings instead of numeric values
    (e.g. beta vaults with no APY data).

    :param value:
        Raw string value from API (e.g. ``"1000000000"``)

    :param divisor:
        Divisor to convert to human-readable value (e.g. ``1e9``)
    """
    if not value:
        return None
    return int(value) / divisor


def _parse_vault_metadata(item: dict) -> EmberVaultMetadata:
    """Parse vault metadata from API response.

    Numeric encoding:

    - ``managementFeePercentE18``: string, divide by 1e18 for fraction
    - ``weeklyPerformanceFeeBpsE9``: string, bps in E9 format.
      Divide by 1e9 for bps, then by 10000 for fraction.
    - ``reportedApyE9``: nested in reportedApy, divide by 1e9 for fraction
    - ``totalDepositsInUsdE9``: string, divide by 1e9 for USD

    :param item:
        Raw dict from the API listing endpoint
    """
    # Management fee
    management_fee = _parse_e_value(item.get("managementFeePercentE18"), 1e18)

    # Weekly performance fee (bps in E9 format)
    raw_perf = _parse_e_value(item.get("weeklyPerformanceFeeBpsE9"), 1e9)
    weekly_performance_fee = raw_perf / 10000 if raw_perf is not None else None

    # Withdrawal period
    withdrawal_period_days = item.get("withdrawalPeriodDays")

    # Reported APY
    reported_apy_obj = item.get("reportedApy", {}) or {}
    reported_apy = _parse_e_value(reported_apy_obj.get("reportedApyE9"), 1e9)

    # TVL in USD
    tvl_usd = _parse_e_value(item.get("totalDepositsInUsdE9"), 1e9)

    # Manager info
    managers = item.get("managers", []) or []
    manager_name = managers[0].get("name") if managers else None
    manager_url = managers[0].get("websiteUrl") if managers else None

    return EmberVaultMetadata(
        name=item.get("name", ""),
        description=item.get("description"),
        strategy=item.get("strategy"),
        logo_url=item.get("logoUrl"),
        management_fee=management_fee,
        weekly_performance_fee=weekly_performance_fee,
        withdrawal_period_days=withdrawal_period_days,
        reported_apy=reported_apy,
        tvl_usd=tvl_usd,
        manager_name=manager_name,
        manager_url=manager_url,
    )


def fetch_ember_vaults(
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = datetime.timedelta(days=2),
) -> dict[str, EmberVaultMetadata]:
    """Fetch and cache all Ember offchain vault metadata.

    - Fetches all Ethereum vaults from a single API call
    - Single JSON cache file for all Ember EVM vaults (~9 total)
    - Multiprocess safe via file lock

    :param cache_path:
        Directory for cache files (default ``~/.tradingstrategy/cache/ember/``)

    :param api_base_url:
        Ember API base URL

    :param now_:
        Override current time (for testing)

    :param max_cache_duration:
        How long before refreshing cache (default 2 days)

    :return:
        Dict mapping checksummed vault address to :py:class:`EmberVaultMetadata`
    """

    assert isinstance(cache_path, Path), "cache_path must be Path instance"

    cache_path.mkdir(parents=True, exist_ok=True)
    file = cache_path / "ember_vaults.json"
    file = file.resolve()

    file_size = file.stat().st_size if file.exists() else 0

    if not now_:
        now_ = native_datetime_utc_now()

    with wait_other_writers(file):
        if not file.exists() or (now_ - native_datetime_utc_fromtimestamp(file.stat().st_mtime)) > max_cache_duration or file_size == 0:
            logger.info("Re-fetching Ember vaults metadata from %s", api_base_url)

            items = _fetch_ember_vaults(api_base_url=api_base_url)

            logger.info("Found %d Ember vaults", len(items))

            result: dict[str, EmberVaultMetadata] = {}
            for item in items:
                # Extract Ethereum vault address from detailsByChain
                details_by_chain = item.get("detailsByChain", {}) or {}
                eth_details = details_by_chain.get("ethereum", {}) or {}
                address = eth_details.get("address")
                if not address:
                    continue

                checksummed = Web3.to_checksum_address(address)
                result[checksummed] = _parse_vault_metadata(item)

            logger.info("Fetched metadata for %d Ember EVM vaults", len(result))

            if not result:
                logger.warning("Ember API returned 0 EVM vaults, skipping cache write to avoid poisoning the cache")
                return {}

            with file.open("wt") as f:
                json.dump(result, f, indent=2)

            logger.info("Wrote Ember cache %s", file)

            assert file.stat().st_size > 0, f"File {file} is empty after writing"
            return result

        else:
            timestamp = datetime.datetime.fromtimestamp(file.stat().st_mtime, tz=None)
            ago = now_ - timestamp
            logger.info("Using cached Ember vaults file from %s, last fetched at %s, ago %s", file, timestamp.isoformat(), ago)

            if file_size == 0:
                return {}

            try:
                return json.load(open(file, "rt"))
            except JSONDecodeError as e:
                content = open(file, "rt").read()
                raise RuntimeError(f"Could not parse Ember vaults file at {file}, length {len(content)} content starts with {content[:100]!r}") from e


def fetch_ember_vault_metadata(web3: Web3, vault_address: HexAddress) -> EmberVaultMetadata | None:
    """Fetch vault metadata from Ember's offchain API.

    - Do both in-process and disk cache to avoid repeated fetches

    :param web3:
        Web3 instance (used to checksum address)

    :param vault_address:
        Vault contract address

    :return:
        Metadata dict or None if the vault is not in Ember's database
    """
    global _cached_vaults

    if _cached_vaults is None:
        _cached_vaults = fetch_ember_vaults()

    if _cached_vaults:
        vault_address = Web3.to_checksum_address(vault_address)
        return _cached_vaults.get(vault_address)

    return None


#: In-process cache of fetched vaults (single dict for all chains)
_cached_vaults: dict[HexAddress, EmberVaultMetadata] | None = None
