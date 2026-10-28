"""Arcadia Finance ERC-4626 lending tranche descriptions.

The `official pools API <https://api.arcadia.finance/redoc>`__ provides tranche
identities and optional narrative. When unavailable, the adapter explains
the strategy using onchain token and vault metadata.
"""

from functools import cached_property

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.arcadia.description import ArcadiaVaultDescriptions, resolve_arcadia_descriptions
from eth_defi.erc_4626.vault_protocol.arcadia.offchain_metadata import fetch_arcadia_vault_metadata


class ArcadiaVault(ERC4626Vault):
    """Arcadia lending tranche with provider or generated narrative copy.

    The existing protocol feature selects this metadata-only adapter. All
    financial and transaction behaviour remains inherited from ERC-4626.
    """

    @cached_property
    def arcadia_descriptions(self) -> ArcadiaVaultDescriptions:
        """Resolve both descriptions from one shared metadata lookup.

        Description generation also works when the API is unavailable or the
        tranche is absent from its catalogue.

        :return: Provider narrative or deterministic fallback descriptions.
        """
        metadata = fetch_arcadia_vault_metadata(self.chain_id, self.vault_address)
        token = self.denomination_token
        return resolve_arcadia_descriptions(
            chain_id=self.chain_id,
            vault_name=self.name,
            asset_symbol=token.symbol if token else None,
            metadata=metadata,
        )

    @property
    def description(self) -> str:
        """Full lending-strategy narrative for the existing scanner export."""
        return self.arcadia_descriptions.description

    @property
    def short_description(self) -> str:
        """Compact lending-strategy summary for vault listings."""
        return self.arcadia_descriptions.short_description
