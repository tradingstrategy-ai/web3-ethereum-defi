"""IPOR vault offchain metadata.

- IPOR stores vault descriptions in a public S3-backed JSON file at ``api.ipor.io``
- We reverse-engineered the IPOR Fusion React SPA and discovered the
  ``/fusion/vaults-customization-list`` endpoint that serves vault metadata
  including descriptions, logos, and links
- The frontend also has hardcoded descriptions in the JavaScript bundle for some
  vaults (mostly IPOR DAO-managed), but those are not accessible via API
- Two-level caching: disk (2-day TTL) + in-process dictionary

Data flow in the IPOR frontend
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The vault info page loads descriptions from two sources and merges them:

.. code-block:: javascript

    description = customization?.description ?? config?.description

Where ``customization`` comes from this API and ``config`` is hardcoded in the
JS bundle. We only fetch the API source.

Reference:

- `IPOR Fusion app <https://app.ipor.io/fusion>`__
- API base URL: ``https://api.ipor.io``
- Customisation endpoint: ``GET /fusion/vaults-customization-list``
"""

import datetime
import json
import logging
from json import JSONDecodeError
from pathlib import Path
from typing import TypedDict

import requests

from web3 import Web3
from eth_typing import HexAddress
from eth_defi.compat import native_datetime_utc_now, native_datetime_utc_fromtimestamp
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.utils import wait_other_writers


#: Where we cache fetched IPOR metadata files
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "ipor"

#: IPOR data API base URL (S3-backed, served via CloudFront)
DEFAULT_API_BASE_URL = "https://api.ipor.io"

logger = logging.getLogger(__name__)


class IPORVaultMetadata(TypedDict):
    """Metadata about an IPOR vault from the offchain customisation API.

    Fetched from ``api.ipor.io/fusion/vaults-customization-list``.
    Discovered by reverse-engineering the IPOR Fusion React SPA JavaScript bundles.

    The customisation endpoint returns a flat JSON array. Each entry corresponds
    to a vault that has had its metadata edited by the vault's atomist (operator).
    Not all IPOR vaults have customisation entries — only those whose atomists
    have set descriptions via the IPOR frontend.

    Reference:

    - `IPOR Fusion app <https://app.ipor.io/fusion>`__
    """

    #: EVM chain ID (e.g. ``1`` for Ethereum, ``8453`` for Base)
    chain_id: int

    #: Vault contract address (checksummed)
    vault_address: str

    #: Full vault strategy description set by the atomist.
    #:
    #: Example: ``"The Bitcoin Dollar USDC Vault generates yield by acquiring sBTCD,
    #: a yield bearing 50% BTC 50% USD collateralized token..."``
    description: str | None

    #: URL to the vault logo image on IPOR's API.
    #:
    #: Example: ``"https://api.ipor.io/fusion/vaults-customization/1/0xf8.../vault-logo"``
    vault_logo_url: str | None

    #: URL to a disclaimer document, if provided by the atomist
    disclaimer_link: str | None

    #: URL to a prospectus document, if provided by the atomist
    prospectus_link: str | None


def _parse_customisation_entry(raw: dict) -> IPORVaultMetadata:
    """Parse a single vault customisation entry from the API response.

    :param raw:
        Raw JSON dict from the customisation list
    """
    return IPORVaultMetadata(
        chain_id=raw["chainId"],
        vault_address=Web3.to_checksum_address(raw["vaultAddress"]),
        description=raw.get("description"),
        vault_logo_url=raw.get("vaultLogoUrl"),
        disclaimer_link=raw.get("disclaimerLink"),
        prospectus_link=raw.get("prospectusLink"),
    )


def fetch_ipor_customisation_list(
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = datetime.timedelta(days=2),
) -> dict[tuple[int, str], IPORVaultMetadata]:
    """Fetch and cache the IPOR vault customisation list.

    The API returns a single JSON array covering all chains. We index by
    ``(chain_id, checksummed_address)`` for fast lookup.

    - Single JSON cache file for all chains
    - Multiprocess safe via file lock

    :param cache_path:
        Directory for cache files (default ``~/.tradingstrategy/cache/ipor/``)

    :param api_base_url:
        IPOR data API base URL

    :param now_:
        Override current time (for testing)

    :param max_cache_duration:
        How long before refreshing cache (default 2 days)

    :return:
        Dict mapping ``(chain_id, checksummed_address)`` to :py:class:`IPORVaultMetadata`
    """
    assert isinstance(cache_path, Path), "cache_path must be Path instance"

    cache_path.mkdir(parents=True, exist_ok=True)
    file = cache_path / "ipor_vault_customisations.json"
    file = file.resolve()

    file_size = file.stat().st_size if file.exists() else 0

    if not now_:
        now_ = native_datetime_utc_now()

    with wait_other_writers(file):
        if not file.exists() or (now_ - native_datetime_utc_fromtimestamp(file.stat().st_mtime)) > max_cache_duration or file_size == 0:
            logger.info("Re-fetching IPOR vault customisations from %s", api_base_url)

            url = f"{api_base_url}/fusion/vaults-customization-list"
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                raw_list = resp.json()
            except (requests.RequestException, JSONDecodeError) as e:
                logger.warning("Failed to fetch IPOR vault customisations from %s: %s", url, e)
                return {}

            result: dict[tuple[int, str], IPORVaultMetadata] = {}
            for raw in raw_list:
                entry = _parse_customisation_entry(raw)
                key = (entry["chain_id"], entry["vault_address"])
                result[key] = entry

            logger.info("Fetched metadata for %d IPOR vaults", len(result))

            if not result:
                logger.warning("IPOR customisation API returned 0 entries, skipping cache write to avoid poisoning the cache")
                return {}

            # Serialise with string keys for JSON compatibility
            serialisable = {f"{k[0]}:{k[1]}": v for k, v in result.items()}
            with file.open("wt") as f:
                json.dump(serialisable, f, indent=2)

            logger.info("Wrote IPOR cache %s", file)
            assert file.stat().st_size > 0, f"File {file} is empty after writing"
            return result

        else:
            timestamp = datetime.datetime.fromtimestamp(file.stat().st_mtime, tz=None)
            ago = now_ - timestamp
            logger.info("Using cached IPOR customisations from %s, last fetched at %s, ago %s", file, timestamp.isoformat(), ago)

            if file_size == 0:
                return {}

            try:
                serialised = json.load(open(file, "rt"))
            except JSONDecodeError as e:
                content = open(file, "rt").read()
                raise RuntimeError(f"Could not parse IPOR cache at {file}, length {len(content)}, content starts with {content[:100]!r}") from e

            # Deserialise string keys back to (chain_id, address) tuples
            result = {}
            for str_key, val in serialised.items():
                chain_id_str, address = str_key.split(":", 1)
                result[(int(chain_id_str), address)] = val
            return result


def fetch_ipor_vault_metadata(web3: Web3, vault_address: HexAddress) -> IPORVaultMetadata | None:
    """Fetch vault metadata from IPOR's offchain customisation API.

    - Uses a two-level cache: in-process dict + disk cache
    - Returns ``None`` if the vault has no customisation entry (i.e. the atomist
      has not set a description via the IPOR frontend)

    :param web3:
        Web3 instance (used to get chain_id and checksum address)

    :param vault_address:
        Vault contract address

    :return:
        Metadata dict or None if the vault has no customisation entry
    """
    global _cached_customisations

    chain_id = web3.eth.chain_id

    if _cached_customisations is None:
        _cached_customisations = fetch_ipor_customisation_list()

    vault_address = Web3.to_checksum_address(vault_address)
    return _cached_customisations.get((chain_id, vault_address))


#: In-process cache of fetched customisations (all chains in one dict)
_cached_customisations: dict[tuple[int, str], IPORVaultMetadata] | None = None
