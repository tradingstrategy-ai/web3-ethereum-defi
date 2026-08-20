"""Read-only pToken vault support."""

from functools import cached_property

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.ptoken.offchain_data import PTokenVaultOffchainData, get_ptoken_vault_offchain_data


class PTokenVault(ERC4626Vault):
    """Read one reviewed pToken vault on Robinhood Chain.

    Currently not yet identified. The pToken issuer and source code have not
    been publicly identified, so this adapter exposes generic ERC-4626 reads
    only and does not certify deposit, redemption or fee behaviour.
    """

    @cached_property
    def ptoken_offchain_data(self) -> PTokenVaultOffchainData | None:
        """Return reviewed, address-scoped product copy.

        :return:
            Local pToken data for a reviewed address, if available.
        """

        return get_ptoken_vault_offchain_data(self.vault_address)

    @property
    def description(self) -> str | None:
        """Return the pToken provenance and product description.

        :return:
            Address-scoped description, if the vault is reviewed.
        """

        return self.ptoken_offchain_data["description"] if self.ptoken_offchain_data else None

    @property
    def short_description(self) -> str | None:
        """Return a concise pToken product description.

        :return:
            Address-scoped short description, if the vault is reviewed.
        """

        return self.ptoken_offchain_data["short_description"] if self.ptoken_offchain_data else None

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:  # noqa: PLR6301
        """Return the management fee when verified.

        :param block_identifier:
            Block at which fee data would be read.
        :return:
            ``None`` because no management fee is verified.
        """

        del block_identifier
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:  # noqa: PLR6301
        """Return the performance fee when verified.

        :param block_identifier:
            Block at which fee data would be read.
        :return:
            ``None`` because no performance fee is verified.
        """

        del block_identifier
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return the public explorer page because no pToken application is known.

        :param referral:
            Unsupported referral code.
        :return:
            Robinhood Chain Blockscout address page.
        """

        del referral
        return f"https://robinhoodchain.blockscout.com/address/{self.vault_address}"
