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

#: Static catalogue metadata changes much less frequently than scanner cycles.
DEFAULT_CACHE_DURATION = datetime.timedelta(days=1)

#: Retry unavailable source metadata without making every vault trigger an HTTP request.
UNAVAILABLE_RETRY_DELAY = datetime.timedelta(minutes=5)


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


def _normalise_yearn_vault_metadata(metadata: dict[str, object]) -> YearnVaultMetadata:
    """Extract the frontend-membership fields from one yDaemon vault record.

    Source records may omit nested metadata while Yearn is processing a vault.
    Missing or malformed fields deliberately normalise to ``False``: once a
    complete source document has loaded, absence of an explicit endorsement or
    frontend inclusion is a known exclusion rather than a source outage.

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

    return {address.lower(): _normalise_yearn_vault_metadata(metadata) for address, metadata in vaults.items() if isinstance(address, str) and isinstance(metadata, dict)}


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
_cached_yearn_vaults: dict[int, dict[str, YearnVaultMetadata]] = {}

#: Per-chain retry deadlines after a transient source failure.
_yearn_metadata_retry_after: dict[int, datetime.datetime] = {}


def get_yearn_frontend_membership(chain_id: int, vault_address: HexAddress) -> bool | None:
    """Check whether Yearn endorses and includes a vault in its frontend.

    ``False`` is returned only after successfully loading Yearn metadata and
    finding either no address entry or an entry without both required markers.
    ``None`` deliberately represents unavailable metadata, preventing a
    transient GitHub or cache problem from blacklisting a vault.

    :param chain_id:
        EVM chain ID of the vault.
    :param vault_address:
        Vault contract address.
    :return:
        ``True`` for an official Yearn frontend vault, ``False`` for a known
        unlisted contract, and ``None`` when metadata is unavailable.
    """

    vaults = _cached_yearn_vaults.get(chain_id)
    if vaults is None:
        now_ = native_datetime_utc_now()
        retry_after = _yearn_metadata_retry_after.get(chain_id)
        if retry_after is not None and now_ < retry_after:
            return None

        vaults = fetch_yearn_vaults_file_for_chain(chain_id)
        if vaults is None:
            _yearn_metadata_retry_after[chain_id] = now_ + UNAVAILABLE_RETRY_DELAY
            return None

        _cached_yearn_vaults[chain_id] = vaults
        _yearn_metadata_retry_after.pop(chain_id, None)

    metadata = vaults.get(vault_address.lower())
    if metadata is None:
        return False

    return metadata.endorsed and metadata.is_yearn
