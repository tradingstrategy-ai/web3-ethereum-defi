"""Arcadia-maintained lending pool metadata.

The `public pools API <https://api.arcadia.finance/redoc>`__ lists lending
pools and their ERC-4626 tranches. The scanner indexes the **tranche** address,
not the parent lending pool address. Addresses can be shared across chains.
Only descriptive fields are retained; live rates are not website copy.
"""

import datetime
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.utils import wait_other_writers

logger = logging.getLogger(__name__)

#: Official API endpoint, queried with ``chain_id``.
POOLS_API_URL = "https://api.arcadia.finance/v1/api/pools"
#: Persistent cache of complete per-chain API responses.
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "arcadia"
#: Descriptive metadata refresh interval.
DEFAULT_CACHE_DURATION = datetime.timedelta(days=1)
#: Backoff after an unavailable or malformed response.
UNAVAILABLE_RETRY_DELAY = datetime.timedelta(minutes=5)


@dataclass(slots=True, frozen=True)
class ArcadiaVaultMetadata:
    """Describe one ERC-4626 tranche using its parent pool record.

    Fields are normalised from the official API, with optional text left
    unknown when absent. Financial metrics are deliberately excluded.
    """

    #: Chain on which the tranche is deployed.
    chain_id: int
    #: Lowercase tranche address, matching the scanner's vault identity.
    tranche_address: HexAddress
    #: Lowercase parent lending pool address.
    pool_address: HexAddress
    #: Pool display name, normally the underlying token symbol.
    pool_name: str | None
    #: Explicit tranche display name, such as ``Senior USDG``.
    tranche_name: str | None
    #: Provider copy, which may be only a label like ``Arcadia USDG Pool``.
    description: str | None


@dataclass(slots=True)
class _ChainCache:
    """Keep a refreshable index and failure backoff for one worker.

    An older successful index remains available during a temporary outage.
    """

    #: Latest valid tranche index.
    index: dict[HexAddress, ArcadiaVaultMetadata] | None = None
    #: Time of the last successful response or disk-cache timestamp.
    fetched_at: datetime.datetime | None = None
    #: Earliest next request after a failed fetch.
    retry_after: datetime.datetime | None = None


#: Cache keys include source and directory so isolated callers do not share state.
_chain_caches: dict[tuple[int, Path, str], _ChainCache] = {}
#: Serialise worker threads so multiple vaults share one request per chain.
_cache_lock = threading.RLock()


def normalise_text(value: object) -> str | None:
    """Trim optional API text without coercing malformed objects to strings.

    Missing and whitespace-only fields remain unknown.

    :param value: Raw optional text field.
    :return: Trimmed text, or ``None``.
    """
    return (value.strip() or None) if isinstance(value, str) else None


def parse_arcadia_pools(chain_id: int, payload: object) -> dict[HexAddress, ArcadiaVaultMetadata]:
    """Index all tranches in one complete pool response.

    An empty array is a valid chain without pools. Invalid identity fields or
    malformed record shapes reject the response rather than caching bad data.

    :param chain_id: Chain requested from the API.
    :param payload: Decoded API response array.
    :return: Lowercase tranche-address index for this chain.
    :raises ValueError: If the response does not contain valid pool records.
    """
    if not isinstance(payload, list):
        message = "Arcadia pool response must be an array"
        raise ValueError(message)
    index = {}
    for pool in payload:
        if not isinstance(pool, dict) or not Web3.is_address(pool.get("address")) or not isinstance(pool.get("tranches"), list):
            message = "Arcadia pool record needs a pool address and tranches array"
            raise ValueError(message)
        for tranche in pool["tranches"]:
            if not isinstance(tranche, dict) or not Web3.is_address(tranche.get("address")):
                message = "Arcadia tranche record needs a valid address"
                raise ValueError(message)
            address = HexAddress(tranche["address"].lower())
            if address in index:
                raise ValueError(f"Duplicate Arcadia tranche address on chain {chain_id}: {address}")
            index[address] = ArcadiaVaultMetadata(
                chain_id=chain_id,
                tranche_address=address,
                pool_address=HexAddress(pool["address"].lower()),
                pool_name=normalise_text(pool.get("name")),
                tranche_name=normalise_text(tranche.get("name")),
                description=normalise_text(pool.get("description")),
            )
    return index


def _load_cache(chain_id: int, file: Path) -> _ChainCache | None:
    """Read a validated disk index with its original refresh timestamp.

    Invalid caches are treated as a miss and replaced only after a successful
    provider response.

    :param chain_id: Chain represented by the cache file.
    :param file: Complete response JSON path.
    :return: Validated cache state, or ``None`` if missing or corrupt.
    """
    try:
        if file.exists():
            return _ChainCache(index=parse_arcadia_pools(chain_id, json.loads(file.read_text())), fetched_at=native_datetime_utc_fromtimestamp(file.stat().st_mtime))
    except (OSError, ValueError) as error:
        logger.warning("Could not load Arcadia metadata cache for chain %d: %s; refreshing", chain_id, error)
    return None


def _write_cache(file: Path, payload: list) -> None:
    """Atomically store a validated provider response.

    Clean up temporary files even if the write or replacement fails.

    :param file: Target JSON cache file.
    :param payload: Validated complete API response.
    :return: ``None`` after replacing the file.
    """
    temporary_file = None
    try:
        with NamedTemporaryFile(mode="wt", encoding="utf-8", dir=file.parent, prefix=f".{file.name}.", delete=False) as output:
            temporary_file = Path(output.name)
            json.dump(payload, output)
        temporary_file.replace(file)
    finally:
        if temporary_file is not None:
            temporary_file.unlink(missing_ok=True)


def fetch_arcadia_vaults(
    chain_id: int,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_url: str = POOLS_API_URL,
    now_: datetime.datetime | None = None,
) -> dict[HexAddress, ArcadiaVaultMetadata] | None:
    """Fetch the complete Arcadia tranche index once per chain and day.

    Requests use a 30-second timeout. A failed refresh preserves a valid older
    index and delays further requests for five minutes. Without an index,
    adapters can still generate copy from their onchain metadata.

    :param chain_id: EVM chain ID, passed as the API's ``chain_id`` parameter.
    :param cache_path: Directory for atomic JSON response caches.
    :param api_url: Official pools endpoint, overridable for testing.
    :param now_: Optional naive UTC clock for deterministic expiry tests.
    :return: Tranche index, or ``None`` when no valid response is available.
    """
    now_ = now_ or native_datetime_utc_now()
    file = cache_path.resolve() / f"pools-{chain_id}.json"
    with _cache_lock:
        state = _chain_caches.setdefault((chain_id, file, api_url), _ChainCache())
        if state.retry_after is not None and now_ < state.retry_after:
            return state.index
        if state.fetched_at is not None and now_ - state.fetched_at < DEFAULT_CACHE_DURATION:
            return state.index

        try:
            file.parent.mkdir(parents=True, exist_ok=True)
            with wait_other_writers(file, timeout=30):
                cached = _load_cache(chain_id, file)
                if cached is not None and (state.fetched_at is None or cached.fetched_at > state.fetched_at):
                    state.index, state.fetched_at = cached.index, cached.fetched_at
                    if now_ - cached.fetched_at < DEFAULT_CACHE_DURATION:
                        return cached.index

                logger.info("Fetching Arcadia pool metadata for chain %d", chain_id)
                response = requests.get(api_url, params={"chain_id": chain_id}, timeout=30)
                response.raise_for_status()
                payload = response.json()
                index = parse_arcadia_pools(chain_id, payload)
                state.index, state.fetched_at, state.retry_after = index, now_, None
                try:
                    _write_cache(file, payload)
                except OSError as error:
                    logger.warning("Could not cache Arcadia metadata for chain %d: %s", chain_id, error)
                logger.info("Fetched %d Arcadia tranche records for chain %d", len(index), chain_id)
                return index
        except (requests.RequestException, ValueError, OSError, TimeoutError) as error:
            state.retry_after = now_ + UNAVAILABLE_RETRY_DELAY
            logger.warning("Arcadia metadata unavailable for chain %d: %s; using cached metadata or known fields, retry after %s", chain_id, error, state.retry_after)
            return state.index


def fetch_arcadia_vault_metadata(chain_id: int, vault_address: HexAddress) -> ArcadiaVaultMetadata | None:
    """Look up an Arcadia tranche without requesting each vault separately.

    A miss in a successful index remains cached for the normal daily refresh.

    :param chain_id: Chain on which the tranche is deployed.
    :param vault_address: ERC-4626 tranche address, in any case.
    :return: Provider metadata when listed, otherwise ``None``.
    """
    index = fetch_arcadia_vaults(chain_id)
    metadata = index.get(HexAddress(vault_address.lower())) if index is not None else None
    if index is not None and metadata is None:
        logger.warning("Arcadia tranche %s on chain %d is absent from the pool API; generating descriptions from known fields", vault_address, chain_id)
    return metadata
