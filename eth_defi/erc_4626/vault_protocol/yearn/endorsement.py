"""Yearn primary-list exclusions used for protocol attribution.

`Yearn's public vault registry <https://kong.yearn.fi/api/rest/list/vaults>`__
is broader than Trading Strategy's Yearn-operated vault catalogue. We classify
a vault as not Yearn-operated when the registry explicitly sets
``inclusion.isSet`` without ``inclusion.isYearn``, or when it supplies an empty
``inclusion`` object. Empty inclusion objects include the Katana Stablecoin
Transformer depositors ``0x63a028963907f5a0c1ceb7e47100f52dfc611117`` and
``0xbc64210d565aabca8eb6eb795833cc505ac3647f``.

This deliberately removes uncurated strategy targets and wrappers from the
Yearn protocol and curated-vault lists. The generic Yearn web-page template may
still render such a record as a Yearn vault; that presentation does not change
our attribution policy. The marker is not an assertion about contract safety,
code provenance, or who technically deployed a contract.

The scanner preserves technical Yearn interface features for the appropriate
vault adapter, but records a provenance marker so protocol and curator views do
not attribute excluded contracts to Yearn. The live registry is cached per
scanner worker for one day. A source outage is unknown, not exclusion evidence.
"""

import datetime
import logging
from dataclasses import dataclass
from threading import Lock

import requests
from eth_typing import HexAddress
from requests.exceptions import RequestException

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import YEARN_TECHNICAL_FEATURES, ERC4626Feature

logger = logging.getLogger(__name__)

#: Live Yearn frontend registry that provides vault-list membership decisions.
YEARN_VAULT_REGISTRY_URL = "https://kong.yearn.fi/api/rest/list/vaults"

#: Refresh primary-list decisions daily in persistent scanner workers.
YEARN_REGISTRY_CACHE_DURATION = datetime.timedelta(days=1)

#: Avoid retrying a temporarily unavailable registry for every scanned vault.
YEARN_REGISTRY_UNAVAILABLE_RETRY_DELAY = datetime.timedelta(minutes=5)

#: Bound one registry request so an unavailable remote source cannot stall a scan.
YEARN_REGISTRY_REQUEST_TIMEOUT = 30

#: Reject a valid-looking but truncated registry response. The reviewed
#: exclusion population is substantially larger; accepting a tiny index would
#: reattribute migrated vaults to Yearn until the next refresh.
MINIMUM_YEARN_REGISTRY_EXCLUSION_COUNT = 100

#: Chain/address keys for vaults excluded from Yearn's primary product list.
YearnRegistryExclusions = frozenset[tuple[int, HexAddress]]


@dataclass(slots=True, frozen=True)
class CachedYearnRegistryExclusions:
    """One worker's cached Yearn registry-exclusion index."""

    #: Registry entries excluded from Yearn's primary product list.
    exclusions: YearnRegistryExclusions

    #: Naive UTC time when the registry was successfully fetched.
    fetched_at: datetime.datetime


@dataclass(slots=True)
class YearnRegistryExclusionCache:
    """Hold mutable per-process state for primary-list exclusions."""

    #: Most recently successful registry index.
    index: CachedYearnRegistryExclusions | None = None

    #: Naive UTC time before a failed request may be retried.
    retry_after: datetime.datetime | None = None


#: Shared cache used by all Yearn classification calls in one scanner worker.
_yearn_registry_exclusions_state = YearnRegistryExclusionCache()

#: Serialise the cold-cache registry fetch across threaded scanner workers.
_yearn_registry_exclusions_lock = Lock()


def _is_yearn_registry_exclusion(inclusion: object) -> bool:
    """Check whether one registry inclusion value excludes a vault.

    An empty object is an explicit inclusion value and has different semantics
    from a missing or malformed value. Trading Strategy deliberately treats it
    as not Yearn-operated, alongside Yearn's established
    ``isSet``/``isYearn`` predicate, to remove uncurated strategy noise from
    Yearn protocol and curated-vault listings.

    :param inclusion:
        Raw ``inclusion`` value from a Yearn registry record.
    :return:
        ``True`` when the record excludes the vault from Yearn's primary list.
    """

    if not isinstance(inclusion, dict):
        return False
    return inclusion == {} or (inclusion.get("isSet") is True and inclusion.get("isYearn") is not True)


def parse_yearn_registry_exclusions(payload: object) -> YearnRegistryExclusions:
    """Parse primary-list exclusions from Yearn's public registry response.

    The API is an array of heterogeneous records. Invalid records are omitted,
    because a malformed source entry cannot safely determine product
    attribution. Valid entries use ``chainId`` and EIP-55 or lowercase
    addresses, which are normalised into compact lookup keys.

    :param payload:
        Decoded JSON body returned by Yearn's registry API.
    :return:
        Immutable chain/address keys for excluded vaults.
    """

    if not isinstance(payload, list):
        message = "Yearn vault registry response must be a JSON array"
        raise ValueError(message)

    exclusions: set[tuple[int, HexAddress]] = set()
    for record in payload:
        if not isinstance(record, dict) or not _is_yearn_registry_exclusion(record.get("inclusion")):
            continue
        chain_id = record.get("chainId")
        vault_address = record.get("address")
        if not isinstance(chain_id, int) or not isinstance(vault_address, str):
            continue
        exclusions.add((chain_id, HexAddress(vault_address.lower())))
    return frozenset(exclusions)


def fetch_yearn_registry_exclusions() -> YearnRegistryExclusions | None:
    """Fetch Yearn's primary-list exclusions once per scanner worker.

    A successful index remains usable during a temporary source failure. When
    no successful index exists, the caller receives ``None`` and keeps the
    normal technical protocol classification rather than making a negative
    attribution decision from unavailable offchain data. A response below
    :py:data:`MINIMUM_YEARN_REGISTRY_EXCLUSION_COUNT` is treated as unavailable
    so a truncated source cannot reverse existing attribution for one day.

    :return:
        Excluded chain/address keys, or ``None`` when Yearn's registry is unavailable.
    """

    with _yearn_registry_exclusions_lock:
        now_ = native_datetime_utc_now()
        cached_index = _yearn_registry_exclusions_state.index
        if cached_index is not None and now_ - cached_index.fetched_at <= YEARN_REGISTRY_CACHE_DURATION:
            return cached_index.exclusions

        retry_after = _yearn_registry_exclusions_state.retry_after
        if retry_after is not None and now_ < retry_after:
            return cached_index.exclusions if cached_index is not None else None

        try:
            logger.info("Fetching Yearn primary-list registry from %s", YEARN_VAULT_REGISTRY_URL)
            response = requests.get(YEARN_VAULT_REGISTRY_URL, timeout=YEARN_REGISTRY_REQUEST_TIMEOUT)
            response.raise_for_status()
            exclusions = parse_yearn_registry_exclusions(response.json())
            if len(exclusions) < MINIMUM_YEARN_REGISTRY_EXCLUSION_COUNT:
                message = f"Yearn registry returned only {len(exclusions)} exclusions"
                raise ValueError(message)
        except (RequestException, ValueError) as error:
            _yearn_registry_exclusions_state.retry_after = now_ + YEARN_REGISTRY_UNAVAILABLE_RETRY_DELAY
            if cached_index is not None:
                logger.warning("Could not refresh Yearn primary-list registry: %s; using the previous cache", error)
                return cached_index.exclusions
            logger.warning("Yearn primary-list registry is unavailable: %s", error)
            return None

        _yearn_registry_exclusions_state.index = CachedYearnRegistryExclusions(exclusions=exclusions, fetched_at=now_)
        _yearn_registry_exclusions_state.retry_after = None
        logger.info("Fetched %d Yearn primary-list exclusions", len(exclusions))
        return exclusions


def is_yearn_registry_excluded_vault(
    chain_id: int | None,
    vault_address: HexAddress,
    *,
    exclusions: YearnRegistryExclusions | None = None,
) -> bool:
    """Check whether Yearn excludes a vault from its primary product list.

    The check normalises EIP-55 casing. Supplying an index lets migrations use
    one reviewed registry snapshot for their complete run. Omitting it may
    trigger a cached HTTP lookup in scanner callers.

    :param chain_id:
        EVM chain identifier for the vault deployment, if known.
    :param vault_address:
        ERC-4626 vault contract address.
    :param exclusions:
        Optional pre-fetched Yearn primary-list exclusion index. Omitting it
        may trigger a cached HTTP lookup.
    :return:
        ``True`` when the registry records a primary-list exclusion.
    """

    if chain_id is None:
        return False
    if exclusions is None:
        exclusions = fetch_yearn_registry_exclusions()
    return exclusions is not None and (chain_id, HexAddress(vault_address.lower())) in exclusions


def add_yearn_registry_exclusion(
    chain_id: int | None,
    vault_address: HexAddress,
    features: set[ERC4626Feature],
    *,
    exclusions: YearnRegistryExclusions | None = None,
) -> set[ERC4626Feature]:
    """Add a Yearn primary-list exclusion marker to matching vault features.

    The marker changes protocol and curator attribution only. Technical Yearn
    interface features stay intact, allowing the scanner to select the correct
    Yearn adapter for deposit, fee, and permission handling.

    :param chain_id:
        EVM chain identifier for the vault deployment, if known.
    :param vault_address:
        ERC-4626 vault contract address.
    :param features:
        Features returned by the generic ABI probe.
    :param exclusions:
        Optional pre-fetched Yearn primary-list exclusion index.
    :return:
        Original features, with the exclusion marker added when applicable.
    """

    # The marker changes only Yearn attribution, so generic and non-Yearn vault
    # probes do not need a live registry request.
    if not features.intersection(YEARN_TECHNICAL_FEATURES):
        return features
    if not is_yearn_registry_excluded_vault(chain_id, vault_address, exclusions=exclusions):
        return features
    return features | {ERC4626Feature.yearn_registry_excluded}
