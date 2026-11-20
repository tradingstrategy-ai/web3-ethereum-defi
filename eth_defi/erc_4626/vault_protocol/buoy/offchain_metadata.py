"""Optional descriptive Buoy metadata, with Lagoon-style expiring caches.

The public API at https://docs.buoy.finance/developers/rest-api supplies text
and images only. Onchain identity, economics and admission never depend on it.
"""

import datetime
import hashlib
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, TypedDict

import requests
from atomicwrites import atomic_write
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.erc_4626.vault_protocol.buoy.constants import BUOY_CHAIN_ID
from eth_defi.utils import wait_other_writers
from eth_defi.vault.base import normalise_vault_description, normalise_vault_short_description

logger = logging.getLogger(__name__)
DEFAULT_API_BASE_URL = "https://europe-west1-buoy-loan.cloudfunctions.net/app/api/v1"
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "buoy"
_cache_override: ContextVar[Path | None] = ContextVar("buoy_metadata_cache", default=None)
DEFAULT_CACHE_DURATION = datetime.timedelta(days=1)
FAILED_REFRESH_RETRY_DELAY = datetime.timedelta(hours=1)
#: Source/directory/chain/TTL -> expiry and descriptive records; never economic data.
_memory: dict[tuple[str, str, int, datetime.timedelta], tuple[datetime.datetime, dict[str, "BuoyVaultMetadata"]]] = {}


class BuoyVaultMetadata(TypedDict):
    """Descriptive subset of a chain-address-validated API record."""

    #: Normalised Markdown strategy text.
    description: str | None
    #: One-sentence plain-text summary.
    short_description: str | None
    #: Optional public vault image.
    logo_url: str | None
    #: Optional public leader social handle; not an onchain identity.
    x_handle: str | None


@contextmanager
def use_buoy_metadata_cache(cache_path: Path) -> Iterator[None]:
    """Isolate optional provider cache writes during maintenance.

    The override is scoped to the calling context and restores the previous
    directory even on failure. Production migrations use a temporary directory
    so preview cannot modify the scanner's persistent metadata cache.

    :param cache_path: Caller-owned cache directory.
    :return: Context manager for subsequent metadata reads.
    """
    token = _cache_override.set(cache_path)
    try:
        yield
    finally:
        _cache_override.reset(token)
        for key in tuple(_memory):
            if key[0] == str(cache_path.resolve()):
                del _memory[key]


def _parse_metadata(raw: dict, chain_id: int) -> tuple[str, BuoyVaultMetadata]:
    """Validate identity and discard every API economic field.

    Normalisation also applies when loading disk caches through this module's
    public accessor, so old provider text cannot bypass formatting rules.

    :param raw: One API vault record.
    :param chain_id: Expected chain.
    :return: Lowercase address and descriptive fields.
    :raises ValueError: Wrong chain, invalid address or invalid text fields.
    """
    if raw.get("chainId") != chain_id or not Web3.is_address(raw.get("address", "")):
        raise ValueError("Buoy metadata has invalid chain/address identity")
    for field in ("description", "tagline", "image", "xHandle"):
        if raw.get(field) is not None and not isinstance(raw[field], str):
            raise ValueError(f"Invalid Buoy metadata field {field}")
    return raw["address"].lower(), BuoyVaultMetadata(
        description=normalise_vault_description(raw.get("description")),
        short_description=normalise_vault_short_description(raw.get("tagline") or raw.get("description")),
        logo_url=raw.get("image"),
        x_handle=raw.get("xHandle"),
    )


def fetch_buoy_vaults_for_chain(
    chain_id: int,
    cache_path: Path | None = None,
    api_base_url: str = DEFAULT_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION,
) -> dict[str, BuoyVaultMetadata]:
    """Fetch optional descriptions without making the API a discovery authority.

    One-day disk/process caches expire inside long-running scanner processes.
    Failed refreshes preserve old records and their original successful fetch
    time, while a shorter process cooldown prevents per-vault retry bursts.
    Pagination must finish before replacing a known-good file.

    :param chain_id: Expected chain; other chains return no metadata.
    :param cache_path: Cache directory, isolated in tests.
    :param api_base_url: Public API source, overridable for tests.
    :param now_: Optional deterministic naive UTC clock.
    :param max_cache_duration: Successful response cache lifetime.
    :return: Descriptive records keyed by lowercase address.
    :raises ValueError: Corrupt local cache; never silently reset it.
    """
    if chain_id != BUOY_CHAIN_ID:
        return {}
    cache_path = cache_path if cache_path is not None else (_cache_override.get() or DEFAULT_CACHE_PATH)
    now = now_ or native_datetime_utc_now()
    key = (str(cache_path.resolve()), api_base_url, chain_id, max_cache_duration)
    cached = _memory.get(key)
    if cached and now < cached[0]:
        return cached[1]
    cache_path.mkdir(parents=True, exist_ok=True)
    # Source identity belongs in the disk cache too, not only the process key.
    source = hashlib.sha256(api_base_url.encode()).hexdigest()[:12]
    file = cache_path / f"vaults-{chain_id}-{source}.json"
    with wait_other_writers(file):
        records = {}
        fetched_at = None
        if file.exists():
            saved = json.loads(file.read_text(encoding="utf-8"))
            if saved["chain_id"] != chain_id or saved["source"] != api_base_url:
                raise ValueError("Buoy cache identity mismatch")
            fetched_at = datetime.datetime.fromisoformat(saved["fetched_at"])
            records = saved["vaults"]
            fields = {"description", "short_description", "logo_url", "x_handle"}
            if not isinstance(records, dict) or not records:
                raise ValueError("Invalid Buoy metadata cache")
            for address, metadata in records.items():
                if not isinstance(address, str) or not Web3.is_address(address) or address != address.lower() or not isinstance(metadata, dict) or set(metadata) != fields:
                    raise ValueError("Invalid Buoy cached record identity or fields")
                if any(value is not None and not isinstance(value, str) for value in metadata.values()):
                    raise ValueError("Invalid Buoy cached text value")
                metadata["description"] = normalise_vault_description(metadata["description"])
                metadata["short_description"] = normalise_vault_short_description(metadata["short_description"])
            if now < fetched_at + max_cache_duration:
                _memory[key] = (fetched_at + max_cache_duration, records)
                return records
        try:
            fresh = {}
            offset = 0
            while True:
                response = requests.get(f"{api_base_url}/vaults", params={"offset": offset, "limit": 100}, timeout=30)
                response.raise_for_status()
                page = response.json()
                items = page["items"]
                if not isinstance(items, list) or not items:
                    raise ValueError("Empty or malformed Buoy listing")
                for raw in items:
                    address, metadata = _parse_metadata(raw, chain_id)
                    if address in fresh:
                        raise ValueError("Repeated Buoy metadata page/address")
                    fresh[address] = metadata
                pagination = page["page"]
                if pagination["offset"] != offset or type(pagination["hasMore"]) is not bool:
                    raise ValueError("Invalid Buoy pagination")
                if not pagination["hasMore"]:
                    break
                offset += len(items)
            # Omitted entries are not deletions: the onchain catalogue remains valid.
            records = records | fresh
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            logger.warning("Buoy metadata refresh deferred on chain %s: %s", chain_id, type(error).__name__)
            _memory[key] = (now + FAILED_REFRESH_RETRY_DELAY, records)
            return records
        with atomic_write(str(file), mode="w", overwrite=True, encoding="utf-8") as output:
            json.dump({"source": api_base_url, "chain_id": chain_id, "fetched_at": now.isoformat(), "vaults": records}, output)
        _memory[key] = (now + max_cache_duration, records)
        return records


def fetch_buoy_vault_metadata(web3: Web3, vault_address: HexAddress, **kwargs: Any) -> BuoyVaultMetadata | None:
    """Look up descriptions for an already verified onchain vault.

    This helper never admits API-only addresses into the scanner. Callers
    establish registry membership independently through the adapter.

    :param web3: Connection used only to select the chain.
    :param vault_address: Verified vault identity.
    :param kwargs: Cache/source/clock overrides forwarded to the chain fetcher.
    :return: Normalised descriptive metadata, or no enrichment.
    """
    metadata = fetch_buoy_vaults_for_chain(web3.eth.chain_id, **kwargs).get(vault_address.lower())
    if metadata is None:
        return None
    return {**metadata, "description": normalise_vault_description(metadata.get("description")), "short_description": normalise_vault_short_description(metadata.get("short_description"))}
