"""T3tris vault offchain metadata.

- T3tris stores vault page descriptions in their backend, not on-chain
- The detail endpoint ``/api/v1/pages/vault/{chainId}/{vaultAddress}``
  returns the same page view model shown in the T3tris app
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
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.utils import wait_other_writers

#: Where we cache fetched T3tris metadata files
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "t3tris"

#: T3tris API base URL, reverse-engineered from their Next.js frontend
DEFAULT_API_BASE_URL = "https://api.t3tris.finance/api/v1"

logger = logging.getLogger(__name__)


class T3trisVaultMetadata(TypedDict):
    """Metadata about a T3tris vault from offchain source.

    Fetched from the T3tris REST API endpoint:
    ``GET /api/v1/pages/vault/{chainId}/{vaultAddress}``.
    """

    #: Vault name shown in the app.
    name: str

    #: Vault share token symbol shown in the app.
    symbol: str | None

    #: Full Markdown vault description.
    description: str | None

    #: Human-readable curator or manager name.
    curator_name: str | None

    #: Curator website URL.
    curator_url: str | None

    #: T3tris UI verification flag.
    verified: bool | None

    #: UI-level deposit disable flag.
    deposits_disabled: bool | None

    #: App category label.
    category: str | None

    #: App attribute labels.
    attributes: list[str]

    #: App risk/rating label.
    rating: str | None

    #: Visibility flag used by the app.
    visibility: str | None

    #: Optional IPFS hash copied from app/indexer metadata.
    ipfs_hash: str | None


def _normalise_optional_string(value: object) -> str | None:
    """Normalise blank API string fields to ``None``."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _parse_vault_detail(raw: dict) -> T3trisVaultMetadata:
    """Parse vault metadata from the T3tris page endpoint.

    :param raw:
        Raw JSON dict from ``/api/v1/pages/vault/{chainId}/{vaultAddress}``
        or a single vault row from ``/api/v1/vaults``.
    """
    vault = raw.get("vault") or raw
    name = _normalise_optional_string(vault.get("displayName")) or _normalise_optional_string(vault.get("name")) or _normalise_optional_string(vault.get("onchainName")) or ""
    symbol = _normalise_optional_string(vault.get("displaySymbol")) or _normalise_optional_string(vault.get("symbol")) or _normalise_optional_string(vault.get("onchainSymbol"))
    attributes = vault.get("attributes")
    if not isinstance(attributes, list):
        attributes = []

    return T3trisVaultMetadata(
        name=name,
        symbol=symbol,
        description=_normalise_optional_string(vault.get("description")),
        curator_name=_normalise_optional_string(vault.get("curatorName")),
        curator_url=_normalise_optional_string(vault.get("curatorUrl")),
        verified=vault.get("verified") if isinstance(vault.get("verified"), bool) else None,
        deposits_disabled=vault.get("depositsDisabled") if isinstance(vault.get("depositsDisabled"), bool) else None,
        category=_normalise_optional_string(vault.get("category")),
        attributes=[str(item) for item in attributes],
        rating=_normalise_optional_string(vault.get("rating")),
        visibility=_normalise_optional_string(vault.get("visibility")),
        ipfs_hash=_normalise_optional_string(vault.get("ipfsHash")),
    )


def _fetch_vaults_list_entry(
    chain_id: int,
    address: str,
    api_base_url: str = DEFAULT_API_BASE_URL,
) -> dict | None:
    """Fetch a single vault row from T3tris' vault list endpoint.

    :param chain_id:
        EVM chain id.

    :param address:
        Vault contract address.

    :param api_base_url:
        T3tris API base URL.

    :return:
        Raw vault row, or ``None`` if the vault was not listed.
    """
    url = f"{api_base_url}/vaults"
    address_lower = address.lower()
    logger.debug("Fetching T3tris vault list from %s", url)
    try:
        resp = requests.get(url, timeout=30, headers={"Accept": "application/json"})
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, JSONDecodeError) as e:
        logger.warning("Failed to fetch T3tris vault list for %s on chain %d: %s", address, chain_id, e)
        return None

    vaults = payload.get("vaults") if isinstance(payload, dict) else None
    if not isinstance(vaults, list):
        logger.warning("Unexpected T3tris vault list response shape: %s", type(payload))
        return None

    for item in vaults:
        if not isinstance(item, dict):
            continue
        if item.get("chainId") == chain_id and str(item.get("address", "")).lower() == address_lower:
            return item

    return None


def _fetch_vault_detail(
    chain_id: int,
    address: str,
    api_base_url: str = DEFAULT_API_BASE_URL,
) -> dict | None:
    """Fetch detailed vault metadata from the T3tris web app API.

    :param chain_id:
        EVM chain id.

    :param address:
        Vault contract address.

    :param api_base_url:
        T3tris API base URL.

    :return:
        Raw JSON response dict, or ``None`` if the request fails.
    """
    url = f"{api_base_url}/pages/vault/{chain_id}/{address}"
    logger.debug("Fetching T3tris vault detail from %s", url)
    try:
        resp = requests.get(url, timeout=30, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, JSONDecodeError) as e:
        logger.warning("Failed to fetch T3tris vault detail for %s on chain %d: %s; falling back to vault list", address, chain_id, e)
        return _fetch_vaults_list_entry(chain_id, address, api_base_url=api_base_url)


def fetch_t3tris_vault_metadata(
    web3: Web3,
    vault_address: HexAddress,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = datetime.timedelta(days=2),
) -> T3trisVaultMetadata | None:
    """Fetch vault metadata from T3tris' offchain web app API.

    - Uses one JSON cache file per chain and vault
    - Multiprocess safe via file lock
    - Returns ``None`` when the vault is not available in the T3tris app API

    :param web3:
        Web3 instance used to get ``chain_id`` and checksum the vault address.

    :param vault_address:
        Vault contract address.

    :param cache_path:
        Directory for cache files.

    :param api_base_url:
        T3tris API base URL.

    :param now_:
        Override current time for testing.

    :param max_cache_duration:
        How long before refreshing cache.
    """
    chain_id = web3.eth.chain_id
    checksummed = Web3.to_checksum_address(vault_address)
    chain_cache = _cached_vaults.setdefault(chain_id, {})
    if checksummed in chain_cache:
        return chain_cache[checksummed]

    cache_path.mkdir(parents=True, exist_ok=True)
    file = (cache_path / f"t3tris_vault_{chain_id}_{checksummed.lower()}.json").resolve()
    file_size = file.stat().st_size if file.exists() else 0

    if not now_:
        now_ = native_datetime_utc_now()

    with wait_other_writers(file):
        should_refresh = not file.exists() or (now_ - native_datetime_utc_fromtimestamp(file.stat().st_mtime)) > max_cache_duration or file_size == 0
        if should_refresh:
            detail = _fetch_vault_detail(chain_id, checksummed, api_base_url=api_base_url)
            if detail is None:
                return None

            metadata = _parse_vault_detail(detail)
            with file.open("wt") as f:
                json.dump(metadata, f, indent=2)

            assert file.stat().st_size > 0, f"File {file} is empty after writing"
            chain_cache[checksummed] = metadata
            return metadata

        if file_size == 0:
            return None

        try:
            with file.open("rt") as f:
                metadata = json.load(f)
        except JSONDecodeError as e:
            content = file.read_text()
            raise RuntimeError(f"Could not parse T3tris vault file for {checksummed} on chain {chain_id} at {file}, length {len(content)} content starts with {content[:100]!r}") from e

        chain_cache[checksummed] = metadata
        return metadata


#: In-process cache of fetched vaults
_cached_vaults: dict[int, dict[HexAddress, T3trisVaultMetadata]] = {}
