"""Optional offchain metadata for Enzyme Blue and Onyx vault listings.

The Onyx ``Shares`` contract exposes a vault name and symbol, but not a
strategy description or curator display name. This small curated registry adds
listing context without making the scanner depend on the Enzyme web application
or an indexer API.
"""

from dataclasses import dataclass

from eth_typing import HexAddress


@dataclass(slots=True, frozen=True)
class EnzymeVaultMetadata:
    """Human-curated presentation data for one Enzyme vault.

    :param description:
        Longer strategy description suitable for a vault detail view.
    :param short_description:
        One-line strategy summary suitable for a table.
    :param manager_name:
        Curator or manager display name, if it differs from the vault name.
    """

    description: str | None = None
    short_description: str | None = None
    manager_name: str | None = None


#: Entries are keyed by ``(chain id, lower-case canonical share-token
#: address)``. This is a VaultProxy on Blue and Shares token on Onyx. Both
#: families are discovered dynamically from reviewed protocol events, so this
#: registry is enrichment only and never an allowlist.
ENZYME_VAULT_METADATA: dict[tuple[int, HexAddress], EnzymeVaultMetadata] = {}


def fetch_enzyme_vault_metadata(chain_id: int, shares_address: HexAddress | str) -> EnzymeVaultMetadata | None:
    """Look up optional Enzyme vault presentation metadata.

    :param chain_id:
        EVM chain id of the vault.
    :param shares_address:
        Enzyme Blue VaultProxy or Onyx Shares contract address.
    :return:
        Curated metadata, or ``None`` when the vault has no local override.
    """

    return ENZYME_VAULT_METADATA.get((chain_id, HexAddress(shares_address.lower())))
