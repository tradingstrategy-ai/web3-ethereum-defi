"""Offchain profile metadata handling for Enzyme vault listings.

Enzyme Blue publishes manager-entered vault profiles in its application
backend. The app's unauthenticated GraphQL ``vaultProfile`` query currently
exposes the vault's tagline, descriptions and manager contact channels. This
is an undocumented application integration. Keep it isolated here and treat
both its response schema and address-only resolver contract as changeable: the
query has no deployment argument, so Enzyme currently resolves Blue VaultProxy
addresses globally.

The separate Enzyme Onyx architecture has no equivalent public profile reader.
Onyx metadata is editable in its management application, but its scanner rows
therefore retain an explicit unavailable marker until Enzyme publishes a
supported reader.

The production scanner never makes thousands of app requests as a side effect
of scanning a chain. ``scripts/enzyme/migrate-offchain-metadata.py`` fetches
the reviewed Blue metadata and saves it in a durable, multiprocess-safe cache.
Blue adapters read this cache only; when Enzyme has no manager-entered copy,
both description fields remain absent rather than using inferred fallback text.
"""

import json
import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from json import JSONDecodeError
from pathlib import Path
from urllib.parse import urlsplit

from eth_typing import HexAddress
from requests import Session
from requests.adapters import HTTPAdapter
from web3 import Web3

from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.logging_retry import LoggingRetry
from eth_defi.utils import wait_other_writers

logger = logging.getLogger(__name__)

#: Undocumented Enzyme application GraphQL endpoint used by the public vault
#: detail page. The query below is unauthenticated today, but manager updates
#: require an authenticated owner through the app's ``updateVaultProfile``
#: mutation.
ENZYME_APP_GRAPHQL_URL = "https://app.enzyme.finance/api/graphql"

#: Public fields returned by the app's ``vaultProfile`` resolver.
ENZYME_APP_VAULT_PROFILE_FIELDS = (
    "tagline",
    "description",
    "managerDescription",
    "contactInfo",
    "email",
    "telegram",
    "websiteUrl",
    "twitter",
)

#: Current durable cache schema written by the Enzyme metadata migration.
#:
#: Version two adds manager-contact fields sourced from the app GraphQL reader.
ENZYME_METADATA_CACHE_VERSION = 2

#: Versioned durable cache populated by the Enzyme metadata migration.
DEFAULT_ENZYME_METADATA_CACHE_PATH = DEFAULT_CACHE_ROOT / "enzyme" / "vault-metadata.json"

#: Maximum public ``vaultProfile`` aliases accepted by Enzyme's app backend.
ENZYME_APP_MAX_PROFILE_ALIASES = 5

#: Profile hosts normalised to X/Twitter handles.
ENZYME_TWITTER_PROFILE_DOMAINS = frozenset({"x.com", "twitter.com"})

#: Profile hosts normalised to Telegram handles.
ENZYME_TELEGRAM_PROFILE_DOMAINS = frozenset({"t.me", "telegram.me", "telegram.dog"})

#: The exact public catalogue note used when an Onyx manager description is
#: unavailable through a documented public source.
ONYX_PUBLIC_DESCRIPTION_UNAVAILABLE = "Description is not publicly available"

#: Enzyme application network slugs for reviewed Blue and Onyx deployments.
#: Keep these integration-owned values separate from generic display names,
#: which may change independently and are not URL identifiers.
ENZYME_NETWORK_SLUGS: dict[int, str] = {
    1: "ethereum",
    137: "polygon",
    8453: "base",
    42161: "arbitrum",
}


@dataclass(slots=True, frozen=True)
class EnzymeVaultMetadata:
    """App-sourced presentation data for one Enzyme Blue vault."""

    #: Longer product description suitable for a detail view.
    description: str | None = None
    #: One-line product summary suitable for a table.
    short_description: str | None = None
    #: Manager-provided biography, separate from the vault product copy.
    manager_description: str | None = None
    #: Free-form manager contact guidance.
    contact_info: str | None = None
    #: Public manager contact email address.
    contact_email: str | None = None
    #: Public Telegram handle without an assumed URL format.
    telegram: str | None = None
    #: Public X/Twitter handle without an assumed URL format.
    twitter: str | None = None
    #: Public manager or vault website URL.
    website_url: str | None = None
    #: Best source-proven manager identifier for the vault catalogue.
    manager_name: str | None = None


def _normalise_optional_text(value: object) -> str | None:
    """Turn an optional app-profile text field into meaningful listing copy.

    The app omits absent fields, while managers can also save whitespace-only
    values. Treat both cases as missing instead of publishing invented copy.

    :param value: Raw GraphQL field value.
    :return: Stripped non-empty string or ``None``.
    """

    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _normalise_social_handle(value: object, *, domains: frozenset[str]) -> str | None:
    """Return a display-safe social handle without a leading at-sign.

    :param value: Raw X/Twitter or Telegram handle or profile URL from the app.
    :param domains: Accepted lower-case profile hostnames for this field.
    :return: Non-empty handle without a leading ``@``, or ``None``.
    """

    handle = _normalise_optional_text(value)
    if handle is None:
        return None
    try:
        parsed = urlsplit(handle)
        is_url = parsed.hostname is not None or bool(parsed.scheme)
        if parsed.hostname is None and not parsed.scheme:
            parsed = urlsplit(f"https://{handle}")
    except ValueError:
        return None
    hostname = parsed.hostname.lower().removeprefix("www.") if parsed.hostname else None
    if hostname in domains:
        handle = parsed.path.strip("/").split("/", maxsplit=1)[0].removeprefix("@")
        return handle if handle and handle not in {"i", "intent"} else None
    if is_url or (hostname and "." in hostname):
        return None
    return handle.removeprefix("@") or None


def _normalise_website_url(value: object) -> str | None:
    """Return a browser-safe public website URL.

    :param value: Raw app-profile website field.
    :return: Non-empty HTTP(S) URL, adding ``https://`` to a hostname-only value.
    """

    website_url = _normalise_optional_text(value)
    if website_url is None:
        return None
    try:
        parsed = urlsplit(website_url)
        if parsed.hostname is None and not parsed.scheme:
            website_url = f"https://{website_url}"
            parsed = urlsplit(website_url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        return None
    return website_url


def _extract_website_domain(website_url: str | None) -> str | None:
    """Extract a website hostname without guessing a manager display name.

    :param website_url: Public manager or vault website URL.
    :return: Lower-case hostname without ``www.``, or ``None`` for an unusable URL.
    """

    if website_url is None:
        return None
    try:
        parsed = urlsplit(website_url)
        if parsed.hostname is None and not parsed.scheme:
            parsed = urlsplit(f"https://{website_url}")
        return parsed.hostname.lower().removeprefix("www.") if parsed.hostname else None
    except ValueError:
        return None


def _derive_manager_name(
    *,
    twitter: str | None,
    telegram: str | None,
    contact_email: str | None,
    website_url: str | None,
) -> str | None:
    """Choose a source-proven manager identifier from public contacts.

    A social account is a manager-provided identity and is preferable to
    guessing from prose. A non-generic email local part is the next best
    identifier. When no such identity exists, use the configured website
    domain exactly as requested rather than manufacturing a company or person
    name.

    :param twitter: Public X/Twitter handle.
    :param telegram: Public Telegram handle.
    :param contact_email: Public contact email address.
    :param website_url: Public manager or vault website URL.
    :return: Best available manager identifier, or ``None``.
    """

    if twitter:
        return twitter
    if telegram:
        return telegram
    if contact_email and "@" in contact_email:
        local_part, _separator, _domain = contact_email.partition("@")
        if local_part and local_part.casefold() not in {"admin", "contact", "hello", "info", "support", "team"}:
            return local_part
    return _extract_website_domain(website_url)


def _parse_enzyme_app_vault_profile(profile: object) -> EnzymeVaultMetadata:
    """Parse one Enzyme manager profile object.

    :param profile: Value returned by a ``vaultProfile`` GraphQL field.
    :return: Parsed optional listing metadata.
    :raise ValueError: If the profile is neither an object nor ``null``.
    """

    if profile is None:
        return EnzymeVaultMetadata()
    if not isinstance(profile, dict):
        message = "Enzyme app GraphQL vaultProfile must be an object or null"
        raise ValueError(message)

    twitter = _normalise_social_handle(profile.get("twitter"), domains=ENZYME_TWITTER_PROFILE_DOMAINS)
    telegram = _normalise_social_handle(profile.get("telegram"), domains=ENZYME_TELEGRAM_PROFILE_DOMAINS)
    contact_email = _normalise_optional_text(profile.get("email"))
    website_url = _normalise_website_url(profile.get("websiteUrl"))
    return EnzymeVaultMetadata(
        short_description=_normalise_optional_text(profile.get("tagline")),
        description=_normalise_optional_text(profile.get("description")),
        manager_description=_normalise_optional_text(profile.get("managerDescription")),
        contact_info=_normalise_optional_text(profile.get("contactInfo")),
        contact_email=contact_email,
        telegram=telegram,
        twitter=twitter,
        website_url=website_url,
        manager_name=_derive_manager_name(
            twitter=twitter,
            telegram=telegram,
            contact_email=contact_email,
            website_url=website_url,
        ),
    )


def parse_enzyme_app_vault_metadata(payload: object) -> EnzymeVaultMetadata:
    """Parse manager profile data from Enzyme's app GraphQL response.

    ``tagline`` is the compact catalogue description and ``description`` is
    the vault narrative. The app keeps the manager bio and contact channels in
    separate fields, which must never be merged into the strategy text.

    :param payload: Decoded JSON returned by the app's ``vaultProfile`` query.
    :return: Parsed optional listing metadata.
    :raise ValueError: If the response is not a JSON object.
    """

    if not isinstance(payload, dict):
        message = "Enzyme app GraphQL response must be a JSON object"
        raise ValueError(message)
    data = payload.get("data")
    if not isinstance(data, dict):
        message = "Enzyme app GraphQL response must contain a data object"
        raise ValueError(message)
    return _parse_enzyme_app_vault_profile(data.get("vaultProfile"))


def _create_enzyme_app_vault_profiles_query(profile_count: int) -> str:
    """Build a GraphQL alias query for independent public vault profiles.

    GraphQL aliases let the migration make several of the page's regular
    ``vaultProfile`` reads in one HTTP request. The fields and resolver remain
    identical to the application's individual vault-detail query.

    :param profile_count: Number of vault-profile aliases to include.
    :return: A named GraphQL operation accepting one address variable per row.
    :raise ValueError: If the request is empty or exceeds the app alias limit.
    """

    if profile_count < 1:
        message = "profile_count must be positive"
        raise ValueError(message)
    if profile_count > ENZYME_APP_MAX_PROFILE_ALIASES:
        message = f"profile_count must not exceed {ENZYME_APP_MAX_PROFILE_ALIASES}"
        raise ValueError(message)
    variables = ", ".join(f"$vaultAddress{index}: Address!" for index in range(profile_count))
    profile_fields = "\n".join(f"    {field}" for field in ENZYME_APP_VAULT_PROFILE_FIELDS)
    profiles = "\n".join(f"  profile{index}: vaultProfile(vaultAddress: $vaultAddress{index}) {{\n{profile_fields}\n  }}" for index in range(profile_count))
    return f"query VaultProfiles({variables}) {{\n{profiles}\n}}"


def fetch_enzyme_app_vault_metadata_batch(
    session: Session,
    *,
    shares_addresses: list[HexAddress | str],
    timeout: float,
) -> dict[HexAddress, EnzymeVaultMetadata]:
    """Fetch public profile metadata for a bounded serial batch of Blue vaults.

    :param session: Shared retrying HTTP session.
    :param shares_addresses: Canonical Blue VaultProxy addresses to fetch.
    :param timeout: HTTP request timeout in seconds.
    :return: Metadata keyed by each supplied lower-case VaultProxy address.
    :raise ValueError: If the app response has a GraphQL error or invalid schema.
    :raise requests.RequestException: If the request fails.
    """

    query = _create_enzyme_app_vault_profiles_query(len(shares_addresses))
    variables = {f"vaultAddress{index}": Web3.to_checksum_address(address) for index, address in enumerate(shares_addresses)}
    response = session.post(
        ENZYME_APP_GRAPHQL_URL,
        headers={"Content-Type": "application/json"},
        json={"operationName": "VaultProfiles", "variables": variables, "query": query},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        message = "Enzyme app GraphQL response must be a JSON object"
        raise ValueError(message)
    if errors := payload.get("errors"):
        raise ValueError(f"Enzyme app GraphQL returned errors: {errors}")
    data = payload.get("data")
    if not isinstance(data, dict):
        message = "Enzyme app GraphQL response must contain a data object"
        raise ValueError(message)
    profile_keys = [f"profile{index}" for index in range(len(shares_addresses))]
    missing_keys = [key for key in profile_keys if key not in data]
    if missing_keys:
        raise ValueError(f"Enzyme app GraphQL response omitted aliases: {', '.join(missing_keys)}")
    return {HexAddress(address.lower()): _parse_enzyme_app_vault_profile(data[key]) for key, address in zip(profile_keys, shares_addresses, strict=True)}


def create_enzyme_app_session(retries: int = 5, backoff_factor: float = 0.5) -> Session:
    """Create a pooled retrying session for bounded Enzyme metadata collection.

    The migration submits serial app-profile batches. Retry only transient
    network and throttling statuses; other HTTP 4xx and schema errors must
    surface to the operator instead of being disguised as missing metadata.

    :param retries: Maximum retry count for a transient request failure.
    :param backoff_factor: Exponential retry delay factor.
    :return: Thread-shareable configured HTTP session.
    """

    retry_policy = LoggingRetry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset({"POST"}),
        respect_retry_after_header=True,
        logger=logger,
    )
    adapter = HTTPAdapter(max_retries=retry_policy)
    session = Session()
    session.mount("https://", adapter)
    return session


def _metadata_key(chain_id: int, shares_address: HexAddress | str) -> tuple[int, HexAddress]:
    """Build the canonical metadata mapping key.

    :param chain_id: EVM chain id.
    :param shares_address: Blue VaultProxy address.
    :return: ``(chain_id, lower-case address)`` key.
    """

    return chain_id, HexAddress(shares_address.lower())


def load_enzyme_vault_metadata_cache(
    cache_path: Path = DEFAULT_ENZYME_METADATA_CACHE_PATH,
    *,
    minimum_version: int = 1,
) -> dict[tuple[int, HexAddress], EnzymeVaultMetadata]:
    """Load durable app-profile metadata without making an HTTP request.

    A malformed cache is ignored with a warning. Blue adapters then expose no
    offchain copy until a successful metadata migration replaces the cache.

    :param cache_path: Versioned JSON cache written by the migration.
    :param minimum_version: Oldest compatible cache schema to accept. The
        scanner accepts version one to preserve existing descriptions during an
        upgrade; the migration requires the current version to collect contacts.
    :return: Address-indexed Enzyme Blue app-profile metadata.
    """

    if not cache_path.exists():
        return {}
    try:
        with cache_path.open("rt") as inp:
            payload = json.load(inp)
        version = payload.get("version") if isinstance(payload, dict) else None
        if isinstance(version, int) and 1 <= version < minimum_version:
            logger.info(
                "Ignoring Enzyme metadata cache %s at version %s; migration needs version %s",
                cache_path,
                version,
                minimum_version,
            )
            return {}
        if not isinstance(version, int) or not minimum_version <= version <= ENZYME_METADATA_CACHE_VERSION:
            message = f"expected cache version {minimum_version} through {ENZYME_METADATA_CACHE_VERSION} JSON object"
            raise ValueError(message)
        records = payload.get("vaults")
        if not isinstance(records, list):
            message = "vaults must be a JSON list"
            raise ValueError(message)
    except (OSError, JSONDecodeError, ValueError) as error:
        logger.warning("Cannot load Enzyme metadata cache %s: %s", cache_path, error)
        return {}

    metadata: dict[tuple[int, HexAddress], EnzymeVaultMetadata] = {}
    for record in records:
        if not isinstance(record, dict):
            logger.warning("Skipping malformed Enzyme metadata cache record in %s", cache_path)
            continue
        chain_id = record.get("chain_id")
        address = record.get("address")
        if not isinstance(chain_id, int) or not isinstance(address, str):
            logger.warning("Skipping Enzyme metadata cache record without chain_id/address in %s", cache_path)
            continue
        if not Web3.is_address(address):
            logger.warning("Skipping Enzyme metadata cache record with invalid address %r", address)
            continue
        metadata[_metadata_key(chain_id, address)] = EnzymeVaultMetadata(
            short_description=_normalise_optional_text(record.get("short_description")),
            description=_normalise_optional_text(record.get("description")),
            manager_description=_normalise_optional_text(record.get("manager_description")),
            contact_info=_normalise_optional_text(record.get("contact_info")),
            contact_email=_normalise_optional_text(record.get("contact_email")),
            telegram=_normalise_social_handle(record.get("telegram"), domains=ENZYME_TELEGRAM_PROFILE_DOMAINS),
            twitter=_normalise_social_handle(record.get("twitter"), domains=ENZYME_TWITTER_PROFILE_DOMAINS),
            website_url=_normalise_website_url(record.get("website_url")),
            manager_name=_normalise_optional_text(record.get("manager_name")),
        )
    return metadata


def write_enzyme_vault_metadata_cache(
    metadata: Mapping[tuple[int, HexAddress], EnzymeVaultMetadata],
    cache_path: Path = DEFAULT_ENZYME_METADATA_CACHE_PATH,
) -> None:
    """Atomically replace the durable Enzyme metadata cache.

    Cache entries with no text are retained. They document a successful app
    read whose vault has not supplied descriptions, so the Blue adapter can
    expose no text rather than infer a strategy.

    :param metadata: Complete address-indexed cache contents.
    :param cache_path: JSON cache destination.
    :return: None after the cache is safely replaced.
    """

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "chain_id": chain_id,
            "address": address,
            **asdict(item),
        }
        for (chain_id, address), item in sorted(metadata.items(), key=lambda item: (item[0][0], item[0][1]))
    ]
    payload = {"version": ENZYME_METADATA_CACHE_VERSION, "vaults": records}
    temporary_path = cache_path.with_suffix(f"{cache_path.suffix}.tmp")
    with wait_other_writers(cache_path):
        with temporary_path.open("wt") as out:
            json.dump(payload, out, indent=2, sort_keys=True)
            out.write("\n")
        temporary_path.replace(cache_path)


def create_enzyme_vault_link(chain_id: int, shares_address: HexAddress | str) -> str:
    """Create an address-specific Enzyme application URL.

    Blue VaultProxy and Onyx Shares vehicles use the same application route.
    The canonical address selects the vault and the lower-case network query
    selects its deployment, so callers never need to fall back to the generic
    discovery catalogue.

    :param chain_id: EVM chain id of the Enzyme deployment.
    :param shares_address: Blue VaultProxy or Onyx Shares address.
    :return: Direct Enzyme vault-detail URL.
    """

    try:
        network = ENZYME_NETWORK_SLUGS[chain_id]
    except KeyError as error:
        raise ValueError(f"Unsupported Enzyme chain: {chain_id}") from error
    address = Web3.to_checksum_address(shares_address)
    return f"https://app.enzyme.finance/vault/{address}?network={network}"


def load_enzyme_blue_vault_metadata(chain_id: int, vault_proxy_address: HexAddress | str) -> EnzymeVaultMetadata | None:
    """Look up cached Enzyme Blue listing metadata.

    This adapter-facing function deliberately never calls the app backend.
    The metadata migration warms the durable cache in a bounded batch, keeping
    ordinary scanner cycles deterministic and independent of app availability.
    The cache contains successful empty responses, allowing callers to
    distinguish absent manager copy from a transient app failure.

    :param chain_id: EVM chain id of the Blue vault.
    :param vault_proxy_address: Canonical Blue VaultProxy address.
    :return: Cached app-profile metadata, or ``None`` when no successful cache entry exists.
    """

    key = _metadata_key(chain_id, vault_proxy_address)
    return _load_cached_enzyme_vault_metadata().get(key)


def _load_cached_enzyme_vault_metadata() -> dict[tuple[int, HexAddress], EnzymeVaultMetadata]:
    """Load the process-wide read-only metadata cache once.

    :return: Address-indexed cache contents.
    """

    global _cached_enzyme_vault_metadata  # noqa: PLW0603 - Deliberate process-wide immutable cache.
    if _cached_enzyme_vault_metadata is None:
        _cached_enzyme_vault_metadata = load_enzyme_vault_metadata_cache()
    return _cached_enzyme_vault_metadata


#: Process-wide cache for ordinary adapter construction.
_cached_enzyme_vault_metadata: dict[tuple[int, HexAddress], EnzymeVaultMetadata] | None = None
