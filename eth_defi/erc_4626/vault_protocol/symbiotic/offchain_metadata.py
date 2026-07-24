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
from json import JSONDecodeError
from pathlib import Path
from typing import TypedDict

import requests
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.utils import wait_other_writers

logger = logging.getLogger(__name__)

#: Cache directory for official Symbiotic metadata.
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "symbiotic"

#: Official GitHub contents API for the metadata repository used by Symbiotic's app.
DEFAULT_API_BASE_URL = "https://api.github.com/repos/symbioticfi/metadata-mainnet/contents"

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
