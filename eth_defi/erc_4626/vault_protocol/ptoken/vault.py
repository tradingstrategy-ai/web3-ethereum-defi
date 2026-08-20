"""Read-only pToken vault support."""

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

#: Product and provenance copy shared by the two reviewed pToken vaults.
UNKNOWN_ISSUER_DESCRIPTION = "Currently not yet identified. These USDG-denominated Robinhood Chain pTokens share a factory, upgradeable beacon and unlabelled manager address. Their issuer, source repository, product terms and public deployment registry have not been identified. The contracts use Arcus's published Paxos USDG deposit proxy; this shared funding integration does not by itself identify the issuer. Their leverage maintenance, liquidity, redemption timetable and complete fee terms have not been independently verified."

#: Listing-friendly provenance copy shared by the reviewed vaults.
UNKNOWN_ISSUER_SHORT_DESCRIPTION = "Currently not yet identified issuer of reviewed USDG-denominated pTokens."


class PTokenVault(ERC4626Vault):
    """Read one reviewed pToken vault on Robinhood Chain.

    The adapter is address-scoped because the issuer and source code of this
    contract family have not been identified. It exposes generic ERC-4626 reads
    only and does not certify deposit, redemption or fee behaviour. See the
    `published Arcus deployment registry <https://github.com/arcus-xyz/rootchain-contracts-abis/blob/main/deployments.json>`__
    for the shared Paxos USDG deposit proxy.
    """

    @property
    def description(self) -> str | None:
        """Return the pToken provenance and product limitations.

        :return:
            Shared copy for an address-scoped reviewed pToken vault.
        """

        return UNKNOWN_ISSUER_DESCRIPTION

    @property
    def short_description(self) -> str | None:
        """Return a concise pToken provenance description.

        :return:
            Shared copy for an address-scoped reviewed pToken vault.
        """

        return UNKNOWN_ISSUER_SHORT_DESCRIPTION

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
