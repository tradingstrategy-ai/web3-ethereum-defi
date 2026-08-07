"""Lagoon vault offchain metadata.

- Lagoon stores vault descriptions in their web app, not onchain or in a public data repository
- We reverse-engineered the Lagoon Next.js app and discovered web application API endpoints
  at ``app.lagoon.finance`` that serve vault metadata including descriptions
- The listing endpoint ``/api/vaults`` returns paginated vault data without descriptions
- The detail endpoint ``/api/vault`` returns full vault data including ``description`` and ``shortDescription``
- We fetch and cache this data locally to avoid repeated API calls
- Two-level caching: disk (1-day TTL) + an expiring in-process dictionary
"""

import datetime
import json
import logging
from dataclasses import dataclass
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

#: Where we cache fetched Lagoon metadata files
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "lagoon"

#: Lagoon web app API base URL, reverse-engineered from their Next.js frontend
DEFAULT_API_BASE_URL = "https://app.lagoon.finance/api"

#: How long Lagoon metadata is cached before the scanner refreshes it.
DEFAULT_CACHE_DURATION = datetime.timedelta(days=1)

#: Prevent one failed refresh from being retried for every vault in the same scan.
FAILED_REFRESH_RETRY_DELAY = datetime.timedelta(hours=1)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _LagoonMetadataCache:
    """One chain's Lagoon metadata held by the long-running scanner.

    The looped scanner stays in one Python process across scan cycles. Its
    process cache therefore needs an explicit expiry in addition to the JSON
    file expiry.
    """

    #: Metadata keyed by checksummed vault address.
    vaults: dict[HexAddress, "LagoonVaultMetadata"]

    #: Naive UTC time after which Lagoon metadata must be fetched again.
    expires_at: datetime.datetime


def _get_cache_file(cache_path: Path, chain_id: int) -> Path:
    """Resolve one chain's Lagoon cache file.

    :param cache_path:
        Directory containing Lagoon metadata cache files.

    :param chain_id:
        EVM chain id.

    :return:
        Absolute path to the chain's JSON cache file.
    """
    return (cache_path / f"lagoon_vaults_chain_{chain_id}.json").resolve()


class LagoonCuratorMetadata(TypedDict):
    """Metadata about a Lagoon vault curator.

    Extracted from the Lagoon web app API ``/api/vault`` endpoint.
    """

    #: Curator slug identifier, e.g. ``tulipa-capital``
    id: str

    #: Human-readable curator name, e.g. ``Tulipa Capital``
    name: str

    #: Curator's website URL, e.g. ``https://tulipa.capital``
    url: str | None

    #: Logo URL on Lagoon's GCS bucket
    logo_url: str | None

    #: Short about text, e.g. ``Asset manager: Tulipa Capital``
    about_description: str | None


class LagoonVaultMetadata(TypedDict):
    """Metadata about a Lagoon vault from offchain source.

    Fetched from the Lagoon web app API at ``app.lagoon.finance``.
    Discovered by reverse-engineering the Lagoon Next.js JavaScript bundles.

    - Listing endpoint: ``GET /api/vaults?chainId={chainId}&pageIndex=0&pageSize=100``
    - Detail endpoint: ``GET /api/vault?chainId={chainId}&address={address}``

    The detail endpoint returns ``description`` and ``shortDescription`` fields
    that are not available in the listing endpoint.
    """

    #: Vault name from Lagoon's app, e.g. ``RockSolid rETH Vault``
    name: str

    #: Full vault strategy description.
    #:
    #: Example: ``RockSolid's rETH Vault maximizes rETH-based returns
    #: by allocating it across DeFi protocols like AAVE and Morpho...``
    description: str | None

    #: One-liner vault summary.
    #:
    #: Example: ``Our rETH vault both monitors for the latest opportunities,
    #: monitors funding rates to ensure optimal allocations...``
    short_description: str | None

    #: Vault share token logo URL on Lagoon's GCS bucket.
    #:
    #: Example: ``https://storage.googleapis.com/lagoon-logos/shares/rocketh``
    logo_url: str | None

    #: URL to an external transparency/reporting page, if provided by the curator
    transparency_url: str | None

    #: Average settlement time in seconds (e.g. 86400 for 1 day)
    average_settlement: int | None

    #: List of curators managing this vault
    curators: list[LagoonCuratorMetadata]


def _parse_curator(raw: dict) -> LagoonCuratorMetadata:
    """Parse a curator object from the Lagoon API response."""
    return LagoonCuratorMetadata(
        id=raw.get("id", ""),
        name=raw.get("name") or "",
        url=raw.get("url"),
        logo_url=raw.get("logoUrl"),
        about_description=raw.get("aboutDescription"),
    )


def _parse_vault_detail(raw: dict) -> LagoonVaultMetadata:
    """Parse vault metadata from the detail API response.

    :param raw:
        Raw JSON dict from ``/api/vault`` endpoint
    """
    curators_raw = raw.get("curators", [])
    curators = [_parse_curator(c) for c in curators_raw] if curators_raw else []

    return LagoonVaultMetadata(
        name=raw.get("name", ""),
        description=raw.get("description"),
        short_description=raw.get("shortDescription"),
        logo_url=raw.get("logoUrl"),
        transparency_url=raw.get("transparencyUrl"),
        average_settlement=raw.get("averageSettlement"),
        curators=curators,
    )


def _fetch_vault_listing_page(
    chain_id: int,
    page_index: int = 0,
    page_size: int = 100,
    api_base_url: str = DEFAULT_API_BASE_URL,
) -> dict | None:
    """Fetch a page of vault listings from the Lagoon web app API.

    The official Lagoon web application uses this endpoint at
    ``https://app.lagoon.finance/api/vaults``.

    :param chain_id:
        EVM chain id

    :param page_index:
        Pagination index (0-based)

    :param page_size:
        Number of vaults per page

    :param api_base_url:
        Lagoon API base URL

    :return:
        Raw JSON response dict with keys ``vaults``, ``totalCount`` and
        ``hasNextPage``, or ``None`` when the request fails.
    """
    url = f"{api_base_url}/vaults?chainId={chain_id}&pageIndex={page_index}&pageSize={page_size}"
    logger.debug("Fetching Lagoon vault listing from %s", url)
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, JSONDecodeError) as e:
        logger.warning("Failed to fetch Lagoon vault listing from %s: %s", url, e)
        return None


def _fetch_vault_detail(
    chain_id: int,
    address: HexAddress,
    api_base_url: str = DEFAULT_API_BASE_URL,
) -> dict | None:
    """Fetch detailed vault metadata from the Lagoon web app API.

    The official Lagoon web application uses this endpoint at
    ``https://app.lagoon.finance/api/vault``.

    :param chain_id:
        EVM chain id

    :param address:
        Vault contract address

    :param api_base_url:
        Lagoon API base URL

    :return:
        Raw JSON response dict, or ``None`` when the request fails or Lagoon
        does not have a record for the vault.
    """
    url = f"{api_base_url}/vault?chainId={chain_id}&address={address}"
    logger.debug("Fetching Lagoon vault detail from %s", url)
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, JSONDecodeError) as e:
        logger.warning("Failed to fetch Lagoon vault detail for %s on chain %d: %s", address, chain_id, e)
        return None


def _load_cached_vaults(file: Path, chain_id: int) -> dict[HexAddress, LagoonVaultMetadata]:
    """Load one chain's Lagoon metadata JSON cache.

    Cache corruption is raised as a hard error because silently replacing the
    mapping with an empty result would erase metadata during the next vault
    scan.

    :param file:
        JSON cache file to read.

    :param chain_id:
        EVM chain id used in error messages.

    :return:
        Metadata keyed by checksummed vault address.

    :raises RuntimeError:
        If the cache is not valid JSON.
    """
    try:
        with file.open(encoding="utf-8") as cache_input:
            return json.load(cache_input)
    except (JSONDecodeError, UnicodeDecodeError) as e:
        content = file.read_text(encoding="utf-8", errors="replace")
        raise RuntimeError(f"Could not parse Lagoon vaults file for chain {chain_id} at {file}, length {len(content)} content starts with {content[:100]!r}") from e


def fetch_lagoon_vaults_for_chain(
    chain_id: int,
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION,
) -> dict[HexAddress, LagoonVaultMetadata]:
    """Fetch and cache Lagoon offchain vault metadata for a given chain.

    - Enumerates vaults using the listing endpoint, then fetches each vault's
      detail (including description) from the detail endpoint
    - One JSON cache file per chain
    - Serialises concurrent readers and writers with a file lock
    - Retains stale metadata when Lagoon has a transient API failure

    The official Lagoon web application uses these endpoints under
    ``https://app.lagoon.finance/api``.

    :param chain_id:
        EVM chain id

    :param cache_path:
        Directory for cache files (default ``~/.tradingstrategy/cache/lagoon/``)

    :param api_base_url:
        Lagoon API base URL

    :param now_:
        Override current time (for testing)

    :param max_cache_duration:
        How long before refreshing cache (default 1 day)

    :return:
        Dict mapping checksummed vault address to :py:class:`LagoonVaultMetadata`
    """

    assert type(chain_id) is int, "chain_id must be integer"
    assert isinstance(cache_path, Path), "cache_path must be Path instance"

    cache_path.mkdir(parents=True, exist_ok=True)
    file = _get_cache_file(cache_path, chain_id)

    now_ = now_ or native_datetime_utc_now()

    # Multiple scanner threads may discover Lagoon vaults at the same time.
    # Keep the freshness check and any replacement write under one file lock.
    with wait_other_writers(file):
        has_cache = file.exists() and file.stat().st_size > 0
        cached_vaults = _load_cached_vaults(file, chain_id) if has_cache else {}
        if has_cache:
            fetched_at = native_datetime_utc_fromtimestamp(file.stat().st_mtime)
            if now_ - fetched_at < max_cache_duration:
                logger.info("Using cached Lagoon vaults file for chain %d from %s, last fetched at %s, ago %s", chain_id, file, fetched_at.isoformat(), now_ - fetched_at)
                return cached_vaults

        logger.info("Re-fetching Lagoon vaults metadata for chain %d from %s", chain_id, api_base_url)

        # Lagoon ignores chainId on the listing endpoint, so filter every page
        # by the chain id embedded in each returned vault record.
        all_vault_addresses: list[HexAddress] = []
        page_index = 0
        while True:
            page_data = _fetch_vault_listing_page(chain_id, page_index=page_index, api_base_url=api_base_url)
            if page_data is None:
                logger.warning("Keeping %d stale Lagoon metadata entries for chain %d after a listing request failure", len(cached_vaults), chain_id)
                return cached_vaults

            for vault in page_data.get("vaults", []):
                address = vault.get("address")
                vault_chain_id = vault.get("chain", {}).get("id")
                if address and vault_chain_id is not None and int(vault_chain_id) == chain_id:
                    all_vault_addresses.append(HexAddress(address))

            if not page_data.get("hasNextPage", False):
                break
            page_index += 1

        logger.info("Found %d Lagoon vaults on chain %d, fetching details", len(all_vault_addresses), chain_id)

        if not all_vault_addresses and cached_vaults:
            # An empty listing can also mean Lagoon changed its response shape.
            # Do not erase known descriptions until a later refresh confirms it.
            logger.warning("Lagoon returned no vaults for chain %d; keeping %d stale entries", chain_id, len(cached_vaults))
            return cached_vaults

        result: dict[HexAddress, LagoonVaultMetadata] = {}
        fresh_detail_count = 0
        for address in all_vault_addresses:
            detail = _fetch_vault_detail(chain_id, address, api_base_url=api_base_url)
            checksummed_address = Web3.to_checksum_address(address)
            if detail is not None:
                result[checksummed_address] = _parse_vault_detail(detail)
                fresh_detail_count += 1
            elif checksummed_address in cached_vaults:
                # A single failed detail request must not erase metadata that
                # the scanner already published for this vault.
                result[checksummed_address] = cached_vaults[checksummed_address]

        logger.info("Fetched fresh metadata for %d/%d Lagoon vaults on chain %d", fresh_detail_count, len(all_vault_addresses), chain_id)

        if all_vault_addresses and fresh_detail_count == 0:
            logger.warning("Lagoon API returned no fresh vault details for chain %d; keeping %d stale entries", chain_id, len(cached_vaults))
            return cached_vaults

        # Atomic replacement prevents a scanner interruption from leaving a
        # truncated JSON file that blocks every later metadata refresh.
        with atomic_write(str(file), mode="w", overwrite=True, encoding="utf-8") as cache_output:
            json.dump(result, cache_output, indent=2)

        logger.info("Wrote Lagoon cache %s", file)
        assert file.stat().st_size > 0, f"File {file} is empty after writing"
        return result


def _resolve_memory_cache_expiry(cache_path: Path, chain_id: int, now_: datetime.datetime) -> datetime.datetime:
    """Resolve when one chain's in-memory Lagoon metadata expires.

    A successful refresh writes the JSON file, so its modification time anchors
    the normal one-day expiry. When a failed refresh returns stale data without
    writing, use a short retry delay instead of retrying once per scanned vault.

    :param cache_path:
        Directory containing Lagoon metadata cache files.

    :param chain_id:
        EVM chain id.

    :param now_:
        Current naive UTC time.

    :return:
        Naive UTC expiry time for the in-memory entry.
    """
    cache_file = _get_cache_file(cache_path, chain_id)
    if not cache_file.exists() or cache_file.stat().st_size == 0:
        return now_ + FAILED_REFRESH_RETRY_DELAY

    fetched_at = native_datetime_utc_fromtimestamp(cache_file.stat().st_mtime)
    regular_expiry = fetched_at + DEFAULT_CACHE_DURATION
    return regular_expiry if regular_expiry > now_ else now_ + FAILED_REFRESH_RETRY_DELAY


def fetch_lagoon_vault_metadata(
    web3: Web3,
    vault_address: HexAddress,
    now_: datetime.datetime | None = None,
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> LagoonVaultMetadata | None:
    """Fetch vault metadata from Lagoon's offchain web app API.

    - Use both expiring in-process and disk caches to avoid repeated fetches

    :param web3:
        Web3 instance (used to get chain_id and checksum address)

    :param vault_address:
        Vault contract address

    :param now_:
        Override the current naive UTC time for testing.

    :param cache_path:
        Directory containing Lagoon metadata cache files.

    :return:
        Metadata dict or None if the vault is not in Lagoon's app database
    """
    chain_id = web3.eth.chain_id
    now_ = now_ or native_datetime_utc_now()
    cache_file = _get_cache_file(cache_path, chain_id)
    cached = _cached_vaults.get(cache_file)

    # Refresh the process cache as well as the disk cache. The looped scanner
    # keeps this module loaded across scan cycles, so an unbounded dictionary
    # would otherwise keep stale descriptions until the container restarts.
    if cached is None or now_ >= cached.expires_at:
        vaults = fetch_lagoon_vaults_for_chain(chain_id, cache_path=cache_path, now_=now_)
        cached = _LagoonMetadataCache(
            vaults=vaults,
            expires_at=_resolve_memory_cache_expiry(cache_path, chain_id, now_),
        )
        _cached_vaults[cache_file] = cached

    # Extract vault from Lagoon cache
    vault_address = Web3.to_checksum_address(vault_address)
    return cached.vaults.get(vault_address)


#: Expiring in-process cache keyed by the chain's disk-cache file.
_cached_vaults: dict[Path, _LagoonMetadataCache] = {}
