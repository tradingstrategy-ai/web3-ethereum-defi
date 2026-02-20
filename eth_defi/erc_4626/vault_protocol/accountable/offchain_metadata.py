"""Accountable Capital vault offchain metadata.

- Accountable stores vault descriptions in their web app, not on-chain
- We reverse-engineered the React SPA at ``yield.accountable.capital`` and discovered
  API endpoints that serve vault metadata including strategy descriptions
- The listing endpoint ``/api/loan`` returns paginated vault data with basic info
- The detail endpoint ``/api/loan/{id}`` returns full metadata including ``vault_strategy``
  and ``company_info`` descriptions
- We fetch and cache this data locally to avoid repeated API calls
- Two-level caching: disk (2-day TTL) + in-process dictionary
- Single cache file for all chains (Accountable has ~7 vaults total)
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


#: Where we cache fetched Accountable metadata files
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "accountable"

#: Accountable yield app API base URL, reverse-engineered from their React SPA
DEFAULT_API_BASE_URL = "https://yield.accountable.capital/api"

logger = logging.getLogger(__name__)


class AccountableVaultMetadata(TypedDict):
    """Metadata about an Accountable vault from offchain source.

    Fetched from the Accountable yield app API at ``yield.accountable.capital``.
    Discovered by reverse-engineering the React SPA JavaScript bundles.

    - Listing endpoint: ``GET /api/loan``
    - Detail endpoint: ``GET /api/loan/{id}``

    The detail endpoint returns ``vault_strategy`` and ``company_info`` fields
    that provide strategy and company descriptions.
    """

    #: Vault name from Accountable's app, e.g. ``Aegis Yield Vault``
    name: str

    #: Full vault strategy description (may contain markdown formatting)
    description: str | None

    #: Company/manager description used as a short summary
    short_description: str | None

    #: Company/manager name, e.g. ``Aegis``
    company_name: str | None

    #: Company website URL
    company_url: str | None

    #: Net APY as percentage, e.g. ``12.43``
    net_apy: float | None

    #: Performance fee as fraction, e.g. ``0.20`` for 20%
    performance_fee: float | None

    #: Source of yield, or None if not specified
    yield_source: str | None


def _fetch_vault_listing(
    api_base_url: str = DEFAULT_API_BASE_URL,
) -> list[dict]:
    """Fetch all vaults from the Accountable listing endpoint.

    The listing endpoint returns paginated results. We fetch all pages.

    :param api_base_url:
        API base URL

    :return:
        List of raw vault item dicts from the listing endpoint
    """
    all_items = []
    page = 1
    while True:
        url = f"{api_base_url}/loan?page={page}"
        logger.debug("Fetching Accountable vault listing from %s", url)
        try:
            resp = requests.get(url, timeout=30, headers={"Accept": "application/json"})
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, JSONDecodeError) as e:
            logger.warning("Failed to fetch Accountable vault listing from %s: %s", url, e)
            return all_items

        items = data.get("items", [])
        all_items.extend(items)

        total_count = data.get("total_count", 0)
        page_size = data.get("page_size", 24)
        if page * page_size >= total_count:
            break
        page += 1

    return all_items


def _fetch_vault_detail(
    vault_id: int,
    api_base_url: str = DEFAULT_API_BASE_URL,
) -> dict | None:
    """Fetch detailed vault metadata from the Accountable detail endpoint.

    :param vault_id:
        Integer vault ID from the listing endpoint

    :param api_base_url:
        API base URL

    :return:
        Raw JSON response dict with ``loan``, ``loan_computed``, ``on_chain_loan`` keys,
        or None on failure
    """
    url = f"{api_base_url}/loan/{vault_id}"
    logger.debug("Fetching Accountable vault detail from %s", url)
    try:
        resp = requests.get(url, timeout=30, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, JSONDecodeError) as e:
        logger.warning("Failed to fetch Accountable vault detail for ID %d: %s", vault_id, e)
        return None


def _parse_vault_metadata(listing_item: dict, detail: dict | None) -> AccountableVaultMetadata:
    """Parse vault metadata from listing and detail API responses.

    :param listing_item:
        Raw dict from the listing endpoint ``/api/loan``

    :param detail:
        Raw dict from the detail endpoint ``/api/loan/{id}``, or None if fetch failed
    """
    loan = detail.get("loan", {}) if detail else {}

    # Performance fee from listing is in basis points (e.g. 200000 = 20%)
    raw_fee = listing_item.get("performance_fee")
    performance_fee = raw_fee / 1_000_000 if raw_fee is not None else None

    return AccountableVaultMetadata(
        name=listing_item.get("loan_name", ""),
        description=loan.get("vault_strategy"),
        short_description=loan.get("company_info"),
        company_name=loan.get("company_name") or listing_item.get("company_name"),
        company_url=loan.get("company_url"),
        net_apy=listing_item.get("net_apy"),
        performance_fee=performance_fee,
        yield_source=loan.get("yield_source"),
    )


def fetch_accountable_vaults(
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = datetime.timedelta(days=2),
) -> dict[str, AccountableVaultMetadata]:
    """Fetch and cache all Accountable offchain vault metadata.

    - Fetches all vaults from the listing endpoint across all chains
    - Fetches detail for each vault to get strategy descriptions
    - Single JSON cache file for all Accountable vaults (~7 total)
    - Multiprocess safe via file lock

    :param cache_path:
        Directory for cache files (default ``~/.tradingstrategy/cache/accountable/``)

    :param api_base_url:
        Accountable API base URL

    :param now_:
        Override current time (for testing)

    :param max_cache_duration:
        How long before refreshing cache (default 2 days)

    :return:
        Dict mapping checksummed vault address to :py:class:`AccountableVaultMetadata`
    """

    assert isinstance(cache_path, Path), "cache_path must be Path instance"

    cache_path.mkdir(parents=True, exist_ok=True)
    file = cache_path / "accountable_vaults.json"
    file = file.resolve()

    file_size = file.stat().st_size if file.exists() else 0

    if not now_:
        now_ = native_datetime_utc_now()

    # When running multiprocess vault scan, we have competition over this file write and
    # if we do not wait the race condition may try to read zero-bytes file
    with wait_other_writers(file):
        if not file.exists() or (now_ - native_datetime_utc_fromtimestamp(file.stat().st_mtime)) > max_cache_duration or file_size == 0:
            logger.info("Re-fetching Accountable vaults metadata from %s", api_base_url)

            all_items = _fetch_vault_listing(api_base_url=api_base_url)

            logger.info("Found %d Accountable vaults, fetching details", len(all_items))

            # Fetch detail for each vault (uses integer ID, not address).
            # The API's ``loan_address`` is the strategy/loan contract, not the ERC-4626 vault.
            # The actual vault (share token) address is in ``on_chain_loan.vault.share``.
            result: dict[str, AccountableVaultMetadata] = {}
            for item in all_items:
                vault_id = item.get("id")
                if not vault_id:
                    continue

                detail = _fetch_vault_detail(vault_id, api_base_url=api_base_url)

                # Use the share token address as key — this is the ERC-4626 vault contract
                share_address = None
                if detail:
                    share_address = detail.get("on_chain_loan", {}).get("vault", {}).get("share")
                if not share_address:
                    # Fallback to loan_address if detail fetch failed
                    share_address = item.get("loan_address")
                if not share_address:
                    continue

                checksummed = Web3.to_checksum_address(share_address)
                result[checksummed] = _parse_vault_metadata(item, detail)

            logger.info("Fetched metadata for %d Accountable vaults", len(result))

            if not result:
                logger.warning("Accountable API returned 0 vaults, skipping cache write to avoid poisoning the cache")
                return {}

            # Serialise result dict so that TypedDict values are JSON-compatible
            with file.open("wt") as f:
                json.dump(result, f, indent=2)

            logger.info("Wrote Accountable cache %s", file)

            assert file.stat().st_size > 0, f"File {file} is empty after writing"
            return result

        else:
            timestamp = datetime.datetime.fromtimestamp(file.stat().st_mtime, tz=None)
            ago = now_ - timestamp
            logger.info("Using cached Accountable vaults file from %s, last fetched at %s, ago %s", file, timestamp.isoformat(), ago)

            if file_size == 0:
                return {}

            try:
                return json.load(open(file, "rt"))
            except JSONDecodeError as e:
                content = open(file, "rt").read()
                raise RuntimeError(f"Could not parse Accountable vaults file at {file}, length {len(content)} content starts with {content[:100]!r}") from e


def fetch_accountable_vault_metadata(web3: Web3, vault_address: HexAddress) -> AccountableVaultMetadata | None:
    """Fetch vault metadata from Accountable's offchain yield app API.

    - Do both in-process and disk cache to avoid repeated fetches

    :param web3:
        Web3 instance (used to checksum address)

    :param vault_address:
        Vault contract address

    :return:
        Metadata dict or None if the vault is not in Accountable's app database
    """
    global _cached_vaults

    if _cached_vaults is None:
        _cached_vaults = fetch_accountable_vaults()

    if _cached_vaults:
        vault_address = Web3.to_checksum_address(vault_address)
        return _cached_vaults.get(vault_address)

    return None


#: In-process cache of fetched vaults (single dict for all chains)
_cached_vaults: dict[HexAddress, AccountableVaultMetadata] | None = None
