"""Offchain description handling for Enzyme vault listings.

Enzyme Blue publishes manager-entered vault metadata through its authenticated
`GetVault API <https://sdk.enzyme.finance/api/endpoints/vault/>`__.  In
particular, the response contains a short ``tagline`` and a long
``description``.  The API does not document an equivalent public endpoint for
the separate Enzyme Onyx architecture.  Onyx metadata is editable in its
management application, but its scanner rows therefore retain an explicit
unavailable marker until Enzyme publishes a supported reader.

The production scanner never makes thousands of API requests as a side effect
of scanning a chain.  ``scripts/enzyme/migrate-offchain-metadata.py`` fetches
the reviewed Blue metadata and saves it in a durable, multiprocess-safe cache.
Blue adapters read this cache and fall back only when the official source has
no manager-entered copy. A small in-source registry remains available for
reviewed exceptional Blue metadata that has no API source.
"""

import json
import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from json import JSONDecodeError
from pathlib import Path

from eth_typing import HexAddress
from requests import Session
from requests.adapters import HTTPAdapter
from web3 import Web3

from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.logging_retry import LoggingRetry
from eth_defi.utils import wait_other_writers

logger = logging.getLogger(__name__)

#: Official Enzyme Connect/gRPC JSON endpoint for one Blue vault.
#:
#: See https://sdk.enzyme.finance/api/overview/
ENZYME_API_VAULT_URL = "https://api.enzyme.finance/enzyme.enzyme.v1.EnzymeService/GetVault"

#: Connect JSON deployment enum names supported by Enzyme Blue's API.
#:
#: The four entries match Enzyme Blue's reviewed production deployments. Onyx
#: is intentionally absent: its separately documented architecture does not
#: have a corresponding public API endpoint for its manager-entered metadata.
ENZYME_API_DEPLOYMENTS: dict[int, str] = {
    1: "DEPLOYMENT_ETHEREUM",
    137: "DEPLOYMENT_POLYGON",
    8453: "DEPLOYMENT_BASE",
    42161: "DEPLOYMENT_ARBITRUM",
}

#: Versioned durable cache populated by the Enzyme metadata migration.
DEFAULT_ENZYME_METADATA_CACHE_PATH = DEFAULT_CACHE_ROOT / "enzyme" / "vault-metadata.json"

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
    """Human-curated or API-sourced presentation data for one Enzyme vault.

    :param description: Longer product description suitable for a detail view.
    :param short_description: One-line product summary suitable for a table.
    :param manager_name: Curator or manager display name, if known.
    """

    description: str | None = None
    short_description: str | None = None
    manager_name: str | None = None


#: Entries are keyed by ``(chain id, lower-case canonical Blue VaultProxy
#: address)``. Blue vaults are discovered dynamically from reviewed protocol
#: events, so this registry is enrichment only and never an allowlist.
#:
#: The official Blue API cache takes precedence over this registry only when it
#: contains manager-entered copy. Keep this registry for reviewed exceptional
#: Blue entries that cannot be fetched from a documented API.
ENZYME_BLUE_VAULT_METADATA: dict[tuple[int, HexAddress], EnzymeVaultMetadata] = {}


def _normalise_optional_text(value: object) -> str | None:
    """Turn an optional API text field into meaningful listing copy.

    Connect JSON omits absent protobuf scalar fields, while managers can also
    save whitespace-only values. Treat both cases as missing instead of
    replacing useful fallback copy with blank text.

    :param value: Raw JSON field value.
    :return: Stripped non-empty string or ``None``.
    """

    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def parse_enzyme_api_vault_metadata(payload: object) -> EnzymeVaultMetadata:
    """Parse manager-entered descriptions from an Enzyme ``GetVault`` reply.

    ``tagline`` is intentionally the compact catalogue description and
    ``description`` is the product-detail narrative. ``managerDescription``
    is about the manager rather than the vault and is therefore not merged into
    the vault strategy text.

    :param payload: Decoded JSON returned by the official ``GetVault`` API.
    :return: Parsed optional listing metadata.
    :raise ValueError: If the response is not a JSON object.
    """

    if not isinstance(payload, dict):
        message = "Enzyme GetVault API response must be a JSON object"
        raise ValueError(message)
    return EnzymeVaultMetadata(
        short_description=_normalise_optional_text(payload.get("tagline")),
        description=_normalise_optional_text(payload.get("description")),
    )


def fetch_enzyme_api_vault_metadata(
    session: Session,
    *,
    chain_id: int,
    shares_address: HexAddress | str,
    api_token: str,
    timeout: float,
) -> EnzymeVaultMetadata:
    """Fetch manager-entered metadata for one Enzyme Blue VaultProxy.

    The official API combines Enzyme-managed offchain descriptions with its
    indexed Blue vault data. It requires an API token generated in the Enzyme
    application. A caller may receive an empty :class:`EnzymeVaultMetadata`
    when a valid vault has no manager-entered tagline or description; this is
    distinct from an HTTP failure, which raises and must not erase cached copy.

    :param session: Shared retrying HTTP session.
    :param chain_id: Reviewed Enzyme Blue production chain id.
    :param shares_address: Canonical Blue VaultProxy address.
    :param api_token: Enzyme API bearer token.
    :param timeout: HTTP request timeout in seconds.
    :return: Optional API-sourced short and long description fields.
    :raise ValueError: If the chain is unsupported by Enzyme's Blue API.
    :raise requests.RequestException: If the request fails.
    """

    try:
        deployment = ENZYME_API_DEPLOYMENTS[chain_id]
    except KeyError as error:
        raise ValueError(f"Unsupported Enzyme Blue API chain {chain_id}") from error

    response = session.post(
        ENZYME_API_VAULT_URL,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
        },
        json={
            "deployment": deployment,
            "address": Web3.to_checksum_address(shares_address),
            "currency": "CURRENCY_USD",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_enzyme_api_vault_metadata(response.json())


def create_enzyme_api_session(max_workers: int, retries: int = 5, backoff_factor: float = 0.5) -> Session:
    """Create a pooled retrying session for bounded Enzyme metadata collection.

    The migration performs independent authenticated requests. Retry only
    transient API and throttling statuses; HTTP 401 and schema errors must
    surface to the operator instead of being disguised as missing metadata.

    :param max_workers: Maximum concurrent requests and pooled connections.
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
    adapter = HTTPAdapter(
        max_retries=retry_policy,
        pool_connections=max_workers,
        pool_maxsize=max_workers,
        pool_block=True,
    )
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


def load_enzyme_vault_metadata_cache(cache_path: Path = DEFAULT_ENZYME_METADATA_CACHE_PATH) -> dict[tuple[int, HexAddress], EnzymeVaultMetadata]:
    """Load durable API metadata without making an HTTP request.

    A malformed cache is ignored with a warning. Blue adapters then use generic
    fallback copy until a successful authenticated migration replaces the cache.

    :param cache_path: Versioned JSON cache written by the migration.
    :return: Address-indexed official Enzyme Blue metadata.
    """

    if not cache_path.exists():
        return {}
    try:
        with cache_path.open("rt") as inp:
            payload = json.load(inp)
        if not isinstance(payload, dict) or payload.get("version") != 1:
            message = "expected version 1 JSON object"
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
            manager_name=_normalise_optional_text(record.get("manager_name")),
        )
    return metadata


def write_enzyme_vault_metadata_cache(
    metadata: Mapping[tuple[int, HexAddress], EnzymeVaultMetadata],
    cache_path: Path = DEFAULT_ENZYME_METADATA_CACHE_PATH,
) -> None:
    """Atomically replace the durable Enzyme metadata cache.

    Cache entries with no text are retained. They document a successful API
    read whose vault has not supplied descriptions, while allowing the Blue
    adapter to select generic fallback copy.

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
    payload = {"version": 1, "vaults": records}
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


def create_enzyme_blue_fallback_metadata(vault_name: str) -> EnzymeVaultMetadata:
    """Create safe listing metadata for an uncurated Enzyme Blue vault.

    A share-token name is an identifier, not evidence of a strategy. This
    generic copy applies only when the official Blue API has no manager text.

    :param vault_name: Onchain Blue VaultProxy ERC-20 name.
    :return: Complete generic Blue listing copy.
    """

    display_name = vault_name.strip() or "Unnamed vault"
    return EnzymeVaultMetadata(
        short_description="Enzyme Blue tokenised digital-asset investment vehicle.",
        description=(f"{display_name} is an Enzyme Blue tokenised investment vehicle. Investors hold ERC-20 shares while the vault manager controls the investment configuration and portfolio operations. No manager-provided strategy description is available in this catalogue entry."),
    )


def load_enzyme_blue_vault_metadata(chain_id: int, vault_proxy_address: HexAddress | str) -> EnzymeVaultMetadata | None:
    """Look up cached or manually curated Enzyme Blue listing metadata.

    This adapter-facing function deliberately never calls the authenticated API.
    The metadata migration warms the durable cache in a bounded batch, keeping
    ordinary scanner cycles deterministic and independent of API credentials.
    Manually reviewed metadata wins over an API entry only when it contains a
    field not present in the API response.

    :param chain_id: EVM chain id of the Blue vault.
    :param vault_proxy_address: Canonical Blue VaultProxy address.
    :return: Cached or curated metadata, or ``None`` when fallback copy applies.
    """

    key = _metadata_key(chain_id, vault_proxy_address)
    curated = ENZYME_BLUE_VAULT_METADATA.get(key)
    cached = _load_cached_enzyme_vault_metadata().get(key)
    if curated is None:
        return cached
    if cached is None:
        return curated
    return EnzymeVaultMetadata(
        description=curated.description or cached.description,
        short_description=curated.short_description or cached.short_description,
        manager_name=curated.manager_name or cached.manager_name,
    )


def resolve_enzyme_blue_vault_metadata(vault_name: str, override: EnzymeVaultMetadata | None) -> EnzymeVaultMetadata:
    """Merge optional source metadata with generic Enzyme Blue fallback copy.

    :param vault_name: Onchain Blue VaultProxy name used in fallback text.
    :param override: Optional official API or curator metadata.
    :return: Complete Blue listing metadata.
    """

    fallback = create_enzyme_blue_fallback_metadata(vault_name)
    if override is None:
        return fallback
    return EnzymeVaultMetadata(
        description=override.description or fallback.description,
        short_description=override.short_description or fallback.short_description,
        manager_name=override.manager_name,
    )


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
