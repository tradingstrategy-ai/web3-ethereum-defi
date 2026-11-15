"""T3tris vault offchain metadata.

- T3tris stores vault page descriptions in their backend, not onchain
- The detail endpoint ``/api/vaults/{chainId}/{vaultAddress}`` on the
  `T3tris app <https://earn.t3tris.finance/vaults>`__ returns the same page
  view model shown in the app; it answers 404 for vaults the app does not list
- We fetch and cache this data locally to avoid repeated API calls
- Two-level caching: disk (2-day TTL) + in-process dictionary
"""

import datetime
import email.utils
import json
import logging
import time
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

#: T3tris API base URL, reverse-engineered from their Next.js frontend.
#: ``https://api.t3tris.finance/api/v1`` answered 404 for every path from
#: October 2026; the app now serves the same routes itself.
DEFAULT_API_BASE_URL = "https://earn.t3tris.finance/api"

#: Private key under which :py:func:`_fetch_vault_detail` stores the response
#: time, as a Unix timestamp, so the parser can turn ``estimatedRedemptionTs``
#: into a duration.
RESPONSE_TIMESTAMP_KEY = "_response_ts"

#: Shortest average settlement interval accepted as an estimate, in seconds.
#:
#: A heuristic floor. In November 2026 the Kingfisher vault, whose two
#: settlements were in the same block, reported an interval of about 20
#: seconds. T3tris does not publish its averaging method, so a sub-hour value
#: is treated as an artefact rather than a real settlement cadence.
MIN_AVERAGE_SETTLEMENT_SECONDS = 3600

logger = logging.getLogger(__name__)


class T3trisVaultMetadata(TypedDict):
    """Metadata about a T3tris vault from offchain source.

    Fetched from the T3tris REST API endpoint:
    ``GET /api/vaults/{chainId}/{vaultAddress}``.
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

    #: T3tris' average settlement interval in seconds, or ``None``.
    #:
    #: Derived from the detail response's ``estimatedRedemptionTs``, which the
    #: app labels "one average settlement interval from today; not a scheduled
    #: or guaranteed date". T3tris computes it server-side and does not publish
    #: the method. It ignores how long ago the last settlement was: a vault
    #: last settled months ago can report a sub-day interval, and two
    #: settlements in one block give zero. Treat it as a weak fallback;
    #: recorded settlement events are the better timing source. ``None`` when
    #: the field is absent, the interval is shorter than
    #: :py:data:`MIN_AVERAGE_SETTLEMENT_SECONDS`, or the data came from the
    #: vault-list fallback. Cache files written before this field existed
    #: do not contain the key.
    average_settlement_seconds: int | None


def _normalise_optional_string(value: object) -> str | None:
    """Normalise blank API string fields to ``None``."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _parse_average_settlement_seconds(raw: dict) -> int | None:
    """Turn the detail response's ``estimatedRedemptionTs`` into an interval.

    T3tris returns the estimate as an absolute time: the response time plus
    its average settlement interval. Store the interval rather than the time,
    so a cached value does not shrink as the cache ages.

    :param raw:
        Raw detail response, with :py:data:`RESPONSE_TIMESTAMP_KEY` set by
        :py:func:`_fetch_vault_detail`.
    :return:
        Interval in seconds, or ``None`` when unknown or shorter than
        :py:data:`MIN_AVERAGE_SETTLEMENT_SECONDS`.
    """
    estimated = raw.get("estimatedRedemptionTs")
    response_ts = raw.get(RESPONSE_TIMESTAMP_KEY)
    if isinstance(estimated, bool) or not isinstance(estimated, int | float) or not isinstance(response_ts, int):
        return None
    interval = int(estimated) - response_ts
    return interval if interval >= MIN_AVERAGE_SETTLEMENT_SECONDS else None


def _parse_vault_detail(raw: dict) -> T3trisVaultMetadata:
    """Parse vault metadata from the T3tris page endpoint.

    :param raw:
        Raw JSON dict from ``/api/vaults/{chainId}/{vaultAddress}``
        or a single vault row from ``/api/vaults``.
    :return:
        Parsed metadata. Fields the current API no longer returns
        (``category``, ``attributes``, ``rating``) parse as empty.
    """
    vault = raw.get("vault") or raw
    name = _normalise_optional_string(vault.get("displayName")) or _normalise_optional_string(vault.get("name")) or _normalise_optional_string(vault.get("onchainName")) or ""
    symbol = _normalise_optional_string(vault.get("displaySymbol")) or _normalise_optional_string(vault.get("symbol")) or _normalise_optional_string(vault.get("onchainSymbol"))
    attributes = vault.get("attributes")
    if not isinstance(attributes, list):
        attributes = []
    deposits_disabled = vault.get("depositsDisabled") if isinstance(vault.get("depositsDisabled"), bool) else None
    flags = vault.get("flags")
    if deposits_disabled is None and isinstance(flags, dict) and isinstance(flags.get("depositEnabled"), bool):
        deposits_disabled = not flags["depositEnabled"]

    return T3trisVaultMetadata(
        name=name,
        symbol=symbol,
        description=_normalise_optional_string(vault.get("description")),
        curator_name=_normalise_optional_string(vault.get("curatorName")),
        curator_url=_normalise_optional_string(vault.get("curatorUrl")),
        verified=vault.get("verified") if isinstance(vault.get("verified"), bool) else None,
        deposits_disabled=deposits_disabled,
        category=_normalise_optional_string(vault.get("category")),
        attributes=[str(item) for item in attributes],
        rating=_normalise_optional_string(vault.get("rating")),
        visibility=_normalise_optional_string(vault.get("visibility")),
        ipfs_hash=_normalise_optional_string(vault.get("ipfsHash")),
        average_settlement_seconds=_parse_average_settlement_seconds(raw),
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

    vaults = (payload.get("items") or payload.get("vaults")) if isinstance(payload, dict) else None
    if not isinstance(vaults, list):
        logger.warning("Unexpected T3tris vault list response shape: %s", type(payload))
        return None

    for item in vaults:
        if not isinstance(item, dict):
            continue
        if item.get("chainId") == chain_id and str(item.get("address", "")).lower() == address_lower:
            return item

    return None


def _fetch_response_timestamp(resp: requests.Response) -> int:
    """Read the server's response time, falling back to the local clock.

    :param resp:
        Successful HTTP response.
    :return:
        Unix timestamp in seconds.
    """
    header = resp.headers.get("Date")
    if header:
        try:
            return int(email.utils.parsedate_to_datetime(header).timestamp())
        except (TypeError, ValueError):
            logger.warning("Unparseable T3tris response Date header %r; using local time", header)
    return int(time.time())


def _fetch_vault_detail(
    chain_id: int,
    address: str,
    api_base_url: str = DEFAULT_API_BASE_URL,
    strict: bool = False,
) -> dict | None:
    """Fetch detailed vault metadata from the T3tris web app API.

    :param chain_id:
        EVM chain id.

    :param address:
        Vault contract address.

    :param api_base_url:
        T3tris API base URL.

    :param strict:
        Raise transient failures instead of falling back to the vault list,
        which carries no settlement estimate. A 404 still returns ``None``.
        Migrations use this so that a failed fetch is retried rather than
        recorded as a vault without an estimate.

    :return:
        Raw JSON response dict, or ``None`` if T3tris does not list the vault
        or both endpoints fail. A detail response also carries the response
        time under :py:data:`RESPONSE_TIMESTAMP_KEY`, from the HTTP ``Date``
        header or the local clock.
    """
    url = f"{api_base_url}/vaults/{chain_id}/{address}"
    logger.debug("Fetching T3tris vault detail from %s", url)
    try:
        resp = requests.get(url, timeout=30, headers={"Accept": "application/json"})
        resp.raise_for_status()
        raw = resp.json()
        if isinstance(raw, dict):
            raw[RESPONSE_TIMESTAMP_KEY] = _fetch_response_timestamp(resp)
        return raw
    except requests.HTTPError as e:
        if getattr(e.response, "status_code", None) == 404:
            # Vaults deployed on the protocol but not listed in the app
            logger.info("T3tris app does not list vault %s on chain %d", address, chain_id)
            return None
        if strict:
            raise
        logger.warning("Failed to fetch T3tris vault detail for %s on chain %d: %s; falling back to vault list", address, chain_id, e)
        return _fetch_vaults_list_entry(chain_id, address, api_base_url=api_base_url)
    except (requests.RequestException, JSONDecodeError) as e:
        if strict:
            raise
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
