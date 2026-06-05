"""Royco offchain vault metadata.

Royco exposes two first-party API surfaces that can contain vault-like rows:

- ``POST /api/v1/market/explore`` for Incentivised Action Market vault wrappers
- ``POST /api/v1/vault/explore`` for Royco Vault product rows

Both endpoints are authenticated with ``x-api-key``. Royco documents
``ROYCO_DEMO`` as the public demo key.

Relevant documentation:

- `Royco API <https://docs.royco.org/royco-api/getting-started-with-the-royco-api>`__
- `Royco developer overview <https://docs.royco.org/for-incentive-providers/developer-overview>`__
"""

import datetime
import json
import logging
import os
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Literal, TypedDict

import requests
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.utils import wait_other_writers

logger = logging.getLogger(__name__)


#: Where fetched Royco metadata files are cached.
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "royco"

#: Royco API base URL.
DEFAULT_API_BASE_URL = "https://api.royco.org/api/v1"

#: Public demo API key documented by Royco.
DEFAULT_ROYCO_API_KEY = "ROYCO_DEMO"


class RoycoOffchainVaultMetadata(TypedDict):
    """Royco offchain vault metadata.

    :ivar source:
        API source that produced the row: ``vault_explore`` or ``market_explore``.

    :ivar chain_id:
        EVM chain id.

    :ivar vault_address:
        Vault contract address to scan.

    :ivar underlying_vault_address:
        Underlying vault address for Royco Vault Market wrappers, when supplied.

    :ivar name:
        Royco display name.

    :ivar description:
        Royco description text.

    :ivar is_active:
        Whether the market row is active. Royco Vault product rows do not expose
        this flag and use ``None``.

    :ivar is_verified:
        Whether Royco marks the row verified.

    :ivar tvl_usd:
        Royco's USD TVL snapshot.

    :ivar share_price:
        Royco's share price string for Royco Vault product rows.

    :ivar last_updated:
        API row last-updated timestamp.

    :ivar raw:
        Original API row.
    """

    source: Literal["vault_explore", "market_explore"]
    chain_id: int
    vault_address: HexAddress
    underlying_vault_address: HexAddress | None
    name: str
    description: str | None
    is_active: bool | None
    is_verified: bool
    tvl_usd: float | None
    share_price: str | None
    last_updated: str | None
    raw: dict[str, Any]


def _post_royco_api(
    path: str,
    payload: dict[str, Any],
    api_base_url: str,
    api_key: str,
) -> dict[str, Any]:
    """Post a JSON request to Royco's API.

    :param path:
        API path under ``api_base_url``.

    :param payload:
        JSON request body.

    :param api_base_url:
        Royco API base URL.

    :param api_key:
        Royco API key.

    :return:
        Decoded JSON object.
    """
    url = f"{api_base_url.rstrip('/')}/{path.lstrip('/')}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "Authorization": f"Bearer {api_key}",
    }
    response = requests.post(url, json=payload, headers=headers, timeout=45)
    response.raise_for_status()
    return response.json()


def _fetch_paginated_royco_rows(
    path: str,
    payload: dict[str, Any],
    api_base_url: str,
    api_key: str,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    """Fetch all rows from a paginated Royco endpoint.

    Royco pagination is one-based and returns ``page.total`` as the number of
    pages.

    :param path:
        Endpoint path, e.g. ``"vault/explore"``.

    :param payload:
        Base request payload. The ``page`` key is overwritten.

    :param api_base_url:
        Royco API base URL.

    :param api_key:
        Royco API key.

    :param page_size:
        Number of rows per page. Royco's documented maximum is 500.

    :return:
        List of raw API rows.
    """
    rows: list[dict[str, Any]] = []
    page_index = 1

    while True:
        request_payload = dict(payload)
        request_payload["page"] = {
            "index": page_index,
            "size": page_size,
        }
        data = _post_royco_api(path, request_payload, api_base_url=api_base_url, api_key=api_key)
        rows.extend(data.get("data") or [])

        page = data.get("page") or {}
        total_pages = int(page.get("total") or 1)
        if page_index >= total_pages:
            break
        page_index += 1

    return rows


def _parse_float_or_none(value: Any) -> float | None:
    """Parse an optional floating point value."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalise_address(value: str | None) -> HexAddress | None:
    """Normalise an optional EVM address to checksum format."""
    if not value:
        return None
    return Web3.to_checksum_address(value)


def _parse_vault_explore_row(row: dict[str, Any]) -> RoycoOffchainVaultMetadata | None:
    """Parse a ``vault/explore`` row.

    :param row:
        Raw Royco API row.

    :return:
        Parsed metadata, or ``None`` if the row does not include a vault address.
    """
    vault_address = _normalise_address(row.get("vaultAddress"))
    if vault_address is None:
        return None

    return RoycoOffchainVaultMetadata(
        source="vault_explore",
        chain_id=int(row["chainId"]),
        vault_address=vault_address,
        underlying_vault_address=None,
        name=row.get("name") or "",
        description=row.get("description"),
        is_active=None,
        is_verified=bool(row.get("isVerified")),
        tvl_usd=_parse_float_or_none(row.get("tvlUsd")),
        share_price=row.get("sharePrice"),
        last_updated=row.get("lastUpdated"),
        raw=row,
    )


def _parse_market_explore_row(row: dict[str, Any]) -> RoycoOffchainVaultMetadata | None:
    """Parse a ``market/explore`` Vault Market row.

    :param row:
        Raw Royco API row.

    :return:
        Parsed metadata, or ``None`` if the row does not include a market id.
    """
    vault_address = _normalise_address(row.get("marketId"))
    if vault_address is None:
        return None

    return RoycoOffchainVaultMetadata(
        source="market_explore",
        chain_id=int(row["chainId"]),
        vault_address=vault_address,
        underlying_vault_address=_normalise_address(row.get("underlyingVaultAddress")),
        name=row.get("name") or "",
        description=row.get("description"),
        is_active=bool(row.get("isActive")),
        is_verified=bool(row.get("isVerified")),
        tvl_usd=_parse_float_or_none(row.get("tvlUsd")),
        share_price=None,
        last_updated=row.get("lastUpdated"),
        raw=row,
    )


def _fetch_royco_metadata_rows(
    api_base_url: str = DEFAULT_API_BASE_URL,
    api_key: str = DEFAULT_ROYCO_API_KEY,
) -> dict[HexAddress, RoycoOffchainVaultMetadata]:
    """Fetch Royco vault metadata from first-party APIs.

    :param api_base_url:
        Royco API base URL.

    :param api_key:
        Royco API key.

    :return:
        Metadata mapped by checksummed vault address.
    """
    result: dict[HexAddress, RoycoOffchainVaultMetadata] = {}

    vault_rows = _fetch_paginated_royco_rows(
        "vault/explore",
        payload={},
        api_base_url=api_base_url,
        api_key=api_key,
    )
    for row in vault_rows:
        metadata = _parse_vault_explore_row(row)
        if metadata is not None:
            result[metadata["vault_address"]] = metadata

    market_rows = _fetch_paginated_royco_rows(
        "market/explore",
        payload={
            "filters": [
                {
                    "id": "marketType",
                    "value": 1,
                    "condition": "eq",
                }
            ],
        },
        api_base_url=api_base_url,
        api_key=api_key,
    )
    for row in market_rows:
        metadata = _parse_market_explore_row(row)
        if metadata is not None:
            result.setdefault(metadata["vault_address"], metadata)

    return result


def fetch_royco_vaults(
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    api_key: str | None = None,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = datetime.timedelta(days=2),
) -> dict[HexAddress, RoycoOffchainVaultMetadata]:
    """Fetch and cache Royco offchain vault metadata.

    :param cache_path:
        Directory for cache files.

    :param api_base_url:
        Royco API base URL.

    :param api_key:
        Royco API key. Defaults to ``ROYCO_API_KEY`` or ``ROYCO_DEMO``.

    :param now_:
        Override current time for tests.

    :param max_cache_duration:
        How long before refreshing cache.

    :return:
        Metadata mapped by checksummed vault address.
    """
    assert isinstance(cache_path, Path), "cache_path must be Path instance"

    if api_key is None:
        api_key = os.environ.get("ROYCO_API_KEY", DEFAULT_ROYCO_API_KEY)

    cache_path.mkdir(parents=True, exist_ok=True)
    file = (cache_path / "royco_vaults.json").resolve()
    file_size = file.stat().st_size if file.exists() else 0

    if not now_:
        now_ = native_datetime_utc_now()

    with wait_other_writers(file):
        if not file.exists() or (now_ - native_datetime_utc_fromtimestamp(file.stat().st_mtime)) > max_cache_duration or file_size == 0:
            logger.info("Re-fetching Royco vault metadata from %s", api_base_url)
            try:
                result = _fetch_royco_metadata_rows(api_base_url=api_base_url, api_key=api_key)
            except (requests.RequestException, JSONDecodeError, KeyError, ValueError) as e:
                logger.warning("Failed to fetch Royco vault metadata: %s", e)
                return {}

            logger.info("Fetched metadata for %d Royco vault rows", len(result))
            if not result:
                logger.warning("Royco API returned 0 vault rows, skipping cache write to avoid poisoning the cache")
                return {}

            with file.open("wt") as f:
                json.dump(result, f, indent=2)

            assert file.stat().st_size > 0, f"File {file} is empty after writing"
            return result

        timestamp = native_datetime_utc_fromtimestamp(file.stat().st_mtime)
        ago = now_ - timestamp
        logger.info("Using cached Royco vault metadata from %s, last fetched at %s, ago %s", file, timestamp.isoformat(), ago)

        if file_size == 0:
            return {}

        try:
            with file.open("rt") as f:
                return json.load(f)
        except JSONDecodeError as e:
            content = file.read_text()
            raise RuntimeError(f"Could not parse Royco vault metadata file at {file}, length {len(content)} content starts with {content[:100]!r}") from e


def fetch_royco_vault_metadata(web3: Web3, vault_address: HexAddress) -> RoycoOffchainVaultMetadata | None:
    """Fetch Royco metadata for a vault address.

    :param web3:
        Web3 instance, used for checksum normalisation.

    :param vault_address:
        Vault contract address.

    :return:
        Royco metadata dict or ``None`` if the vault is not in Royco's API.
    """
    global _cached_vaults

    if _cached_vaults is None:
        _cached_vaults = fetch_royco_vaults()

    if _cached_vaults:
        checksummed = web3.to_checksum_address(vault_address)
        return _cached_vaults.get(checksummed)

    return None


#: In-process cache of fetched Royco vaults.
_cached_vaults: dict[HexAddress, RoycoOffchainVaultMetadata] | None = None
