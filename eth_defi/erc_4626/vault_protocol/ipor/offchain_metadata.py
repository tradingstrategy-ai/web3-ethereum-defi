"""IPOR vault offchain metadata.

- IPOR stores vault descriptions, logos and optional atomist names in a public
  S3-backed JSON file at ``api.ipor.io``
- We reverse-engineered the IPOR Fusion React SPA and discovered the
  ``/fusion/vaults-customization-list`` endpoint that serves vault metadata
  including descriptions, logos, links and optional ``curatorName`` values
- The frontend bundle also contains the public IPOR app vault configuration.
  We parse only the address-keyed ``atomist`` values from this bundle, because
  production API rows currently expose ``curatorName`` sparsely.
- Vault accessors fetch this metadata lazily on first access and then reuse the
  local disk cache and module-level in-process cache where available.

Data flow in the IPOR frontend
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The vault info page loads descriptions from two sources and merges them:

.. code-block:: javascript

    description = customization?.description ?? config?.description

Where ``customization`` comes from the API and ``config`` is hardcoded in the
JS bundle.  Descriptions come from the API customisation source.  Manager names
prefer API ``curatorName`` and fall back to the frontend ``atomist`` value.

Reference:

- `IPOR Fusion app <https://app.ipor.io/fusion>`__
- API base URL: ``https://api.ipor.io``
- Customisation endpoint: ``GET /fusion/vaults-customization-list``
"""

import datetime
import json
import logging
import re
from json import JSONDecodeError
from pathlib import Path
from typing import TypedDict
from urllib.parse import urljoin

import requests
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.utils import wait_other_writers

#: Where we cache fetched IPOR metadata files
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "ipor"

#: IPOR data API base URL (S3-backed, served via CloudFront)
DEFAULT_API_BASE_URL = "https://api.ipor.io"

#: IPOR Fusion frontend base URL. The React bundle contains the atomist config
#: used by the public app when the API customisation entry has no curator name.
DEFAULT_APP_BASE_URL = "https://app.ipor.io"

#: How long fetched IPOR offchain metadata is trusted before refresh.
DEFAULT_CACHE_DURATION = datetime.timedelta(days=2)

#: Script path pattern in the IPOR app HTML.
#:
#: IPOR currently serves same-origin Vite assets under ``/assets/``, but allow
#: absolute CDN URLs as well so a frontend hosting change does not make manager
#: cache refreshes depend only on stale disk data.
FRONTEND_BUNDLE_RE = re.compile(r"""["'](?P<asset>https?://[^"']+/assets/[^"']+\.js|/assets/[^"']+\.js|assets/[^"']+\.js)["']""")

#: Vault address pattern inside the minified frontend config.
FRONTEND_ADDRESS_RE = re.compile(r'address:"(?P<address>0x[a-fA-F0-9]{40})"')

#: Atomist field pattern inside the minified frontend config.
#:
#: The negative lookbehind keeps similarly named keys such as ``xatomist`` from
#: being treated as the manager field.
FRONTEND_ATOMIST_RE = re.compile(r'(?<![A-Za-z0-9_])atomist:"(?P<atomist>[^"]+)"')

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

    #: Optional atomist/curator display name from the IPOR customisation API.
    curator_name: str | None

    #: URL to a disclaimer document, if provided by the atomist
    disclaimer_link: str | None

    #: URL to a prospectus document, if provided by the atomist
    prospectus_link: str | None


class IPORListedVaultMetadata(TypedDict):
    """Metadata about an IPOR-listed vault from the public vault list.

    Fetched from ``api.ipor.io/fusion/vaults``.

    Unlike ``vaults-customization-list``, this endpoint is the broad public
    inventory the Fusion app uses for vault metrics. Most production vaults do
    not have custom descriptions, but they still appear in this list and should
    not be treated as unofficial merely because no customisation row exists.

    Reference:

    - `IPOR Fusion app <https://app.ipor.io/fusion>`__
    """

    #: EVM chain ID (e.g. ``1`` for Ethereum, ``8453`` for Base)
    chain_id: int

    #: Vault contract address (checksummed)
    vault_address: str

    #: IPOR display name for the vault.
    name: str | None

    #: Denomination asset symbol, e.g. ``USDC``.
    asset: str | None

    #: Denomination token address (checksummed), when IPOR exposes it.
    asset_address: str | None

    #: Current TVL as returned by IPOR, usually a decimal string or ``None``.
    tvl: str | None


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
        curator_name=raw.get("curatorName"),
        disclaimer_link=raw.get("disclaimerLink"),
        prospectus_link=raw.get("prospectusLink"),
    )


def _parse_listed_vault_entry(raw: dict) -> IPORListedVaultMetadata:
    """Parse a single vault entry from the IPOR public vault list.

    :param raw:
        Raw JSON dict from ``/fusion/vaults``.

    :return:
        Normalised vault list metadata.
    """
    asset_address = raw.get("assetAddress")
    return IPORListedVaultMetadata(
        chain_id=raw["chainId"],
        vault_address=Web3.to_checksum_address(raw["address"]),
        name=raw.get("name"),
        asset=raw.get("asset"),
        asset_address=Web3.to_checksum_address(asset_address) if asset_address else None,
        tvl=raw.get("tvl"),
    )


def _read_ipor_customisation_cache(
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> dict[tuple[int, str], IPORVaultMetadata]:
    """Read cached IPOR vault customisations without network access.

    This helper is an implementation detail for
    :py:func:`fetch_ipor_customisation_list`. It parses only the local JSON
    cache that the fetcher writes after a successful IPOR API response.

    :param cache_path:
        Directory for IPOR cache files.

    :return:
        Dict mapping ``(chain_id, checksummed_address)`` to
        :py:class:`IPORVaultMetadata`. Returns an empty dict if the cache file
        does not exist or is empty.
    """
    assert isinstance(cache_path, Path), "cache_path must be Path instance"

    file = (cache_path / "ipor_vault_customisations.json").resolve()
    if not file.exists() or file.stat().st_size == 0:
        return {}

    serialised = _read_json_cache(file)
    result: dict[tuple[int, str], IPORVaultMetadata] = {}
    for str_key, val in serialised.items():
        chain_id_str, address = str_key.split(":", 1)
        result[int(chain_id_str), Web3.to_checksum_address(address)] = val
    return result


def _read_ipor_vault_list_cache(
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> dict[tuple[int, str], IPORListedVaultMetadata]:
    """Read cached IPOR public vault list without network access.

    This helper is an implementation detail for
    :py:func:`fetch_ipor_vault_list`. It parses only the local JSON cache that
    the fetcher writes after a successful IPOR API response.

    :param cache_path:
        Directory for IPOR cache files.

    :return:
        Dict mapping ``(chain_id, checksummed_address)`` to
        :py:class:`IPORListedVaultMetadata`. Returns an empty dict if the cache
        file does not exist or is empty.
    """
    assert isinstance(cache_path, Path), "cache_path must be Path instance"

    file = (cache_path / "ipor_vaults.json").resolve()
    if not file.exists() or file.stat().st_size == 0:
        return {}

    serialised = _read_json_cache(file)
    result: dict[tuple[int, str], IPORListedVaultMetadata] = {}
    for str_key, val in serialised.items():
        chain_id_str, address = str_key.split(":", 1)
        result[int(chain_id_str), Web3.to_checksum_address(address)] = val
    return result


def _cache_is_stale(file: Path, now_: datetime.datetime, max_cache_duration: datetime.timedelta) -> bool:
    """Return ``True`` if a cache file should be refreshed.

    :param file:
        Cache file path.

    :param now_:
        Naive UTC timestamp used for repeatable tests.

    :param max_cache_duration:
        Cache time-to-live.

    :return:
        ``True`` when the file is missing, empty, or older than the TTL.
    """
    if not file.exists():
        return True
    if file.stat().st_size == 0:
        return True
    return (now_ - native_datetime_utc_fromtimestamp(file.stat().st_mtime)) > max_cache_duration


def _read_json_cache(file: Path) -> dict:
    """Read a JSON cache file with a helpful parse error.

    :param file:
        Cache file path.

    :return:
        Parsed JSON dict.
    """
    try:
        with file.open("rt", encoding="utf-8") as f:
            return json.load(f)
    except JSONDecodeError as e:
        content = file.read_text(encoding="utf-8")
        raise RuntimeError(f"Could not parse IPOR cache at {file}, length {len(content)}, content starts with {content[:100]!r}") from e


def _find_frontend_bundle_urls(html: str, app_base_url: str) -> list[str]:
    """Extract IPOR app JavaScript bundle URLs from the app HTML.

    The IPOR frontend is a Vite/React single-page app. The vault config has
    historically lived in the main entry bundle, but relying on the first
    ``/assets/*.js`` match is fragile because HTML can also reference helper
    chunks. Return all script-like JavaScript assets in document order so the
    caller can parse each candidate and merge the address-keyed atomist data.

    :param html:
        HTML returned by the IPOR app entrypoint.

    :param app_base_url:
        IPOR app base URL used to resolve relative asset paths.

    :return:
        Absolute bundle URLs in document order, without duplicates.
    """
    urls: list[str] = []
    seen: set[str] = set()
    for match in FRONTEND_BUNDLE_RE.finditer(html):
        url = urljoin(app_base_url, match.group("asset"))
        if url in seen:
            continue
        urls.append(url)
        seen.add(url)

    if not urls:
        msg = "Could not find IPOR frontend JavaScript bundle in app HTML"
        raise RuntimeError(msg)

    return urls


def _extract_ipor_frontend_atomists(bundle: str) -> dict[str, str]:
    """Extract ``vault_address -> atomist`` from the IPOR frontend bundle.

    IPOR's public customisation API currently advertises optional
    ``curatorName`` fields, but the live endpoint often returns them as null.
    The frontend fills the gap from a bundled vault config where each vault
    object currently serialises ``address`` before ``atomist``. This parser
    intentionally looks only inside the short text span between one ``address``
    field and the next one: if IPOR changes the bundle shape, we prefer missing
    metadata plus a warning over assigning the next vault's atomist to the
    current vault.

    :param bundle:
        Minified IPOR JavaScript bundle.

    :return:
        Dict keyed by lower-case vault address.
    """
    address_matches = list(FRONTEND_ADDRESS_RE.finditer(bundle))
    atomists: dict[str, str] = {}

    for idx, address_match in enumerate(address_matches):
        address = address_match.group("address").lower()
        next_address_start = address_matches[idx + 1].start() if idx + 1 < len(address_matches) else len(bundle)
        segment = bundle[address_match.start() : next_address_start]
        atomist_match = FRONTEND_ATOMIST_RE.search(segment)
        if not atomist_match:
            continue
        atomists[address] = atomist_match.group("atomist")

    return atomists


def _read_ipor_frontend_atomist_cache(
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> dict[str, str]:
    """Read cached IPOR frontend atomists without network access.

    The scanner refreshes this cache through
    :py:func:`fetch_ipor_frontend_atomists`. It is used as a stale-cache
    fallback when the IPOR app or CDN is temporarily unavailable.

    :param cache_path:
        Directory for IPOR cache files.

    :return:
        Dict keyed by lower-case vault address. Returns an empty dict when the
        cache is missing or empty.
    """
    assert isinstance(cache_path, Path), "cache_path must be Path instance"

    file = (cache_path / "ipor_frontend_atomists.json").resolve()
    if not file.exists() or file.stat().st_size == 0:
        return {}
    return _read_json_cache(file)


def _remember_frontend_atomists(
    cache_key: tuple[str, str, datetime.timedelta],
    atomists: dict[str, str],
    *,
    use_process_cache: bool,
) -> dict[str, str]:
    """Store frontend atomists in the process cache when enabled.

    :param cache_key:
        Cache key derived from source URL, cache directory and TTL.

    :param atomists:
        Parsed address-keyed atomist data.

    :param use_process_cache:
        ``False`` when tests pass an explicit timestamp and need to exercise
        disk-cache expiry.

    :return:
        The same atomist mapping for convenient early returns.
    """
    if use_process_cache:
        _cached_frontend_atomists[cache_key] = atomists
    return atomists


def fetch_ipor_frontend_atomists(
    cache_path: Path = DEFAULT_CACHE_PATH,
    app_base_url: str = DEFAULT_APP_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION,
) -> dict[str, str]:
    """Fetch and cache IPOR frontend atomist metadata.

    The cache stores only the parsed ``vault_address -> atomist`` mapping, not
    the full JavaScript bundle. If refresh fails but an older cache exists, the
    stale cache is returned so manager attribution does not disappear because of
    a transient app or CDN failure.

    :param cache_path:
        Directory for cache files.

    :param app_base_url:
        IPOR Fusion app base URL.

    :param now_:
        Override current time for tests.

    :param max_cache_duration:
        Cache time-to-live.

    :return:
        Dict keyed by lower-case vault address.
    """
    assert isinstance(cache_path, Path), "cache_path must be Path instance"

    cache_key = (str(cache_path.resolve()), app_base_url, max_cache_duration)
    use_process_cache = now_ is None
    if use_process_cache and cache_key in _cached_frontend_atomists:
        return _cached_frontend_atomists[cache_key]

    cache_path.mkdir(parents=True, exist_ok=True)
    file = (cache_path / "ipor_frontend_atomists.json").resolve()

    if not now_:
        now_ = native_datetime_utc_now()

    with wait_other_writers(file):
        if _cache_is_stale(file, now_, max_cache_duration):
            try:
                app_url = urljoin(app_base_url, "/fusion")
                html_response = requests.get(app_url, timeout=30)
                html_response.raise_for_status()
                bundle_urls = _find_frontend_bundle_urls(html_response.text, app_base_url)
                atomists: dict[str, str] = {}
                for bundle_url in bundle_urls:
                    try:
                        bundle_response = requests.get(bundle_url, timeout=60)
                        bundle_response.raise_for_status()
                    except requests.RequestException as e:
                        logger.warning("Failed to fetch IPOR frontend bundle %s: %s", bundle_url, e)
                        continue
                    atomists.update(_extract_ipor_frontend_atomists(bundle_response.text))
            except requests.RequestException as e:
                logger.warning("Failed to fetch IPOR frontend atomists from %s: %s", app_base_url, e)
                atomists = _read_ipor_frontend_atomist_cache(cache_path)
                return _remember_frontend_atomists(cache_key, atomists, use_process_cache=use_process_cache)
            except RuntimeError as e:
                logger.warning("Failed to parse IPOR frontend atomists from %s: %s", app_base_url, e)
                atomists = _read_ipor_frontend_atomist_cache(cache_path)
                return _remember_frontend_atomists(cache_key, atomists, use_process_cache=use_process_cache)

            if not atomists:
                logger.warning("IPOR frontend atomist parser returned 0 entries, skipping cache write")
                atomists = _read_ipor_frontend_atomist_cache(cache_path)
                return _remember_frontend_atomists(cache_key, atomists, use_process_cache=use_process_cache)

            with file.open("wt", encoding="utf-8") as f:
                json.dump(atomists, f, indent=2, sort_keys=True)

            logger.info("Wrote IPOR frontend atomist cache %s with %d entries", file, len(atomists))
            assert file.stat().st_size > 0, f"File {file} is empty after writing"
            return _remember_frontend_atomists(cache_key, atomists, use_process_cache=use_process_cache)

        atomists = _read_ipor_frontend_atomist_cache(cache_path)
        return _remember_frontend_atomists(cache_key, atomists, use_process_cache=use_process_cache)


def fetch_ipor_atomist_names(
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    app_base_url: str = DEFAULT_APP_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION,
) -> set[str]:
    """Fetch known IPOR atomist names for curator maintenance checks.

    This helper is for audits and tests that need the set of IPOR manager
    names, not a vault-specific lookup. Vault accessors should call
    :py:func:`fetch_ipor_vault_atomist` instead.

    :param cache_path:
        Directory for IPOR cache files.

    :param api_base_url:
        IPOR data API base URL.

    :param app_base_url:
        IPOR Fusion app base URL.

    :param now_:
        Override current time for tests.

    :param max_cache_duration:
        Cache time-to-live.

    :return:
        Set of atomist display names IPOR exposes through API or frontend data.
    """
    customisations = fetch_ipor_customisation_list(
        cache_path=cache_path,
        api_base_url=api_base_url,
        now_=now_,
        max_cache_duration=max_cache_duration,
    )
    frontend_atomists = fetch_ipor_frontend_atomists(
        cache_path=cache_path,
        app_base_url=app_base_url,
        now_=now_,
        max_cache_duration=max_cache_duration,
    )

    atomists = set(frontend_atomists.values())
    atomists.update(metadata["curator_name"] for metadata in customisations.values() if metadata.get("curator_name"))
    return atomists


def fetch_ipor_customisation_list(
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION,
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

    if not now_:
        now_ = native_datetime_utc_now()

    with wait_other_writers(file):
        if _cache_is_stale(file, now_, max_cache_duration):
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

        timestamp = native_datetime_utc_fromtimestamp(file.stat().st_mtime)
        ago = now_ - timestamp
        logger.info("Using cached IPOR customisations from %s, last fetched at %s, ago %s", file, timestamp.isoformat(), ago)

        return _read_ipor_customisation_cache(cache_path)


def fetch_ipor_vault_list(
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION,
) -> dict[tuple[int, str], IPORListedVaultMetadata]:
    """Fetch and cache IPOR's public vault list.

    The API returns an object whose ``vaults`` array covers all chains. We index
    by ``(chain_id, checksummed_address)`` for fast lookup.

    This is the broad official IPOR offchain source used for deciding whether a
    vault is listed by IPOR. Do not use the customisation endpoint for that:
    customisation rows are sparse and mostly indicate descriptions, logos or
    prospectus links.

    :param cache_path:
        Directory for cache files.

    :param api_base_url:
        IPOR data API base URL.

    :param now_:
        Override current time for tests.

    :param max_cache_duration:
        How long before refreshing cache.

    :return:
        Dict mapping ``(chain_id, checksummed_address)`` to
        :py:class:`IPORListedVaultMetadata`.
    """
    assert isinstance(cache_path, Path), "cache_path must be Path instance"

    cache_path.mkdir(parents=True, exist_ok=True)
    file = (cache_path / "ipor_vaults.json").resolve()

    if not now_:
        now_ = native_datetime_utc_now()

    with wait_other_writers(file):
        if _cache_is_stale(file, now_, max_cache_duration):
            logger.info("Re-fetching IPOR vault list from %s", api_base_url)

            url = f"{api_base_url}/fusion/vaults"
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                payload = resp.json()
            except (requests.RequestException, JSONDecodeError) as e:
                logger.warning("Failed to fetch IPOR vault list from %s: %s", url, e)
                return _read_ipor_vault_list_cache(cache_path)

            raw_list = payload.get("vaults") if isinstance(payload, dict) else None
            if not isinstance(raw_list, list):
                logger.warning("IPOR vault list from %s did not contain a vaults array", url)
                return _read_ipor_vault_list_cache(cache_path)

            result: dict[tuple[int, str], IPORListedVaultMetadata] = {}
            for raw in raw_list:
                entry = _parse_listed_vault_entry(raw)
                key = (entry["chain_id"], entry["vault_address"])
                result[key] = entry

            logger.info("Fetched %d IPOR listed vaults", len(result))

            if not result:
                logger.warning("IPOR vault list API returned 0 entries, skipping cache write to avoid poisoning the cache")
                return _read_ipor_vault_list_cache(cache_path)

            serialisable = {f"{k[0]}:{k[1]}": v for k, v in result.items()}
            with file.open("wt", encoding="utf-8") as f:
                json.dump(serialisable, f, indent=2)

            logger.info("Wrote IPOR vault list cache %s", file)
            assert file.stat().st_size > 0, f"File {file} is empty after writing"
            return result

        return _read_ipor_vault_list_cache(cache_path)


def _fetch_ipor_vault_list_cached(
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION,
) -> dict[tuple[int, str], IPORListedVaultMetadata]:
    """Fetch IPOR public vault list metadata with a process-local cache.

    :param cache_path:
        Directory for IPOR cache files.

    :param api_base_url:
        IPOR data API base URL.

    :param now_:
        Override current time for tests. Passing this disables the in-process
        cache so TTL-sensitive tests can exercise the disk cache logic.

    :param max_cache_duration:
        Cache time-to-live.

    :return:
        Dict mapping ``(chain_id, checksummed_address)`` to
        :py:class:`IPORListedVaultMetadata`.
    """
    cache_key = (str(cache_path.resolve()), api_base_url, max_cache_duration)
    if now_ is None and cache_key in _cached_vault_list:
        return _cached_vault_list[cache_key]

    vaults = fetch_ipor_vault_list(
        cache_path=cache_path,
        api_base_url=api_base_url,
        now_=now_,
        max_cache_duration=max_cache_duration,
    )
    if now_ is None:
        _cached_vault_list[cache_key] = vaults
    return vaults


def _fetch_ipor_customisation_list_cached(
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION,
) -> dict[tuple[int, str], IPORVaultMetadata]:
    """Fetch IPOR customisation metadata with a process-local cache.

    The scanner may construct several IPOR vault instances in one process.
    Reusing parsed customisation metadata avoids repeated file locking and JSON
    parsing while preserving testability for callers that pass ``now_``.

    :param cache_path:
        Directory for IPOR cache files.

    :param api_base_url:
        IPOR data API base URL.

    :param now_:
        Override current time for tests. Passing this disables the in-process
        cache so TTL-sensitive tests can exercise the disk cache logic.

    :param max_cache_duration:
        Cache time-to-live.

    :return:
        Dict mapping ``(chain_id, checksummed_address)`` to
        :py:class:`IPORVaultMetadata`.
    """
    cache_key = (str(cache_path.resolve()), api_base_url, max_cache_duration)
    if now_ is None and cache_key in _cached_customisations:
        return _cached_customisations[cache_key]

    customisations = fetch_ipor_customisation_list(
        cache_path=cache_path,
        api_base_url=api_base_url,
        now_=now_,
        max_cache_duration=max_cache_duration,
    )
    if now_ is None:
        _cached_customisations[cache_key] = customisations
    return customisations


def fetch_ipor_vault_is_listed(
    web3: Web3,
    vault_address: HexAddress,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    app_base_url: str = DEFAULT_APP_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION,
) -> bool:
    """Check whether IPOR lists a vault in public offchain sources.

    ``vaults-customization-list`` is intentionally not treated as the sole
    source of truth because many live IPOR vaults have no custom description.
    The broad ``/fusion/vaults`` list is checked first, with the customisation
    API and frontend atomist map as fallbacks for temporary API shape changes.

    :param web3:
        Web3 instance used to get the EVM chain id.

    :param vault_address:
        Vault contract address.

    :param cache_path:
        Directory for IPOR cache files.

    :param api_base_url:
        IPOR data API base URL.

    :param app_base_url:
        IPOR Fusion app base URL.

    :param now_:
        Override current time for tests.

    :param max_cache_duration:
        Cache time-to-live.

    :return:
        ``True`` when the vault appears in IPOR public offchain data.
    """
    chain_id = web3.eth.chain_id
    vault_address = Web3.to_checksum_address(vault_address)

    listed_vaults = _fetch_ipor_vault_list_cached(
        cache_path=cache_path,
        api_base_url=api_base_url,
        now_=now_,
        max_cache_duration=max_cache_duration,
    )
    if (chain_id, vault_address) in listed_vaults:
        return True

    customisations = _fetch_ipor_customisation_list_cached(
        cache_path=cache_path,
        api_base_url=api_base_url,
        now_=now_,
        max_cache_duration=max_cache_duration,
    )
    if (chain_id, vault_address) in customisations:
        return True

    frontend_atomists = fetch_ipor_frontend_atomists(
        cache_path=cache_path,
        app_base_url=app_base_url,
        now_=now_,
        max_cache_duration=max_cache_duration,
    )
    return vault_address.lower() in frontend_atomists


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
    chain_id = web3.eth.chain_id

    customisations = _fetch_ipor_customisation_list_cached()
    vault_address = Web3.to_checksum_address(vault_address)
    return customisations.get((chain_id, vault_address))


def fetch_ipor_vault_atomist(
    web3: Web3,
    vault_address: HexAddress,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    api_base_url: str = DEFAULT_API_BASE_URL,
    app_base_url: str = DEFAULT_APP_BASE_URL,
    now_: datetime.datetime | None = None,
    max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION,
) -> str | None:
    """Fetch an IPOR vault atomist display name.

    This accessor-style helper mirrors other protocol offchain metadata
    modules: the first vault property access triggers the fetch, then the
    local disk cache and in-process cache are reused by later calls.

    :param web3:
        Web3 instance used to get the EVM chain id.

    :param vault_address:
        Vault contract address.

    :param cache_path:
        Directory for IPOR cache files.

    :param api_base_url:
        IPOR data API base URL.

    :param app_base_url:
        IPOR Fusion app base URL.

    :param now_:
        Override current time for tests.

    :param max_cache_duration:
        Cache time-to-live.

    :return:
        Atomist display name, or ``None`` if IPOR does not expose one.
    """
    chain_id = web3.eth.chain_id
    vault_address = Web3.to_checksum_address(vault_address)

    customisations = _fetch_ipor_customisation_list_cached(
        cache_path=cache_path,
        api_base_url=api_base_url,
        now_=now_,
        max_cache_duration=max_cache_duration,
    )
    metadata = customisations.get((chain_id, vault_address))
    if metadata and metadata.get("curator_name"):
        return metadata["curator_name"]

    # The IPOR customisation API advertises ``curatorName``, but production rows
    # may leave it null. Fall back to the address-keyed atomist that the IPOR
    # app itself displays from its frontend vault config.
    frontend_atomists = fetch_ipor_frontend_atomists(
        cache_path=cache_path,
        app_base_url=app_base_url,
        now_=now_,
        max_cache_duration=max_cache_duration,
    )
    return frontend_atomists.get(vault_address.lower())


#: In-process cache of fetched customisations keyed by source and TTL.
_cached_customisations: dict[tuple[str, str, datetime.timedelta], dict[tuple[int, str], IPORVaultMetadata]] = {}

#: In-process cache of fetched listed vault metadata keyed by source and TTL.
_cached_vault_list: dict[tuple[str, str, datetime.timedelta], dict[tuple[int, str], IPORListedVaultMetadata]] = {}

#: In-process cache of fetched frontend atomists keyed by source and TTL.
_cached_frontend_atomists: dict[tuple[str, str, datetime.timedelta], dict[str, str]] = {}
