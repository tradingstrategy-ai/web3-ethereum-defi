"""Lagoon vault offchain metadata.

- Lagoon stores vault descriptions in their web app, not on-chain or in a public data repository
- We reverse-engineered the Lagoon Next.js app and discovered internal API endpoints
  at ``app.lagoon.finance`` that serve vault metadata including descriptions
- The listing endpoint ``/api/vaults`` returns paginated vault data without descriptions
- The detail endpoint ``/api/vault`` returns full vault data including ``description`` and ``shortDescription``
- We fetch and cache this data locally to avoid repeated API calls
- Two-level caching: disk (2-day TTL) + in-process dictionary
"""

import datetime
import json
import logging
from json import JSONDecodeError
from pathlib import Path
from typing import TypedDict

import requests

from web3 import Web3
from eth_typing import HexAddress
from eth_defi.compat import native_datetime_utc_now, native_datetime_utc_fromtimestamp
from eth_defi.utils import wait_other_writers


#: Where we cache fetched Lagoon metadata files
DEFAULT_CACHE_PATH = Path.home() / ".cache" / "lagoon"

#: Lagoon web app API base URL, reverse-engineered from their Next.js frontend
DEFAULT_API_BASE_URL = "https://app.lagoon.finance/api"

logger = logging.getLogger(__name__)


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
        name=raw.get("name", ""),
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
) -> dict:
    """Fetch a page of vault listings from the Lagoon web app API.

    :param chain_id:
        EVM chain id

    :param page_index:
        Pagination index (0-based)

    :param page_size:
        Number of vaults per page

    :param api_base_url:
        Lagoon API base URL

    :return:
        Raw JSON response dict with keys: ``vaults``, ``totalCount``, ``hasNextPage``
    """
    url = f"{api_base_url}/vaults?chainId={chain_id}&pageIndex={page_index}&pageSize={page_size}"
    logger.debug("Fetching Lagoon vault listing from %s", url)
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, JSONDecodeError) as e:
        logger.warning("Failed to fetch Lagoon vault listing from %s: %s", url, e)
        return {"vaults": [], "totalCount": 0, "hasNextPage": False}


def _fetch_vault_detail(
    chain_id: int,
    address: str,
    api_base_url: str = DEFAULT_API_BASE_URL,
) -> dict | None:
    """Fetch detailed vault metadata from the Lagoon web app API.

    :param chain_id:
        EVM chain id

    :param address:
        Vault contract address

    :param api_base_url:
        Lagoon API base URL

    :return:
        Raw JSON response dict, or None if the vault is not in Lagoon's database (HTTP 500)
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


def fetch_lagoon_vaults_for_chain(
    chain_id: int,
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = datetime.timedelta(days=2),
) -> dict[str, LagoonVaultMetadata]:
    """Fetch and cache Lagoon offchain vault metadata for a given chain.

    - Enumerates vaults using the listing endpoint, then fetches each vault's
      detail (including description) from the detail endpoint
    - One JSON cache file per chain
    - Multiprocess safe via file lock

    :param chain_id:
        EVM chain id

    :param cache_path:
        Directory for cache files (default ``~/.cache/lagoon/``)

    :param api_base_url:
        Lagoon API base URL

    :param now_:
        Override current time (for testing)

    :param max_cache_duration:
        How long before refreshing cache (default 2 days)

    :return:
        Dict mapping checksummed vault address to :py:class:`LagoonVaultMetadata`
    """

    assert type(chain_id) is int, "chain_id must be integer"
    assert isinstance(cache_path, Path), "cache_path must be Path instance"

    cache_path.mkdir(parents=True, exist_ok=True)
    file = cache_path / f"lagoon_vaults_chain_{chain_id}.json"
    file = file.resolve()

    file_size = file.stat().st_size if file.exists() else 0

    if not now_:
        now_ = native_datetime_utc_now()

    # When running multiprocess vault scan, we have competition over this file write and
    # if we do not wait the race condition may try to read zero-bytes file
    with wait_other_writers(file):
        if not file.exists() or (now_ - native_datetime_utc_fromtimestamp(file.stat().st_mtime)) > max_cache_duration or file_size == 0:
            logger.info("Re-fetching Lagoon vaults metadata for chain %d from %s", chain_id, api_base_url)

            # Step 1: Enumerate all vaults on this chain via listing endpoint
            all_vault_addresses = []
            page_index = 0
            while True:
                page_data = _fetch_vault_listing_page(chain_id, page_index=page_index, api_base_url=api_base_url)
                vaults_in_page = page_data.get("vaults", [])
                for v in vaults_in_page:
                    addr = v.get("address")
                    if addr:
                        all_vault_addresses.append(addr)
                if not page_data.get("hasNextPage", False):
                    break
                page_index += 1

            logger.info("Found %d Lagoon vaults on chain %d, fetching details", len(all_vault_addresses), chain_id)

            # Step 2: Fetch detail for each vault to get descriptions
            result: dict[str, LagoonVaultMetadata] = {}
            for addr in all_vault_addresses:
                detail = _fetch_vault_detail(chain_id, addr, api_base_url=api_base_url)
                if detail is not None:
                    checksummed = Web3.to_checksum_address(addr)
                    result[checksummed] = _parse_vault_detail(detail)

            logger.info("Fetched metadata for %d Lagoon vaults on chain %d", len(result), chain_id)

            # Serialise result dict so that TypedDict values are JSON-compatible
            with file.open("wt") as f:
                json.dump(result, f, indent=2)

            logger.info("Wrote Lagoon cache %s", file)

            assert file.stat().st_size > 0, f"File {file} is empty after writing"
            return result

        else:
            timestamp = datetime.datetime.fromtimestamp(file.stat().st_mtime, tz=None)
            ago = now_ - timestamp
            logger.info("Using cached Lagoon vaults file for chain %d from %s, last fetched at %s, ago %s", chain_id, file, timestamp.isoformat(), ago)

            if file_size == 0:
                return {}

            try:
                return json.load(open(file, "rt"))
            except JSONDecodeError as e:
                content = open(file, "rt").read()
                raise RuntimeError(f"Could not parse Lagoon vaults file for chain {chain_id} at {file}, length {len(content)} content starts with {content[:100]!r}") from e


def fetch_lagoon_vault_metadata(web3: Web3, vault_address: HexAddress) -> LagoonVaultMetadata | None:
    """Fetch vault metadata from Lagoon's offchain web app API.

    - Do both in-process and disk cache to avoid repeated fetches

    :param web3:
        Web3 instance (used to get chain_id and checksum address)

    :param vault_address:
        Vault contract address

    :return:
        Metadata dict or None if the vault is not in Lagoon's app database
    """
    global _cached_vaults

    chain_id = web3.eth.chain_id

    # Get per-chain copy of vault data into in-process cache
    if chain_id not in _cached_vaults:
        vaults = fetch_lagoon_vaults_for_chain(chain_id)
        _cached_vaults[chain_id] = vaults

    # Extract vault from Lagoon cache
    vaults = _cached_vaults[chain_id]
    if vaults:
        vault_address = Web3.to_checksum_address(vault_address)
        return vaults.get(vault_address)

    return None


#: In-process cache of fetched vaults
_cached_vaults: dict[int, dict[HexAddress, LagoonVaultMetadata]] = {}
