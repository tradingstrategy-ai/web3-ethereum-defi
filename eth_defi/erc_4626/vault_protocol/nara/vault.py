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

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.nara.deposit_redeem import NaraDepositManager
from eth_defi.vault.deposit_redeem import VaultDepositManagerCapability

#: NaraUSD+ methods outside the standard ERC-4626 surface.
#:
#: ABI recovered from https://app.nara.io/swap and covered by the fork test.
NARAUSD_PLUS_ABI = [
    {
        "inputs": [],
        "name": "cooldownDuration",
        "outputs": [{"internalType": "uint24", "name": "", "type": "uint24"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "shares", "type": "uint256"}],
        "name": "cooldownShares",
        "outputs": [{"internalType": "uint256", "name": "assets", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "", "type": "address"}],
        "name": "cooldowns",
        "outputs": [
            {"internalType": "uint104", "name": "cooldownEnd", "type": "uint104"},
            {"internalType": "uint152", "name": "sharesAmount", "type": "uint152"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "receiver", "type": "address"}],
        "name": "unstake",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


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
            Contract bound to the vault address and cooldown ABI.
        """
        return self.web3.eth.contract(address=self.vault_address, abi=NARAUSD_PLUS_ABI)

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
