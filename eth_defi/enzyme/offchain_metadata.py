"""Offchain descriptions for Enzyme Blue and Onyx vault listings.

Enzyme share-token contracts identify a vault but do not require a manager to
publish a strategy narrative. The scanner must nevertheless export a short and
long description for every factory-confirmed vault. This module therefore has
two layers: address-specific curated metadata, when available, and a neutral
architecture-level fallback for every remaining Blue or Onyx vault.

The fallback describes the investment vehicle only. It does not invent an
investment strategy, performance objective, manager, or eligibility claim.
"""

from dataclasses import dataclass
from typing import Literal

from eth_typing import HexAddress
from web3 import Web3

EnzymeArchitecture = Literal["blue", "onyx"]

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
    """Human-curated presentation data for one Enzyme vault.

    :param description: Longer product description suitable for a detail view.
    :param short_description: One-line product summary suitable for a table.
    :param manager_name: Curator or manager display name, if known.
    """

    description: str | None = None
    short_description: str | None = None
    manager_name: str | None = None


#: Entries are keyed by ``(chain id, lower-case canonical share-token
#: address)``. This is a VaultProxy on Blue and Shares token on Onyx. Both
#: families are discovered dynamically from reviewed protocol events, so this
#: registry is enrichment only and never an allowlist.
ENZYME_VAULT_METADATA: dict[tuple[int, HexAddress], EnzymeVaultMetadata] = {}


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


def create_enzyme_fallback_metadata(architecture: EnzymeArchitecture, vault_name: str) -> EnzymeVaultMetadata:
    """Create complete neutral listing metadata for an uncurated Enzyme vault.

    A share-token name is an identifier, not evidence of a trading strategy.
    The fallback makes that limitation explicit, so public catalogue rows never
    have blank descriptions while avoiding a fabricated strategy narrative.

    :param architecture: Factory-confirmed Enzyme Blue or Onyx architecture.
    :param vault_name: Onchain ERC-20 share-token name.
    :return: Non-empty short and long descriptions for the catalogue.
    """

    display_name = vault_name.strip() or "Unnamed vault"
    if architecture == "blue":
        return EnzymeVaultMetadata(
            short_description="Enzyme Blue tokenised digital-asset investment vehicle.",
            description=(f"{display_name} is an Enzyme Blue tokenised investment vehicle. Investors hold ERC-20 shares while the vault manager controls the investment configuration and portfolio operations. No manager-provided strategy description is available in this catalogue entry."),
        )
    return EnzymeVaultMetadata(
        short_description="Enzyme Onyx tokenised digital-asset investment vehicle.",
        description=(f"{display_name} is an Enzyme Onyx tokenised investment vehicle. Investors hold ERC-20 Shares while the vault manager configures the vehicle's valuation and subscription components. No manager-provided strategy description is available in this catalogue entry."),
    )


def fetch_enzyme_vault_metadata(chain_id: int, shares_address: HexAddress | str) -> EnzymeVaultMetadata | None:
    """Look up optional address-specific Enzyme listing metadata.

    :param chain_id: EVM chain id of the vault.
    :param shares_address: Enzyme Blue VaultProxy or Onyx Shares contract address.
    :return: Curated metadata, or ``None`` when fallback copy applies.
    """

    return ENZYME_VAULT_METADATA.get((chain_id, HexAddress(shares_address.lower())))


def resolve_enzyme_vault_metadata(architecture: EnzymeArchitecture, vault_name: str, override: EnzymeVaultMetadata | None) -> EnzymeVaultMetadata:
    """Merge optional curator copy with complete Enzyme fallback descriptions.

    :param architecture: Factory-confirmed Enzyme Blue or Onyx architecture.
    :param vault_name: Onchain share-token name used in fallback text.
    :param override: Optional address-specific metadata.
    :return: Metadata with non-empty short and long descriptions.
    """

    fallback = create_enzyme_fallback_metadata(architecture, vault_name)
    if override is None:
        return fallback
    return EnzymeVaultMetadata(
        description=override.description or fallback.description,
        short_description=override.short_description or fallback.short_description,
        manager_name=override.manager_name,
    )
