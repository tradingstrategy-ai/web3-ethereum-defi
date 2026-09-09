"""Yearn-maintained offchain vault metadata.

Yearn stores frontend endorsement and inclusion decisions in versioned
``data/meta/vaults/{chain_id}.json`` files in its
`yDaemon repository <https://github.com/yearn/ydaemon>`__.  A contract can
implement the Yearn V3 interface without being a Yearn product, so this
metadata supplements ABI-based protocol detection.

The complete file is fetched once per chain and cached locally.  It is neither
onchain contract data nor an offline bundled data set: callers treat a failed
fetch as unknown and do not exclude any vault on that basis.
"""

import datetime
import json
import logging
import re
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests
from eth_typing import HexAddress
from requests.exceptions import HTTPError, RequestException

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.utils import wait_other_writers

logger = logging.getLogger(__name__)

#: Persistent per-chain cache for the complete Yearn yDaemon metadata files.
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "yearn"

#: Raw GitHub directory containing Yearn's versioned yDaemon chain documents.
YEARN_YDAEMON_METADATA_BASE_URL = "https://raw.githubusercontent.com/yearn/ydaemon/main/data/meta/vaults"

#: Live catalogue that powers Yearn's public vault pages. Unlike the static
#: source repository, it contains recently launched vaults such as Flex USDC.
YEARN_DETECTED_VAULTS_URL = "https://ydaemon.yearn.fi/vaults/detected?limit=2000"

#: Maximum record count requested from the public detected-vault endpoint.
YEARN_DETECTED_VAULTS_LIMIT = 2000

#: Static catalogue metadata changes much less frequently than scanner cycles.
DEFAULT_CACHE_DURATION = datetime.timedelta(days=1)

#: Retry unavailable source metadata without making every vault trigger an HTTP request.
UNAVAILABLE_RETRY_DELAY = datetime.timedelta(minutes=5)

#: A listing summary must remain suitable for a compact vault-table cell.
MAX_SHORT_DESCRIPTION_LENGTH = 200


@dataclass(slots=True, frozen=True)
class YearnVaultMetadata:
    """The normalised yDaemon fields used to decide Yearn frontend membership.

    yDaemon's source document contains many free-form metadata fields.  The
    scanner keeps only the two explicit boolean decisions required to distinguish
    official Yearn frontend vaults from third-party V3-compatible contracts.

    :param endorsed:
        Whether Yearn explicitly endorses the vault.
    :param is_yearn:
        Whether Yearn explicitly includes the vault in its frontend.
    """

    #: ``True`` only when Yearn explicitly endorses the vault.
    endorsed: bool

    #: ``True`` only when Yearn explicitly includes the vault in its frontend.
    is_yearn: bool


@dataclass(slots=True, frozen=True)
class CachedYearnVaultIndex:
    """One worker's refreshable yDaemon metadata index.

    The scanner is a persistent process.  Keep the time of the last attempted
    refresh beside the normalised index so the process cache preserves
    per-vault efficiency without making Yearn's endorsement decisions stale
    until the scanner container restarts.

    :param vaults:
        Lowercase address-keyed normalised yDaemon metadata.
    :param fetched_at:
        Naive UTC time when this worker last refreshed the index.
    """

    #: Lowercase address-keyed normalised yDaemon metadata.
    vaults: dict[str, YearnVaultMetadata]

    #: Naive UTC time when this worker last refreshed the index.
    fetched_at: datetime.datetime


@dataclass(slots=True, frozen=True)
class YearnDetectedVaultMetadata:
    """Public Yearn catalogue metadata for one vault.

    The detected-vault endpoint is used only as positive evidence that Yearn
    publishes a vault page. Its omission of a contract is deliberately not a
    classification decision: TokenizedStrategy and compounder adapters are not
    exhaustively represented by that endpoint.

    :param description:
        Optional Yearn-authored strategy description suitable for export.
    """

    #: Optional Yearn-authored strategy description.
    description: str | None


@dataclass(slots=True, frozen=True)
class CachedYearnDetectedVaultIndex:
    """One worker's cached public Yearn vault-page index.

    :param vaults:
        Chain ID and lowercase address keyed public vault metadata.
    :param fetched_at:
        Naive UTC time when this worker last fetched the endpoint.
    """

    #: Chain ID and lowercase address keyed public vault metadata.
    vaults: dict[tuple[int, str], YearnDetectedVaultMetadata]

    #: Naive UTC time when this worker last fetched the endpoint.
    fetched_at: datetime.datetime


@dataclass(slots=True)
class YearnDetectedVaultCache:
    """Hold the mutable per-process state for the public vault catalogue.

    :param index:
        Most recently successful catalogue index.
    :param retry_after:
        Naive UTC time before a failed request may be retried.
    """

    #: Most recently successful catalogue index.
    index: CachedYearnDetectedVaultIndex | None = None

    #: Naive UTC time before a failed request may be retried.
    retry_after: datetime.datetime | None = None


def extract_yearn_short_description(description: str | None) -> str | None:
    """Extract a bounded first sentence from a Yearn description.

    Yearn descriptions are free-form Markdown and do not consistently use a
    full stop followed by a space. A punctuation-aware split handles links and
    newline-separated sentences, while a length limit prevents a malformed or
    sentence-less source value from filling the website listing.

    :param description:
        Full description from Yearn's detected-vault endpoint.
    :return:
        First sentence when it fits the compact listing field, otherwise
        ``None``.
    """

    if not description:
        return None
    sentence = re.split(r"(?<=[.!?])\s+", description.strip(), maxsplit=1)[0]
    return sentence if len(sentence) <= MAX_SHORT_DESCRIPTION_LENGTH else None


def _normalise_yearn_detected_description(description: object) -> str | None:
    """Validate one public Yearn description before website export.

    yDaemon sometimes returns template placeholders such as ``{{token}}``.
    Their substitution context is not part of this endpoint, so omit those
    records instead of displaying a misleading unresolved template.

    :param description:
        Raw endpoint description.
    :return:
        Trimmed exportable description, or ``None``.
    """

    if not isinstance(description, str):
        return None
    description = description.strip()
    if not description or "{{" in description or "}}" in description:
        return None
    return description


def _parse_yearn_detected_vault_index(payload: object) -> dict[tuple[int, str], YearnDetectedVaultMetadata]:
    """Normalise the live detected-vault response into a public-page index.

    A result exactly at the endpoint limit is still useful for positive hits,
    but may be incomplete. Callers never infer unofficial status from a miss,
    so truncation cannot create a false negative classification.

    :param payload:
        JSON-decoded detected-vault endpoint response.
    :return:
        Chain ID and lowercase address keyed public vault metadata.
    :raise ValueError:
        If the response does not have the expected list-of-vaults shape.
    """

    if not isinstance(payload, list):
        message = "Yearn detected-vault response must be a list"
        raise ValueError(message)
    if len(payload) >= YEARN_DETECTED_VAULTS_LIMIT:
        logger.warning("Yearn detected-vault response reached its limit of %d records; using it for positive matches only", len(payload))

    index: dict[tuple[int, str], YearnDetectedVaultMetadata] = {}
    for record in payload:
        if not isinstance(record, dict):
            continue
        chain_id = record.get("chainID")
        address = record.get("address")
        if type(chain_id) is not int or not isinstance(address, str):
            continue
        index[chain_id, address.lower()] = YearnDetectedVaultMetadata(
            description=_normalise_yearn_detected_description(record.get("description")),
        )
    if not index:
        message = "Yearn detected-vault response must contain vault records"
        raise ValueError(message)
    return index


def _normalise_yearn_vault_metadata(metadata: dict[str, object]) -> YearnVaultMetadata:
    """Extract the frontend-membership fields from one yDaemon vault record.

    Source records may omit nested metadata while Yearn is processing a vault.
    Missing or malformed fields normalise to ``False``.  The document parser
    separately verifies that the complete document still contains at least one
    endorsed Yearn frontend vault, preventing an upstream schema change from
    treating every vault as a known exclusion.

    :param metadata:
        One address-keyed raw yDaemon vault metadata record.
    :return:
        Immutable frontend-membership decision fields for the vault.
    """

    frontend_metadata = metadata.get("metadata")
    inclusion = frontend_metadata.get("inclusion") if isinstance(frontend_metadata, dict) else None
    return YearnVaultMetadata(
        endorsed=metadata.get("endorsed") is True,
        is_yearn=inclusion.get("isYearn") is True if isinstance(inclusion, dict) else False,
    )


def _parse_yearn_vault_index(payload: object) -> dict[str, YearnVaultMetadata]:
    """Normalise a yDaemon chain document to a lowercase address index.

    :param payload:
        JSON-decoded ``data/meta/vaults/{chain_id}.json`` document.
    :return:
        Address-keyed Yearn vault metadata.
    :raise ValueError:
        If the response is not a yDaemon chain metadata document.
    """

    if not isinstance(payload, dict) or not isinstance(vaults := payload.get("vaults"), dict):
        message = "Yearn yDaemon metadata document must contain a vaults object"
        raise ValueError(message)

    index = {address.lower(): _normalise_yearn_vault_metadata(metadata) for address, metadata in vaults.items() if isinstance(address, str) and isinstance(metadata, dict)}
    if not index:
        message = "Yearn yDaemon metadata document must contain vault records"
        raise ValueError(message)
    if not any(metadata.endorsed and metadata.is_yearn for metadata in index.values()):
        message = "Yearn yDaemon metadata document lacks an endorsed Yearn frontend vault"
        raise ValueError(message)
    return index


def _load_cached_yearn_vault_index(file: Path) -> dict[str, YearnVaultMetadata] | None:
    """Load one cached yDaemon document when it is valid.

    :param file:
        JSON cache file to read.
    :return:
        Normalised index, or ``None`` when the cache does not exist or is corrupt.
    """

    try:
        if not file.exists() or file.stat().st_size == 0:
            return None
        with file.open("rt") as input_file:
            return _parse_yearn_vault_index(json.load(input_file))
    except (OSError, JSONDecodeError, ValueError) as error:
        logger.warning("Could not load cached Yearn yDaemon metadata at %s: %s; refreshing it", file, error)
        return None


def _write_yearn_vault_cache(file: Path, payload: dict[str, object]) -> None:
    """Atomically store one chain's complete yDaemon metadata document.

    A unique temporary path ensures that a failed process cannot overwrite a
    concurrent worker's intermediate data before the final atomic replacement.

    :param file:
        Destination cache file.
    :param payload:
        Validated JSON-decoded yDaemon metadata document.
    :return:
        ``None`` after replacing the cache file.
    """

    with NamedTemporaryFile(mode="wt", encoding="utf-8", dir=file.parent, prefix=f".{file.name}.", suffix=".tmp", delete=False) as output_file:
        json.dump(payload, output_file)
        temporary_file = Path(output_file.name)
    temporary_file.replace(file)


def fetch_yearn_vaults_file_for_chain(
    chain_id: int,
    cache_path: Path = DEFAULT_CACHE_PATH,
    github_base_url: str = YEARN_YDAEMON_METADATA_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION,
) -> dict[str, YearnVaultMetadata] | None:
    """Fetch and cache Yearn's complete metadata file for one chain.

    A temporary source failure preserves an older valid cache.  When no valid
    document is available, return ``None`` so callers keep the vault's existing
    classification rather than treating an operational failure as an exclusion.

    :param chain_id:
        EVM chain ID represented by the yDaemon document.
    :param cache_path:
        Directory for persistent chain metadata caches.
    :param github_base_url:
        Raw GitHub directory containing the yDaemon chain JSON files.
    :param now_:
        Optional current UTC timestamp for deterministic cache-expiry tests.
    :param max_cache_duration:
        Maximum cache age before a refresh is attempted.
    :return:
        Lowercase address-keyed metadata, or ``None`` when no source is available.
    """

    assert type(chain_id) is int, "chain_id must be an integer"
    assert isinstance(cache_path, Path), "cache_path must be a Path"

    now_ = now_ or native_datetime_utc_now()
    cache_path.mkdir(parents=True, exist_ok=True)
    file = (cache_path / f"yearn-vaults-{chain_id}.json").resolve()

    with wait_other_writers(file):
        cached_index = _load_cached_yearn_vault_index(file)
        cache_is_fresh = cached_index is not None and now_ - native_datetime_utc_fromtimestamp(file.stat().st_mtime) <= max_cache_duration
        if cache_is_fresh:
            return cached_index

        url = f"{github_base_url.rstrip('/')}/{chain_id}.json"
        try:
            logger.info("Fetching Yearn yDaemon metadata for chain %d from %s", chain_id, url)
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                message = "Yearn yDaemon metadata document must be a JSON object"
                raise ValueError(message)
            index = _parse_yearn_vault_index(payload)
        except (HTTPError, RequestException, JSONDecodeError, ValueError) as error:
            if cached_index is not None:
                logger.warning("Could not refresh Yearn yDaemon metadata for chain %d: %s; using the previous cache", chain_id, error)
                return cached_index
            logger.warning("Yearn yDaemon metadata is unavailable for chain %d: %s", chain_id, error)
            return None

        try:
            _write_yearn_vault_cache(file, payload)
        except OSError as error:
            logger.warning("Could not cache fetched Yearn yDaemon metadata for chain %d: %s", chain_id, error)
        logger.info("Fetched %d Yearn yDaemon vault records for chain %d", len(index), chain_id)
        return index


#: Per-process successful catalogue cache shared by all Yearn adapters in one worker.
_cached_yearn_vaults: dict[int, CachedYearnVaultIndex] = {}

#: Per-chain retry deadlines after a transient source failure.
_yearn_metadata_retry_after: dict[int, datetime.datetime] = {}

#: Per-process positive public-page index shared by all Yearn adapters.
_yearn_detected_vaults_state = YearnDetectedVaultCache()


def fetch_yearn_detected_vaults() -> dict[tuple[int, str], YearnDetectedVaultMetadata] | None:
    """Fetch Yearn's public vault-page catalogue once per scanner worker.

    This endpoint is intentionally a positive-only source. A matching entry
    confirms that the vault appears on Yearn's website and can provide a
    description. A missing entry remains unknown, including when the response
    is capped at its server-side limit.

    :return:
        Public-page metadata keyed by chain ID and lowercase address, or
        ``None`` when the endpoint is temporarily unavailable.
    """

    now_ = native_datetime_utc_now()
    cached_index = _yearn_detected_vaults_state.index
    cache_is_fresh = cached_index is not None and now_ - cached_index.fetched_at <= DEFAULT_CACHE_DURATION
    if cache_is_fresh:
        return cached_index.vaults

    retry_after = _yearn_detected_vaults_state.retry_after
    if retry_after is not None and now_ < retry_after:
        return cached_index.vaults if cached_index is not None else None

    try:
        logger.info("Fetching Yearn public vault catalogue from %s", YEARN_DETECTED_VAULTS_URL)
        response = requests.get(YEARN_DETECTED_VAULTS_URL, timeout=30)
        response.raise_for_status()
        index = _parse_yearn_detected_vault_index(response.json())
    except (HTTPError, RequestException, JSONDecodeError, ValueError) as error:
        _yearn_detected_vaults_state.retry_after = now_ + UNAVAILABLE_RETRY_DELAY
        if cached_index is not None:
            logger.warning("Could not refresh Yearn public vault catalogue: %s; using the previous cache", error)
            return cached_index.vaults
        logger.warning("Yearn public vault catalogue is unavailable: %s", error)
        return None

    _yearn_detected_vaults_state.index = CachedYearnDetectedVaultIndex(vaults=index, fetched_at=now_)
    _yearn_detected_vaults_state.retry_after = None
    logger.info("Fetched %d Yearn public vault-page records", len(index))
    return index


def fetch_yearn_vault_endorsement(chain_id: int, vault_address: HexAddress) -> bool | None:
    """Fetch whether Yearn explicitly endorses a vault.

    ``False`` is returned only after successfully loading Yearn metadata and
    finding either no address entry or an entry without an explicit endorsement.
    An endorsed partner vault may be excluded from Yearn's primary frontend
    (for example, a Yearn Juiced vault), but it remains an official Yearn
    product and must not be classified as ``unofficial``.

    ``None`` deliberately represents unavailable metadata, preventing a
    transient GitHub or cache problem from blacklisting a vault.

    :param chain_id:
        EVM chain ID of the vault.
    :param vault_address:
        Vault contract address.
    :return:
        ``True`` for a Yearn-endorsed vault, ``False`` for a known unendorsed
        contract, and ``None`` when metadata is unavailable.
    """

    now_ = native_datetime_utc_now()
    cached_index = _cached_yearn_vaults.get(chain_id)
    cache_is_stale = cached_index is None or now_ - cached_index.fetched_at > DEFAULT_CACHE_DURATION
    if cache_is_stale:
        retry_after = _yearn_metadata_retry_after.get(chain_id)
        if retry_after is None or now_ >= retry_after:
            vaults = fetch_yearn_vaults_file_for_chain(chain_id)
            if vaults is None:
                _yearn_metadata_retry_after[chain_id] = now_ + UNAVAILABLE_RETRY_DELAY
            else:
                cached_index = CachedYearnVaultIndex(vaults=vaults, fetched_at=now_)
                _cached_yearn_vaults[chain_id] = cached_index
                _yearn_metadata_retry_after.pop(chain_id, None)

    if cached_index is None:
        return None

    metadata = cached_index.vaults.get(vault_address.lower())
    if metadata is None:
        return False

    return metadata.endorsed
