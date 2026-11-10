"""Lagoon vault offchain metadata.

- Lagoon stores vault descriptions in their web app, not onchain or in a public data repository
- We reverse-engineered the Lagoon Next.js app and discovered web application API endpoints
  at ``app.lagoon.finance`` that serve vault metadata including descriptions
- The listing endpoint ``/api/vaults`` returns paginated vault data without descriptions
- The detail endpoint ``/api/vault`` returns full vault data including ``description`` and ``shortDescription``
- We fetch and cache this data locally to avoid repeated API calls
- Separate catalogue and exact-address clocks, with atomic shared disk storage
- Active descriptions refresh daily; empty/closed detail refreshes weekly on demand
"""

import copy
import datetime
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Literal, TypedDict

import requests
from atomicwrites import atomic_write
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.erc_4626.vault_protocol.lagoon.constants import KAMUI_LAGOON_VAULTS
from eth_defi.utils import wait_other_writers

#: Where we cache fetched Lagoon metadata files
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "lagoon"

#: Lagoon web app API base URL, reverse-engineered from their Next.js frontend
DEFAULT_API_BASE_URL = "https://app.lagoon.finance/api"

#: How long Lagoon metadata is cached before the scanner refreshes it.
DEFAULT_CACHE_DURATION = datetime.timedelta(days=1)

#: Prevent one failed refresh from being retried for every vault in the same scan.
FAILED_REFRESH_RETRY_DELAY = datetime.timedelta(hours=1)

#: Hidden Lagoon deployments reviewed for inclusion in the public vault
#: catalogue. Lagoon's detail API recognises these addresses, but its paginated
#: listing endpoint omits them because ``isVisible`` is false. Chain IDs are
#: part of the key so an address reused on another chain is never implicitly
#: included.
LAGOON_PRIVATE_VAULT_ALLOWLIST: frozenset[tuple[int, HexAddress]] = (
    frozenset(
        {
            #: Der base USDC, about $5.1m TVL when reviewed on 2026-09-28.
            (1, HexAddress("0xba6cfe8a9d199cd7f3e50114c4e4ec66f2d52c87")),
            #: TowerBridge, about $4.6m TVL when reviewed on 2026-09-28.
            (1, HexAddress("0x22f99228f3ba7cfc7189ddf14366970fe0cef0cb")),
            #: Der USDC, about $2.2m TVL when reviewed on 2026-09-28.
            (1, HexAddress("0xf10801bcc3deaf467fb8b3dbb7430111822e6dab")),
            #: Muchacho USDC, about $1.1m TVL when reviewed on 2026-09-28.
            (1, HexAddress("0xef39d77c7fb6224ac974c5fa4e3151a6c6ce9594")),
            #: UEB3, about $1.1m TVL when reviewed on 2026-09-28.
            (1, HexAddress("0x23b27310451f2754de34d9c04aa24e8be367124a")),
            #: Odyssey Stablecoins Discretionary, about $1.0m TVL when reviewed on 2026-09-28.
            (1, HexAddress("0xfd104766499a3ff60ea85b5c6015ba9e32b8c891")),
            #: Angmar Capital, about $2.7m TVL when reviewed on 2026-09-28.
            (42161, HexAddress("0x1723cb57af58efb35a013870c90fcc3d60174a4e")),
            #: Dynamic Alpha Fundamental 2X, about $2.2m TVL when reviewed on 2026-09-28.
            (42161, HexAddress("0xc047d64dafe9e6ac76508835c17c6719f9278c1c")),
        }
    )
    | KAMUI_LAGOON_VAULTS
)

#: Explanation persisted for private Lagoon deployments that have no public
#: strategy description.
PRIVATE_LAGOON_VAULT_NOTE = "Private Lagoon vault reviewed and included through an address-specific allowlist; public strategy metadata is not available."

logger = logging.getLogger(__name__)


def is_lagoon_private_vault_allowlisted(chain_id: int, vault_address: HexAddress | str) -> bool:
    """Check whether a hidden Lagoon deployment is approved for listing.

    Lagoon's public catalogue omits private deployments even when its
    exact-address detail endpoint recognises them. This explicit chain-aware
    allowlist permits reviewed, economically material deployments without
    weakening the default treatment of every other hidden contract.

    :param chain_id:
        EVM chain ID of the Lagoon deployment.

    :param vault_address:
        Vault contract address in any checksum casing.

    :return:
        ``True`` only for an explicitly reviewed chain-address pair.
    """

    return (chain_id, HexAddress(vault_address.lower())) in LAGOON_PRIVATE_VAULT_ALLOWLIST


def _get_cache_file(cache_path: Path, chain_id: int) -> Path:
    """Resolve one chain's Lagoon cache file.

    :param cache_path:
        Directory containing Lagoon metadata cache files.

    :param chain_id:
        EVM chain id.

    :return:
        Absolute path to the chain's JSON cache file.
    """
    return (cache_path / f"lagoon_metadata_chain_{chain_id}_v2.json").resolve()


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
    """Normalise one curator object from the Lagoon detail API.

    Nullable names become empty strings so display accessors can omit them.
    Source: https://app.lagoon.finance/api/vault.

    :param raw: One curator object from a validated detail response.
    :return: Curator fields using the internal metadata names.
    """
    return LagoonCuratorMetadata(
        id=raw.get("id", ""),
        name=raw.get("name") or "",
        url=raw.get("url"),
        logo_url=raw.get("logoUrl"),
        about_description=raw.get("aboutDescription"),
    )


def _parse_vault_detail(raw: dict) -> LagoonVaultMetadata:
    """Parse vault metadata from the detail API response.

    Preserve provider descriptions for normalisation by the adapter. Missing
    curator collections are represented by an empty list.
    Source: https://app.lagoon.finance/api/vault.

    :param raw: Validated JSON object from the detail endpoint.
    :return: Internal vault metadata, without changing listing eligibility.
    """
    return LagoonVaultMetadata(
        name=raw.get("name") or "",
        description=raw.get("description"),
        short_description=raw.get("shortDescription"),
        logo_url=raw.get("logoUrl"),
        transparency_url=raw.get("transparencyUrl"),
        average_settlement=raw.get("averageSettlement"),
        curators=[_parse_curator(curator) for curator in (raw.get("curators") or [])],
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


@dataclass(slots=True)
class LagoonDetailResponse:
    """One validated exact-address response from Lagoon's public API.

    Request failures remain distinct from a successful response with empty
    descriptions. See https://app.lagoon.finance/api/vault.
    """

    #: Provider data, populated only after chain/address validation.
    raw: dict | None = None
    #: One of success, not_found or unavailable.
    outcome: Literal["success", "not_found", "unavailable"] = "unavailable"
    #: Optional bounded provider retry delay in seconds.
    retry_after: float = 3600
    #: A provider 429 defers all other detail requests on this chain.
    rate_limited: bool = False


@dataclass(slots=True)
class LagoonMetadataSnapshot:
    """Consistent detail and catalogue observations for one scan row.

    Metadata availability does not confer approval to list a deployment.
    """

    #: Last successful parsed detail, including historical/closed products.
    metadata: LagoonVaultMetadata | None
    #: Public catalogue membership, or None before any successful observation.
    listed: bool | None
    #: Last detail attempt outcome, independent of retained payload presence.
    outcome: str
    #: Naive UTC timestamp of the last successful detail observation.
    fetched_at: str | None
    #: Earliest next exact-address attempt, including provider throttling.
    retry_at: str | None = None


def _retry_after_seconds(value: str | None) -> float:
    """Bound a Retry-After header without sleeping in a scanner worker.

    Both HTTP date and delta-seconds forms are accepted. Invalid values use
    the normal one-hour retry delay.

    :param value: Optional HTTP Retry-After header.
    :return: Delay between one second and one day.
    """
    try:
        delay = float(value) if value and value.isdecimal() else (parsedate_to_datetime(value).astimezone(datetime.UTC) - datetime.datetime.now(datetime.UTC)).total_seconds()
    except (TypeError, ValueError, OverflowError):
        delay = FAILED_REFRESH_RETRY_DELAY.total_seconds()
    return max(1, min(delay, 86400))


def _fetch_detail_response(chain_id: int, address: HexAddress | str, api_base_url: str = DEFAULT_API_BASE_URL) -> LagoonDetailResponse:
    """Fetch exact-address data, keeping absence separate from provider failure.

    Only a response identifying the requested deployment can supply metadata.
    The endpoint is https://app.lagoon.finance/api/vault.

    :param chain_id: Requested chain identifier.
    :param address: Requested contract address.
    :param api_base_url: Public API base, overridable for tests.
    :return: Validated raw payload or a retryable/negative result.
    """
    try:
        response = requests.get(f"{api_base_url}/vault", params={"chainId": chain_id, "address": address}, timeout=30)
        if response.status_code == 404:
            return LagoonDetailResponse(outcome="not_found", retry_after=86400)
        if response.status_code == 429:
            delay = _retry_after_seconds(response.headers.get("Retry-After"))
            logger.warning("Lagoon detail rate limited for %s on chain %s; retry after %s seconds", address, chain_id, delay)
            return LagoonDetailResponse(retry_after=delay, rate_limited=True)
        response.raise_for_status()
        raw = response.json()
        if not isinstance(raw, dict) or not isinstance(raw.get("chain"), dict) or int(raw["chain"]["id"]) != chain_id or raw.get("address", "").lower() != address.lower():
            raise ValueError("Detail response does not identify the requested deployment")
        if any(raw.get(field) is not None and not isinstance(raw[field], str) for field in ("name", "description", "shortDescription")):
            raise ValueError("Invalid text fields in detail response")
        curators = raw.get("curators")
        if curators is None:
            curators = []
        if not isinstance(curators, list) or any(not isinstance(c, dict) or (c.get("name") is not None and not isinstance(c["name"], str)) for c in curators):
            raise ValueError("Invalid curator fields in detail response")
        if raw.get("state") is not None and not isinstance(raw["state"], dict):
            raise ValueError("Invalid state field in detail response")
        return LagoonDetailResponse(raw=raw, outcome="success")
    except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError) as error:
        logger.warning("Lagoon detail deferred for %s on chain %s: %s", address, chain_id, error)
        return LagoonDetailResponse()


def _fetch_vault_detail(chain_id: int, address: HexAddress, api_base_url: str = DEFAULT_API_BASE_URL) -> dict | None:
    """Fetch validated raw metadata for backwards-compatible API consumers.

    The cache uses the richer response to preserve absence/failure provenance.

    :param chain_id: Requested chain.
    :param address: Requested contract.
    :param api_base_url: Public metadata API base URL.
    :return: Raw successful detail, or None when unavailable.
    """
    return _fetch_detail_response(chain_id, address, api_base_url).raw


#: Versioned chain envelopes, keyed by resolved path and atomic file identity.
_cached_vaults: dict[Path, tuple[tuple[int, int, int], dict]] = {}
#: Thread locks coalesce each catalogue or exact-address request in a process.
_request_locks: dict[tuple[Path, str], threading.RLock] = {}
_locks_guard = threading.Lock()


def _request_lock(file: Path, key: str) -> threading.RLock:
    """Resolve a process-local request lock.

    Disk commits have a separate, short cross-process writer lock.

    :param file: Chain cache path.
    :param key: Catalogue marker or normalised address.
    :return: Shared reentrant lock.
    """
    with _locks_guard:
        return _request_locks.setdefault((file, key), threading.RLock())


def _read_cache(file: Path, chain_id: int) -> dict:
    """Read a versioned envelope or losslessly interpret the legacy map.

    Legacy map keys seed the previous eligibility observation only; new detail
    payloads never confer membership. Corruption must not erase cached text.

    :param file: Cache path.
    :param chain_id: Chain used to interpret the reviewed private allowlist.
    :return: Shared read-only envelope. Writers must copy inside the lock.
    :raises RuntimeError: For invalid or unsupported cache content.
    """
    source = file
    if not source.exists():
        legacy = file.parent / f"lagoon_vaults_chain_{chain_id}.json"
        if legacy.exists() and legacy.stat().st_size > 0:
            source = legacy
        else:
            return {"schema_version": 2, "listed": None, "listing_fetched_at": None, "listing_attempt_at": None, "listing_retry_at": None, "details": {}}
    stat = source.stat()
    fingerprint = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
    cached = _cached_vaults.get(file)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        if "schema_version" not in data:
            if any(not Web3.is_address(address) or not isinstance(metadata, dict) for address, metadata in data.items()):
                raise ValueError("Invalid legacy address map")
            if len({a.lower() for a in data}) != len(data):
                raise ValueError("Duplicate legacy address identities")
            observed_at = native_datetime_utc_fromtimestamp(stat.st_mtime)
            fetched_at = observed_at.isoformat()
            retry_at = (observed_at + DEFAULT_CACHE_DURATION).isoformat()
            listed = [address.lower() for address in data if not is_lagoon_private_vault_allowlisted(chain_id, address)]
            data = {"schema_version": 2, "listed": listed, "listing_fetched_at": fetched_at, "listing_attempt_at": fetched_at, "listing_retry_at": retry_at, "legacy": True, "details": {address.lower(): {"metadata": metadata, "fetched_at": fetched_at, "attempt_at": fetched_at, "retry_at": retry_at, "outcome": "success"} for address, metadata in data.items()}}
        if data["schema_version"] != 2 or not isinstance(data["details"], dict) or (data["listed"] is not None and (not isinstance(data["listed"], list) or any(not Web3.is_address(a) for a in data["listed"]))):
            raise ValueError("Unsupported Lagoon cache envelope")
        for field in ("listing_fetched_at", "listing_attempt_at", "listing_retry_at", "provider_retry_at"):
            if data.get(field) is not None:
                if datetime.datetime.fromisoformat(data[field]).tzinfo is not None:
                    raise ValueError("Cache times must be naive UTC")
        for address, record in data["details"].items():
            if not Web3.is_address(address) or address != address.lower():
                raise ValueError("Invalid cached detail address")
            if not isinstance(record, dict) or record.get("outcome") not in {"success", "not_found", "unavailable"} or (record.get("metadata") is not None and not isinstance(record["metadata"], dict)):
                raise ValueError("Invalid detail record")
            for field in ("attempt_at", "retry_at"):
                if not isinstance(record.get(field), str):
                    raise ValueError("Detail record is missing its request clock")
            for field in ("fetched_at", "attempt_at", "retry_at"):
                if record.get(field):
                    if datetime.datetime.fromisoformat(record[field]).tzinfo is not None:
                        raise ValueError("Cache times must be naive UTC")
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as error:
        raise RuntimeError(f"Could not parse Lagoon vaults file for chain {chain_id} at {file}: {error}") from error
    _cached_vaults[file] = (fingerprint, data)
    return data


def _load_cached_vaults(file: Path, chain_id: int) -> dict[HexAddress, LagoonVaultMetadata]:
    """Read detail payloads without making network requests.

    Kept for existing consumers that need only the address map.

    :param file: Chain cache JSON file.
    :param chain_id: Chain identifier.
    :return: Checksummed address-to-payload map, including historical records.
    """
    return {Web3.to_checksum_address(address): copy.deepcopy(record["metadata"]) for address, record in _read_cache(file, chain_id)["details"].items() if record.get("metadata") is not None}


def _commit_cache(file: Path, chain_id: int, update: Callable[[dict], None]) -> None:
    """Merge into the latest disk state under a short cross-process lock.

    HTTP requests must complete before entering this function.

    :param file: Chain cache file.
    :param chain_id: Chain identifier.
    :param update: Mutation applied to the latest detached envelope.
    :return: ``None`` after atomically publishing the merged envelope.
    """
    with wait_other_writers(file):
        data = copy.deepcopy(_read_cache(file, chain_id))
        update(data)
        with atomic_write(str(file), mode="w", overwrite=True, encoding="utf-8") as output:
            json.dump(data, output)


def _is_due(retry_at: str | None, now_: datetime.datetime) -> bool:
    """Compare an independent request deadline with naive UTC time.

    :param retry_at: Serialised deadline, or None for a first attempt.
    :param now_: Current naive UTC time.
    :return: Whether another request may be attempted.
    """
    return retry_at is None or now_ >= datetime.datetime.fromisoformat(retry_at)


def _refresh_catalogue(chain_id: int, file: Path, now_: datetime.datetime, api_base_url: str, force: bool = False) -> None:
    """Refresh membership independently from exact-address payloads.

    Failed/incomplete listings preserve previous eligibility and defer retry.
    See https://app.lagoon.finance/api/vaults for the paginated source.

    :param chain_id: Chain whose rows are selected from the global listing.
    :param file: Chain cache path.
    :param now_: Current naive UTC time.
    :param api_base_url: Metadata API base URL.
    :param force: Refresh successful observations early; failed catalogue
        attempts retain their retry deadlines.
    :return: ``None`` after refreshing or deferring this request.
    """
    with _request_lock(file, "catalogue"):
        previous = _read_cache(file, chain_id)
        if not _is_due(previous["listing_retry_at"], now_):
            failed_attempt = previous["listing_attempt_at"] != previous["listing_fetched_at"]
            if not force or failed_attempt:
                return
        addresses = []
        global_count = 0
        page = 0
        success = True
        while True:
            raw = _fetch_vault_listing_page(chain_id, page_index=page, api_base_url=api_base_url)
            try:
                if not isinstance(raw, dict) or not isinstance(raw["vaults"], list) or not isinstance(raw["hasNextPage"], bool):
                    raise ValueError("Invalid listing shape")
                global_count += len(raw["vaults"])
                for vault in raw["vaults"]:
                    if not Web3.is_address(vault["address"]):
                        raise ValueError("Invalid listed address")
                    if int(vault["chain"]["id"]) == chain_id:
                        addresses.append(vault["address"].lower())
                if not raw["hasNextPage"]:
                    break
                page += 1
                if page >= 1000:
                    raise ValueError("Listing pagination did not terminate")
            except (TypeError, ValueError, KeyError) as error:
                logger.warning("Lagoon catalogue deferred for chain %s: %s", chain_id, error)
                success = False
                break
        if success and global_count == 0 and previous["listed"]:
            logger.warning("Lagoon unexpectedly empty catalogue for chain %s; retaining previous membership", chain_id)
            success = False

        def merge(data: dict) -> None:
            """Apply only if another process has not published a newer attempt."""
            if data.get("listing_attempt_at") and data["listing_attempt_at"] > now_.isoformat():
                return
            data["listing_attempt_at"] = now_.isoformat()
            data["listing_retry_at"] = (now_ + (DEFAULT_CACHE_DURATION if success else FAILED_REFRESH_RETRY_DELAY)).isoformat()
            if success:
                data["listed"] = sorted(set(addresses))
                data["listing_fetched_at"] = now_.isoformat()
                data.pop("legacy", None)

        _commit_cache(file, chain_id, merge)


def _refresh_detail(chain_id: int, address: str, file: Path, now_: datetime.datetime, api_base_url: str, force: bool = False) -> None:
    """Refresh a requested known deployment even when catalogue access fails.

    Success, absence and retryable failure have independent per-address clocks.
    A failed request never deletes the last successful payload.

    :param chain_id: Requested chain.
    :param address: Lowercase requested address.
    :param file: Chain cache path.
    :param now_: Current naive UTC time.
    :param api_base_url: Provider endpoint.
    :param force: Refresh successful observations early; failure and provider
        backoff deadlines still apply.
    :return: ``None`` after refreshing or deferring this request.
    """
    with _request_lock(file, address):
        data = _read_cache(file, chain_id)
        previous = data["details"].get(address, {})
        if not _is_due(previous.get("retry_at"), now_):
            if not force or previous.get("outcome") != "success":
                return
        if not _is_due(data.get("provider_retry_at"), now_):
            return
        response = _fetch_detail_response(chain_id, address, api_base_url)
        delay = datetime.timedelta(seconds=response.retry_after)
        if response.outcome == "success":
            raw = response.raw
            empty = not any((raw.get(field) or "").strip() for field in ("description", "shortDescription"))
            delay = datetime.timedelta(days=7) if empty or (raw.get("state") or {}).get("state") == "Closed" else DEFAULT_CACHE_DURATION

        def merge(data: dict) -> None:
            """Preserve newer results and merge a single exact deployment."""
            if response.rate_limited:
                data["provider_retry_at"] = max(data.get("provider_retry_at") or "", (now_ + delay).isoformat())
            records = data["details"]
            record = records.get(address, {})
            if record.get("attempt_at") and record["attempt_at"] > now_.isoformat():
                return
            record.update(outcome=response.outcome, attempt_at=now_.isoformat(), retry_at=(now_ + delay).isoformat())
            if response.outcome == "success":
                record.update(metadata=_parse_vault_detail(response.raw), fetched_at=now_.isoformat())
            records[address] = record
            cutoff = (now_ - datetime.timedelta(days=30)).isoformat()
            data["details"] = {a: r for a, r in records.items() if r.get("metadata") is not None or r["attempt_at"] >= cutoff}

        _commit_cache(file, chain_id, merge)


def fetch_lagoon_metadata_snapshot(chain_id: int, vault_address: HexAddress | str, now_: datetime.datetime | None = None, cache_path: Path = DEFAULT_CACHE_PATH, api_base_url: str = DEFAULT_API_BASE_URL, refresh_listing: bool = True) -> LagoonMetadataSnapshot:
    """Fetch a coherent metadata and membership observation for one known vault.

    Exact-address detail is independent of catalogue visibility. This does not
    discover vaults or approve a private deployment for public export.
    Source: https://app.lagoon.finance/api/vault.

    :param chain_id: Verified chain identifier.
    :param vault_address: Known deployment, in any address casing.
    :param now_: Override current naive UTC time for deterministic tests.
    :param cache_path: Cache root; migrations must use temporary storage.
    :param api_base_url: Public API URL, overridable for tests.
    :param refresh_listing: False for an exact-address-only migration.
    :return: Parsed payload, source outcome and independent listing observation.
    """
    assert Web3.is_address(vault_address), f"Invalid Lagoon address: {vault_address}"
    now_ = now_ or native_datetime_utc_now()
    cache_path.mkdir(parents=True, exist_ok=True)
    file = _get_cache_file(cache_path, chain_id)
    if refresh_listing:
        _refresh_catalogue(chain_id, file, now_, api_base_url)
    address = vault_address.lower()
    _refresh_detail(chain_id, address, file, now_, api_base_url)
    data = _read_cache(file, chain_id)
    record = data["details"].get(address, {})
    outcome = record.get("outcome", "unavailable")
    retry_at = record.get("retry_at")
    if _is_due(retry_at, now_) and not _is_due(data.get("provider_retry_at"), now_):
        outcome, retry_at = "unavailable", data["provider_retry_at"]
    return LagoonMetadataSnapshot(copy.deepcopy(record.get("metadata")), address in data["listed"] if data["listed"] is not None else None, outcome, record.get("fetched_at"), retry_at)


def fetch_lagoon_vaults_for_chain(chain_id: int, cache_path: Path = DEFAULT_CACHE_PATH, api_base_url: str = DEFAULT_API_BASE_URL, now_: datetime.datetime | None = None, max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION) -> dict[HexAddress, LagoonVaultMetadata]:
    """Refresh the public catalogue and reviewed private details, retaining history.

    Historical unlisted payloads are returned but refreshed only on demand.
    See https://app.lagoon.finance/api/vaults for the source inventory.

    :param chain_id: Verified chain identifier.
    :param cache_path: Root containing per-chain cache envelopes.
    :param api_base_url: Provider API base.
    :param now_: Optional naive UTC clock for tests.
    :param max_cache_duration: Override successful bulk freshness; zero forces
        successful observations, but never bypasses failure/rate-limit deadlines.
    :return: Checksummed address-to-payload map, including retained history.
    """
    now_ = now_ or native_datetime_utc_now()
    cache_path.mkdir(parents=True, exist_ok=True)
    file = _get_cache_file(cache_path, chain_id)
    force = max_cache_duration <= datetime.timedelta(0)
    if max_cache_duration != DEFAULT_CACHE_DURATION and not force:
        existing = _read_cache(file, chain_id)
        observed = existing["listing_fetched_at"]
        if observed is not None and now_ < datetime.datetime.fromisoformat(observed) + max_cache_duration:
            return _load_cached_vaults(file, chain_id)
        force = True
    _refresh_catalogue(chain_id, file, now_, api_base_url, force)
    data = _read_cache(file, chain_id)
    targets = set(data["listed"] or ()) | {a.lower() for c, a in LAGOON_PRIVATE_VAULT_ALLOWLIST if c == chain_id}
    for address in sorted(targets):
        logger.info("Refreshing Lagoon detail %s on chain %s", address, chain_id)
        _refresh_detail(chain_id, address, file, now_, api_base_url, force)
    return _load_cached_vaults(file, chain_id)


def fetch_lagoon_vault_metadata(web3: Web3, vault_address: HexAddress, now_: datetime.datetime | None = None, cache_path: Path = DEFAULT_CACHE_PATH) -> LagoonVaultMetadata | None:
    """Fetch parsed metadata for a known address without implying public approval.

    Uses per-address expiry even for long-lived scanner processes.

    :param web3: Connection providing the verified chain identifier.
    :param vault_address: Known vault contract.
    :param now_: Optional naive UTC clock.
    :param cache_path: Root for shared cache envelopes.
    :return: Last successful payload, or None if none has ever been fetched.
    """
    return fetch_lagoon_metadata_snapshot(web3.eth.chain_id, vault_address, now_, cache_path).metadata
