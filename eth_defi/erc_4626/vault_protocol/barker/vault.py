"""Barker H1 vault support.

`Barker <https://barker.money/>`__ provides stablecoin-yield discovery and
routing. Its reviewed H1 vault deployment on HyperEVM is a USDC-denominated,
upgradeable ERC-4626 vault. The proxy implementation is not verified on
HyperEVM Scan, so this module deliberately supports only that reviewed address
through :data:`eth_defi.erc_4626.classification.HARDCODED_PROTOCOLS`.

H1 exposes an epoch-driven deposit and redemption lifecycle. Although its
feature probes resemble ERC-7540 and ERC-7575 interfaces, its request methods
have not been sufficiently reviewed to certify a generic transaction adapter.
This adapter consequently supports read-only ERC-4626 metadata and historical
reads, but advertises no deposit-manager capability.

- Homepage: https://barker.money/
- App: https://app.barker.money/
- H1 vault: https://hyperevmscan.io/address/0x54251e24e7e5dfc66c02ea02f41bcb2419380bad
"""

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.vault.deposit_redeem import VaultDepositManagerCapability

#: Reviewed Barker H1 USDC vault deployment on HyperEVM.
#:
#: The address is held in the classifier rather than inferred from an
#: unverified implementation. See the explorer link in this module docstring.
BARKER_H1_VAULT_ADDRESS = "0x54251e24e7e5dfc66c02ea02f41bcb2419380bad"


class BarkerVault(ERC4626Vault):
    """Read-only adapter for the reviewed Barker H1 vault.

    Barker H1 accepts deposits and redemptions through an operator-controlled
    epoch process. The published contract surface does not establish that its
    request and claim arguments match the generic ERC-7540 manager, so callers
    must use Barker's application for transactions until the full lifecycle is
    reviewed and certified.
    """

    def _assert_reviewed_deployment(self) -> None:
        """Ensure this adapter remains restricted to the reviewed H1 address.

        Barker's unverified implementation cannot safely be used to infer a
        protocol-wide compatibility promise for any future deployments.

        :raise NotImplementedError:
            If a caller manually applies this adapter to another address.
        """

        if self.vault_address.lower() != BARKER_H1_VAULT_ADDRESS:
            raise NotImplementedError(f"Barker adapter supports only {BARKER_H1_VAULT_ADDRESS}")

    def get_deposit_manager_capability(self) -> VaultDepositManagerCapability | None:
        """Report that Barker's epoch flow has no certified transaction adapter.

        The implementation intentionally avoids constructing the inherited
        synchronous ERC-4626 manager: that manager cannot represent H1's
        request, settlement and claim lifecycle.

        :return:
            ``None`` until the complete Barker flow has focused coverage.
        """

        self._assert_reviewed_deployment()
        return None

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Return an unknown management fee for the selected block.

        Barker has not published a fee schedule for the reviewed H1 deployment
        and the unverified implementation has no reviewed fee accessor.

        :param block_identifier:
            Block at which the fee would apply.

        :return:
            ``None`` because the management fee is not known.
        """

        self._assert_reviewed_deployment()
        del block_identifier
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Return an unknown performance fee for the selected block.

        Barker has not published a fee schedule for the reviewed H1 deployment
        and the unverified implementation has no reviewed fee accessor.

        :param block_identifier:
            Block at which the fee would apply.

        :return:
            ``None`` because the performance fee is not known.
        """

        self._assert_reviewed_deployment()
        del block_identifier
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return Barker's application URL for this vault.

        :param referral:
            Unused because Barker does not publish a reviewed referral URL for
            the H1 vault.

        :return:
            Barker's public application URL.
        """

        self._assert_reviewed_deployment()
        del referral
        return "https://app.barker.money/"
