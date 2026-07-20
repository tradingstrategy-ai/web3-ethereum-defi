# ruff: noqa: PLR6301
"""NaraUSD+ vault support.

NaraUSD+ is Nara's yield-accruing staking token for NaraUSD. Deposits mint
vault shares immediately, while redemptions require the holder to start a
cooldown and later claim NaraUSD. See the `Nara application
<https://app.nara.io/swap>`__ and the `NaraUSD documentation
<https://docs.nara.io/nara-protocol/narausd>`__.
"""

import datetime
from functools import cached_property

from eth_typing import BlockIdentifier
from web3.contract import Contract

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.nara.deposit_redeem import NaraDepositManager
from eth_defi.vault.deposit_redeem import VaultDepositManagerCapability


class NaraVault(ERC4626Vault):
    """NaraUSD+ staking vault with a configurable owner cooldown.

    NaraUSD+ holders receive an appreciating share token. The protocol's public
    application reports that yield originates from short-term payment-financing
    assets. The contract does not expose a management or performance fee rate.
    """

    @cached_property
    def narausd_plus_contract(self) -> Contract:
        """Return the NaraUSD+-specific contract interface.

        :return:
            Contract bound to the vault address and NaraUSD+ interface.
        """
        return get_deployed_erc_4626_contract(
            self.web3,
            self.vault_address,
            abi_fname="nara/NaraUSDPlus.json",
        )

    def has_custom_fees(self) -> bool:
        """Return whether the NaraUSD+ contract exposes entry or exit fees.

        :return:
            ``False``; no such fee accessor exists in the reviewed surface.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Return the NaraUSD+ management fee when published on-chain.

        :param block_identifier:
            Block number or ``"latest"``; retained for the common vault API.
        :return:
            ``None`` because the reviewed contract does not publish this fee.
        """
        del block_identifier
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Return the NaraUSD+ performance fee when published on-chain.

        :param block_identifier:
            Block number or ``"latest"``; retained for the common vault API.
        :return:
            ``None`` because the reviewed contract does not publish this fee.
        """
        del block_identifier
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Read NaraUSD+'s live cooldown duration.

        :return:
            Current cooldown duration, currently seven days on Ethereum.
        """
        duration = int(self.narausd_plus_contract.functions.cooldownDuration().call())
        return datetime.timedelta(seconds=duration)

    def get_deposit_manager(self) -> "NaraDepositManager":
        """Create the NaraUSD+ synchronous-deposit, asynchronous-redeem manager.

        :return:
            Protocol-specific NaraUSD+ manager.
        """
        return NaraDepositManager(self)

    def get_deposit_manager_capability(self) -> VaultDepositManagerCapability:
        """Describe NaraUSD+'s complete public request lifecycle.

        :return:
            Synchronous deposits and asynchronous cooldown redemptions.
        """
        return VaultDepositManagerCapability(
            can_deposit=True,
            can_redeem=True,
            deposit_flow="synchronous",
            redemption_flow="asynchronous",
        )

    def can_check_redeem(self) -> bool:
        """Disable generic ERC-4626 ``maxRedeem`` availability checks.

        :return:
            ``False`` because NaraUSD+ redemption is controlled by cooldown state.
        """
        return False

    def get_link(self, referral: str | None = None) -> str:
        """Return Nara's public staking interface.

        :param referral:
            Unsupported referral code.
        :return:
            Nara application URL.
        """
        del referral
        return "https://app.nara.io/swap"
