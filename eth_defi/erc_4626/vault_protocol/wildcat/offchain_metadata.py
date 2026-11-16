"""Wildcat market offchain metadata.

- Wildcat stores borrower profiles and market descriptions in its web app
  database, not onchain
- We read the public Next.js app source,
  `wildcat-app-v2 <https://github.com/wildcat-finance/wildcat-app-v2>`__
  (commit ``1de5d59``), and use two unauthenticated, undocumented API routes
  at ``https://app.wildcat.finance/api``:

  - ``GET /market-summary/{market}?chainId={chain_id}`` returns
    ``{"description": ...}`` with the borrower-written market description,
    or 404 when the market has none
  - ``GET /profiles/{borrower}?chainId={chain_id}`` returns
    ``{"profile": {...}}`` with the borrower profile, or 404

- The borrower's legal ``name`` comes from Wildcat's KYB check and is
  verified. Profile descriptions, aliases, links and market descriptions are
  supplied by the borrower and are not verified or monitored, see
  `how borrowers are onboarded <https://github.com/wildcat-finance/wildcat-docs/blob/main/using-wildcat/how-borrowers-are-onboarded.md>`__
- Entries are cached in one JSON file per chain, each with its own expiry.
  The file is guarded by a file lock and replaced atomically, so parallel
  scanner threads share one copy
- A failed request keeps the previously cached value and retries after
  :py:data:`FAILED_REFRESH_RETRY_DELAY`. An API omission of an entry or a
  field never erases a previously cached value; a 404 for an uncached entry
  is stored as ``None``. A borrower's deliberate removal is therefore not
  picked up; delete the chain's cache file to invalidate stale text
"""

import datetime
import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http import HTTPStatus
from json import JSONDecodeError
from pathlib import Path
from typing import TypedDict

import requests
from atomicwrites import atomic_write
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.utils import wait_other_writers

logger = logging.getLogger(__name__)

#: Where we cache fetched Wildcat metadata files
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "wildcat"

#: Wildcat web app API base URL, from the public app source
DEFAULT_API_BASE_URL = "https://app.wildcat.finance/api"

#: How long a fetched entry is used before the scanner refreshes it.
DEFAULT_CACHE_DURATION = datetime.timedelta(days=2)

#: Prevent one failed refresh from being retried for every market in the same scan.
FAILED_REFRESH_RETRY_DELAY = datetime.timedelta(hours=1)

#: HTTP timeout for one API request, in seconds.
REQUEST_TIMEOUT = 30

#: Cache directory used instead of :py:data:`DEFAULT_CACHE_PATH` inside
#: :py:func:`redirect_metadata_cache`.
_redirected_cache_path: Path | None = None

#: Markdown heading markers, list bullets and emphasis markers in borrower text.
_MARKDOWN_RE = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+|^[ \t]*[-*+][ \t]+|\*+|__")

#: Sentence boundary: closing punctuation followed by whitespace or the line end.
#: A dot inside a URL or a number is not a boundary.
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?](\s|$)")


class WildcatMarketMetadata(TypedDict):
    """Offchain metadata for one Wildcat market.

    Combined from the market description and its borrower's profile.
    """

    #: Borrower-written market description in Markdown.
    #: ``None`` when the market has no description. Not verified by Wildcat.
    market_description: str | None

    #: Borrower's KYB-verified legal name, e.g. ``Wintermute Trading Ltd``.
    borrower_name: str | None

    #: Borrower-written profile description in Markdown.
    #: Not verified by Wildcat.
    borrower_description: str | None


def strip_markdown(text: str | None) -> str | None:
    """Remove Markdown structure markers from borrower-written text.

    Headings, list bullets and ``*``, ``**`` and ``__`` emphasis are removed,
    so a summary is plain text.

    :param text:
        Borrower-written Markdown, or ``None``.
    :return:
        Text without Markdown markers, or ``None`` for empty input.
    """

    if not text:
        return None
    return _MARKDOWN_RE.sub("", text).strip() or None


def extract_summary_line(text: str | None) -> str | None:
    """Pick the line of borrower text that a one-sentence summary starts from.

    Borrowers often open with a heading, a date or a link on its own line,
    such as ``Loan market info`` or ``March 25, 2026``. Flattening the text
    would join that line to the first real sentence. Instead, use the first
    line that contains a sentence boundary, or the first line when none does.
    A line starting with a lower-case letter continues a sentence the
    borrower wrapped, so it is joined to the previous line first.

    :param text:
        Borrower-written Markdown, or ``None``.
    :return:
        One plain-text line for ``normalise_vault_short_description()``, or ``None``.
    """

    lines: list[str] = []
    for line in (line.strip() for line in (strip_markdown(text) or "").splitlines()):
        if line and lines and line[0].islower():
            lines[-1] = f"{lines[-1]} {line}"
        elif line:
            lines.append(line)
    if not lines:
        return None
    return next((line for line in lines if _SENTENCE_BOUNDARY_RE.search(line)), lines[0])


@contextmanager
def redirect_metadata_cache(cache_path: Path) -> Iterator[None]:
    """Use another cache directory for metadata fetched by market adapters.

    Market adapters fetch metadata lazily with the default cache. A migration
    preview must not write provider caches, so it builds scanner rows inside
    this context with a temporary directory. Not thread-safe: use it around
    sequential work only.

    :param cache_path:
        Directory for the cache files while the context is active.
    :return:
        Context manager restoring the default cache on exit.
    """
    global _redirected_cache_path  # noqa: PLW0603
    previous = _redirected_cache_path
    _redirected_cache_path = cache_path
    try:
        yield
    finally:
        _redirected_cache_path = previous


def _get_cache_file(cache_path: Path, chain_id: int) -> Path:
    """Resolve one chain's Wildcat cache file.

    :param cache_path:
        Directory containing Wildcat metadata cache files.
    :param chain_id:
        EVM chain id.
    :return:
        Absolute path to the chain's JSON cache file.
    """

    return (cache_path / f"wildcat_markets_chain_{chain_id}.json").resolve()


def _fetch_json(url: str) -> tuple[bool, dict | None]:
    """Fetch one Wildcat API document.

    :param url:
        API URL.
    :return:
        ``(succeeded, document)``. A 404 is a successful ``None`` result.
        Transport, HTTP and JSON failures return ``(False, None)``.
    """

    logger.debug("Fetching Wildcat metadata from %s", url)
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code == HTTPStatus.NOT_FOUND:
            return True, None
        resp.raise_for_status()
        document = resp.json()
    except (requests.RequestException, JSONDecodeError) as e:
        logger.warning("Failed to fetch Wildcat metadata from %s: %s", url, e)
        return False, None
    if not isinstance(document, dict):
        logger.warning("Unexpected Wildcat metadata response from %s: %r", url, document)
        return False, None
    return True, document


def _text_field(document: dict, field: str) -> str | None:
    """Read an optional text field from an API document.

    :param document:
        API response object.
    :param field:
        Field name.
    :return:
        Stripped text, or ``None`` when the field is missing or empty.
    :raise ValueError:
        If the field is not a string, which means the undocumented API changed.
    """
    value = document.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        message = f"Wildcat API field {field} is {type(value).__name__}, not text"
        raise ValueError(message)
    return value.strip() or None


def _parse_market_summary(document: dict | None) -> dict | None:
    """Extract the market description from a ``/market-summary`` response.

    :param document:
        Response document, or ``None`` for 404.
    :return:
        Cached value with a ``description`` key, or ``None``.
    """

    if document is None:
        return None
    return {"description": _text_field(document, "description")}


def _parse_profile(document: dict | None) -> dict | None:
    """Extract the used fields from a ``/profiles`` response.

    :param document:
        Response document, or ``None`` for 404.
    :return:
        Cached value with ``name`` and ``description`` keys, or ``None``.
    """

    profile = (document or {}).get("profile")
    if not isinstance(profile, dict):
        return None
    return {
        "name": _text_field(profile, "name"),
        "description": _text_field(profile, "description"),
    }


def _refresh_entry(
    *,
    entries: dict[str, dict],
    key: str,
    url: str,
    parser: Callable[[dict | None], dict | None],
    now_: datetime.datetime,
    max_cache_duration: datetime.timedelta,
) -> bool:
    """Refresh one cached entry when it has expired.

    :param entries:
        Mutable cache section, keyed by lowercased address.
    :param key:
        Lowercased market or borrower address.
    :param url:
        API URL of the entry.
    :param parser:
        Converts the response document into the cached value.
    :param now_:
        Current naive UTC time.
    :param max_cache_duration:
        Expiry of a successful fetch.
    :return:
        ``True`` if the cache section changed and must be written.
    """

    entry = entries.get(key)
    if entry is not None and datetime.datetime.fromisoformat(entry["expires_at"]) > now_:
        return False

    previous = entry["value"] if entry is not None else None
    succeeded, document = _fetch_json(url)
    if succeeded:
        try:
            value = parser(document)
        except ValueError as e:
            # A changed response schema is retried later like a transport failure
            logger.warning("Unexpected Wildcat metadata from %s: %s", url, e)
            succeeded = False
    if succeeded:
        if previous:
            # Keep published fields when Wildcat omits the entry or a field.
            value = {field: (value or {}).get(field) or previous.get(field) for field in previous.keys() | (value or {}).keys()}
        entries[key] = {"value": value, "expires_at": (now_ + max_cache_duration).isoformat()}
    else:
        entries[key] = {"value": previous, "expires_at": (now_ + FAILED_REFRESH_RETRY_DELAY).isoformat()}
    return True


def _load_cache(file: Path) -> dict[str, dict]:
    """Load one chain's Wildcat metadata cache.

    Cache corruption is raised as a hard error, because silently replacing the
    cache would erase published descriptions.

    :param file:
        JSON cache file.
    :return:
        Cache with ``markets`` and ``profiles`` sections.
    :raise RuntimeError:
        If the file is not valid JSON.
    """

    if not file.exists() or file.stat().st_size == 0:
        return {"markets": {}, "profiles": {}}
    try:
        with file.open(encoding="utf-8") as cache_input:
            return json.load(cache_input)
    except (JSONDecodeError, UnicodeDecodeError) as e:
        content = file.read_text(encoding="utf-8", errors="replace")
        raise RuntimeError(f"Could not parse Wildcat metadata cache {file}, length {len(content)} content starts with {content[:100]!r}") from e


def fetch_wildcat_market_metadata(
    web3: Web3,
    market_address: HexAddress | str,
    borrower_address: HexAddress | str,
    *,
    now_: datetime.datetime | None = None,
    cache_path: Path | None = None,
    api_base_url: str = DEFAULT_API_BASE_URL,
    max_cache_duration: datetime.timedelta = DEFAULT_CACHE_DURATION,
) -> WildcatMarketMetadata:
    """Fetch a Wildcat market's description and its borrower's profile.

    Reads the chain's cache file under a file lock, refreshes expired entries
    from the Wildcat app API and writes the file back atomically.

    :param web3:
        Web3 connection of the market's chain.
    :param market_address:
        Market address.
    :param borrower_address:
        Market ``borrower()`` address.
    :param now_:
        Override the current naive UTC time for testing.
    :param cache_path:
        Directory containing Wildcat metadata cache files. Defaults to the
        :py:func:`redirect_metadata_cache` directory, then
        :py:data:`DEFAULT_CACHE_PATH`.
    :param api_base_url:
        Wildcat app API base URL.
    :param max_cache_duration:
        How long a successful fetch is used.
    :return:
        Combined metadata. Fields are ``None`` when Wildcat has no value.
    """

    chain_id = web3.eth.chain_id
    now_ = now_ or native_datetime_utc_now()
    market = market_address.lower()
    borrower = borrower_address.lower()

    cache_path = cache_path or _redirected_cache_path or DEFAULT_CACHE_PATH
    cache_path.mkdir(parents=True, exist_ok=True)
    file = _get_cache_file(cache_path, chain_id)
    with wait_other_writers(file):
        cache = _load_cache(file)
        changed = _refresh_entry(entries=cache["markets"], key=market, url=f"{api_base_url}/market-summary/{market}?chainId={chain_id}", parser=_parse_market_summary, now_=now_, max_cache_duration=max_cache_duration)
        changed |= _refresh_entry(entries=cache["profiles"], key=borrower, url=f"{api_base_url}/profiles/{borrower}?chainId={chain_id}", parser=_parse_profile, now_=now_, max_cache_duration=max_cache_duration)
        if changed:
            # Atomic replacement prevents an interrupted scanner from leaving
            # a truncated file that blocks every later refresh.
            with atomic_write(str(file), mode="w", overwrite=True, encoding="utf-8") as cache_output:
                json.dump(cache, cache_output, indent=2)

    market_value = cache["markets"][market]["value"] or {}
    profile_value = cache["profiles"][borrower]["value"] or {}
    return WildcatMarketMetadata(
        market_description=market_value.get("description"),
        borrower_name=profile_value.get("name"),
        borrower_description=profile_value.get("description"),
    )
