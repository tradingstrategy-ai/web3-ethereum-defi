"""Symbiotic Core V2 vault metadata from the official metadata API.

Symbiotic curators submit vault and curator descriptions to the reviewed
``metadata-mainnet`` repository. The Symbiotic application uses the merged
repository data to display vault metadata. This module retrieves an individual
vault through GitHub's public contents API and stores the normalised result in
a two-day local cache.

See `Submit Metadata <https://docs.symbiotic.fi/integrate/curators/submit-metadata>`__.
"""

import base64
import binascii
import datetime
import json
import logging
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from json import JSONDecodeError
from pathlib import Path
from typing import TypedDict, cast

import requests
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.utils import wait_other_writers

logger = logging.getLogger(__name__)

#: Cache directory for official Symbiotic metadata.
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "symbiotic"

#: Official GitHub contents API for the metadata repository used by Symbiotic's app.
DEFAULT_API_BASE_URL = "https://api.github.com/repos/symbioticfi/metadata-mainnet/contents"

#: Symbiotic application's public mainnet data API.
DEFAULT_APP_API_BASE_URL = "https://app.symbiotic.fi/api/v3"

#: Chain represented by the public application API.
DEFAULT_APP_CHAIN_NAME = "Ethereum"

#: How long to reuse an official metadata response before refresh.
DEFAULT_CACHE_DURATION = datetime.timedelta(days=2)

#: HTTP status returned by the GitHub contents API for an unregistered entity.
HTTP_NOT_FOUND_STATUS = 404


class SymbioticMetadataLink(TypedDict):
    """A user-facing link published in Symbiotic metadata."""

    #: Link category such as ``website`` or ``externalLink``.
    type: str

    #: User-facing label.
    name: str

    #: Destination URL.
    url: str


class SymbioticVaultMetadata(TypedDict):
    """Normalised official metadata for an individual Symbiotic vault."""

    #: Vault display name.
    name: str

    #: Curator-provided strategy description.
    description: str | None

    #: Tags selected by the curator.
    tags: list[str]

    #: Vault-specific user-facing links.
    links: list[SymbioticMetadataLink]

    #: Curator identifier from the official metadata repository.
    curator_id: str | None

    #: Curator display name, when its metadata record exists.
    curator_name: str | None

    #: Curator description, when its metadata record exists.
    curator_description: str | None

    #: Curator user-facing links, when its metadata record exists.
    curator_links: list[SymbioticMetadataLink]


class SymbioticOffchainVault(TypedDict):
    """A vault record from Symbiotic's public application data API."""

    #: ERC-4626 vault address.
    address: HexAddress

    #: User-facing vault name, if the curator has submitted it.
    name: str | None

    #: Human-readable chain name for the application API deployment.
    chain_name: str

    #: Curator display name, or its identifier if no display name is available.
    curator_name: str | None

    #: Current USD total value locked as calculated by Symbiotic's API.
    tvl: Decimal | None

    #: Symbiotic vault implementation category, such as ``v2``.
    vault_type: str


def _fetch_metadata_file(path: str, api_base_url: str) -> dict | None:
    """Fetch and decode one JSON file from Symbiotic's public metadata API.

    :param path:
        Repository-relative metadata path, such as
        ``vaults/0x.../info.json``.
    :param api_base_url:
        GitHub contents API base URL, overridable for testing.
    :return:
        Decoded JSON dictionary, or ``None`` when the file is not registered.
    """
    url = f"{api_base_url}/{path}"
    response = requests.get(url, headers={"Accept": "application/vnd.github+json"}, timeout=30)
    if response.status_code == HTTP_NOT_FOUND_STATUS:
        return None
    response.raise_for_status()
    payload = response.json()
    if payload.get("encoding") != "base64":
        message = f"Unsupported Symbiotic metadata encoding for {path}: {payload.get('encoding')!r}"
        raise ValueError(message)
    return json.loads(base64.b64decode(payload["content"]).decode("utf-8"))


def _normalise_links(raw_links: object) -> list[SymbioticMetadataLink]:
    """Keep valid user-facing links from a metadata record.

    :param raw_links:
        Untrusted ``links`` value from the official JSON file.
    :return:
        Normalised user-facing links.
    """
    if not isinstance(raw_links, list):
        return []

    links: list[SymbioticMetadataLink] = []
    for raw_link in raw_links:
        if not isinstance(raw_link, dict):
            continue
        link_type = raw_link.get("type")
        name = raw_link.get("name")
        url = raw_link.get("url")
        if isinstance(link_type, str) and isinstance(name, str) and isinstance(url, str):
            links.append(SymbioticMetadataLink(type=link_type, name=name, url=url))
    return links


def _parse_vault_metadata(raw_vault: dict, raw_curator: dict | None) -> SymbioticVaultMetadata:
    """Normalise raw vault and curator metadata API records.

    :param raw_vault:
        Decoded vault ``info.json`` record.
    :param raw_curator:
        Decoded curator ``info.json`` record, if available.
    :return:
        Normalised vault metadata.
    """
    curator_id = raw_vault.get("curatorId")
    tags = raw_vault.get("tags")
    return SymbioticVaultMetadata(
        name=raw_vault.get("name") if isinstance(raw_vault.get("name"), str) else "",
        description=raw_vault.get("description") if isinstance(raw_vault.get("description"), str) else None,
        tags=[tag for tag in tags if isinstance(tag, str)] if isinstance(tags, list) else [],
        links=_normalise_links(raw_vault.get("links")),
        curator_id=curator_id if isinstance(curator_id, str) else None,
        curator_name=raw_curator.get("name") if raw_curator and isinstance(raw_curator.get("name"), str) else None,
        curator_description=raw_curator.get("description") if raw_curator and isinstance(raw_curator.get("description"), str) else None,
        curator_links=_normalise_links(raw_curator.get("links")) if raw_curator else [],
    )


def _read_cache(file: Path) -> SymbioticVaultMetadata | None:
    """Read one cached vault metadata response.

    :param file:
        Cache JSON file.
    :return:
        Normalised metadata or ``None`` for a cached unregistered vault.
    """
    with file.open("rt", encoding="utf-8") as input_file:
        cached = json.load(input_file)
    return cached["metadata"]


def fetch_symbiotic_vault_metadata(
    vault_address: str,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION,
) -> SymbioticVaultMetadata | None:
    """Fetch official offchain metadata for a Symbiotic Ethereum vault.

    The official repository stores directory names as EIP-55 checksum
    addresses. A vault response may refer to an optional curator record, which
    is fetched at the same time so callers can display a curator name.

    :param vault_address:
        Symbiotic Ethereum vault address.
    :param cache_path:
        Directory for this module's per-vault JSON cache files.
    :param api_base_url:
        Official contents API base URL, overridable for tests.
    :param now_:
        Naive UTC current time override for deterministic cache tests.
    :param max_cache_duration:
        Cache time-to-live.
    :return:
        Vault metadata, or ``None`` when no official record exists or the API
        cannot be reached.
    """
    assert isinstance(cache_path, Path), "cache_path must be a Path"
    checksum_address = Web3.to_checksum_address(vault_address)
    cache_path.mkdir(parents=True, exist_ok=True)
    cache_file = (cache_path / f"{checksum_address.lower()}.json").resolve()
    now_ = now_ or native_datetime_utc_now()

    with wait_other_writers(cache_file):
        is_stale = not cache_file.exists() or cache_file.stat().st_size == 0
        if not is_stale:
            cached_at = native_datetime_utc_fromtimestamp(cache_file.stat().st_mtime)
            is_stale = now_ - cached_at > max_cache_duration

        if not is_stale:
            try:
                return _read_cache(cache_file)
            except (JSONDecodeError, KeyError) as error:
                logger.warning("Could not read Symbiotic metadata cache %s: %s", cache_file, error)

        try:
            raw_vault = _fetch_metadata_file(f"vaults/{checksum_address}/info.json", api_base_url)
            raw_curator = None
            if raw_vault:
                curator_id = raw_vault.get("curatorId")
                if isinstance(curator_id, str):
                    raw_curator = _fetch_metadata_file(f"curators/{curator_id}/info.json", api_base_url)
            metadata = _parse_vault_metadata(raw_vault, raw_curator) if raw_vault else None
        except (requests.RequestException, JSONDecodeError, UnicodeDecodeError, binascii.Error, KeyError, ValueError) as error:
            logger.warning("Could not fetch Symbiotic metadata for %s: %s", checksum_address, error)
            if cache_file.exists() and cache_file.stat().st_size > 0:
                try:
                    return _read_cache(cache_file)
                except (JSONDecodeError, KeyError) as cache_error:
                    logger.warning("Could not use stale Symbiotic metadata cache %s: %s", cache_file, cache_error)
            return None

        with cache_file.open("wt", encoding="utf-8") as output_file:
            json.dump({"metadata": metadata}, output_file, indent=2)
        return metadata


def _fetch_application_data(path: str, api_base_url: str) -> object:
    """Fetch one unmodified payload from Symbiotic's public application API.

    :param path:
        Endpoint path below the versioned API base URL.
    :param api_base_url:
        Symbiotic application API base URL, overridable for tests.
    :return:
        Decoded JSON payload.
    """
    response = requests.get(f"{api_base_url}/{path}", timeout=30)
    response.raise_for_status()
    return response.json()


def _parse_curator_names(raw_curators: object) -> dict[str, str]:
    """Extract curator display names from an application API response.

    :param raw_curators:
        Decoded ``curators`` response from the public API.
    :return:
        Mapping from curator identifiers to display names.
    """
    if not isinstance(raw_curators, list):
        raise ValueError(f"Expected a list of Symbiotic curators, got {type(raw_curators).__name__}")

    curator_names: dict[str, str] = {}
    for raw_curator in raw_curators:
        if not isinstance(raw_curator, dict):
            continue
        curator_id = raw_curator.get("id")
        raw_meta = raw_curator.get("meta")
        name = raw_meta.get("name") if isinstance(raw_meta, dict) else None
        if isinstance(curator_id, str) and isinstance(name, str):
            curator_names[curator_id] = name
    return curator_names


def fetch_symbiotic_offchain_vaults(
    *,
    api_base_url: str = DEFAULT_APP_API_BASE_URL,
    chain_name: str = DEFAULT_APP_CHAIN_NAME,
    vault_type: str | None = "v2",
) -> Iterator[SymbioticOffchainVault]:
    """Yield vault rows from Symbiotic's public application data API.

    The application API currently serves Ethereum mainnet. It publishes its
    USD TVL calculation and joins curator display names in a separate
    ``curators`` response, so this function retrieves both small payloads and
    yields a normalised reporting record for each vault.

    :param api_base_url:
        Symbiotic application API base URL, overridable for testing.
    :param chain_name:
        Human-readable name for the chain served by ``api_base_url``.
    :param vault_type:
        Optional implementation category filter. The default ``"v2"`` limits
        results to vaults that this integration can index; use ``None`` to
        yield every API vault type.
    :return:
        Iterator yielding normalised offchain vault records.
    """
    raw_vaults = _fetch_application_data("vaults", api_base_url)
    curator_names = _parse_curator_names(_fetch_application_data("curators", api_base_url))
    if not isinstance(raw_vaults, list):
        raise ValueError(f"Expected a list of Symbiotic vaults, got {type(raw_vaults).__name__}")

    for raw_vault in raw_vaults:
        if not isinstance(raw_vault, dict):
            continue
        raw_type = raw_vault.get("type")
        address = raw_vault.get("address")
        if not isinstance(raw_type, str) or not isinstance(address, str):
            continue
        if vault_type is not None and raw_type != vault_type:
            continue

        raw_meta = raw_vault.get("meta")
        name = raw_meta.get("name") if isinstance(raw_meta, dict) else None
        curator_id = raw_vault.get("curator")
        raw_tvl = raw_vault.get("tvl")
        try:
            tvl = Decimal(str(raw_tvl)) if raw_tvl is not None else None
        except InvalidOperation:
            logger.warning("Ignoring invalid Symbiotic TVL %r for vault %s", raw_tvl, address)
            tvl = None

        yield SymbioticOffchainVault(
            address=cast(HexAddress, address),
            name=name if isinstance(name, str) else None,
            chain_name=chain_name,
            curator_name=curator_names.get(curator_id, curator_id) if isinstance(curator_id, str) else None,
            tvl=tvl,
            vault_type=raw_type,
        )
