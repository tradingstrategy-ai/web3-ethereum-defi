"""Axis StakedUSDx rewards vault support.

Axis's reviewed Ethereum V2 and Plasma V1 deployments accept USDx and issue
sUSDx shares. Ethereum V2 uses an asynchronous request, service and claim
lifecycle. Plasma V1 permits direct ERC-4626 redemption while its cooldown is
zero and otherwise uses a holder-controlled cooldown and unstake lifecycle.
The reader does not advertise a transaction manager until both paths have been
implemented and certified on an Anvil fork.

- Homepage: https://www.axis.to/
- Product documentation: https://docs.axis.to/susdx-the-rewards-vault/susdx
- Contract reference: https://docs.axis.to/reference/staking-contracts
- Ethereum V2 deployment: https://etherscan.io/address/0xEB892628D1E58BC475A6dCB7F5dBC4F591632AA4
- Ethereum V2 implementation: https://etherscan.io/address/0x1D8191c20c06c5628f1a977bc6D6aFe7dD541cf2#code
- Plasma V1 deployment: https://plasmaexplorer.com/address/0x13A099765B34b3aAFedb8698CF7fd418E7730012
"""

import datetime

from eth_typing import BlockIdentifier
from web3.exceptions import Web3Exception

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.axis.constants import AXIS_ETHEREUM_CHAIN_ID, AXIS_PLASMA_CHAIN_ID, AXIS_SHORT_DESCRIPTION
from eth_defi.erc_4626.vault_protocol.axis.tags import STRATEGY_TAGS
from eth_defi.types import Percent
from eth_defi.vault.base import INSTANT_WITHDRAWAL_PERIOD, WithdrawalDelayType, WithdrawalPeriod
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

#: ``cooldownDuration()`` selector shared by both reviewed Axis deployments.
COOLDOWN_DURATION_SELECTOR = bytes.fromhex("35269315")

#: ABI-encoded scalar return size.
EVM_WORD_BYTES = 32


class AxisVault(ERC4626Vault):
    """Axis's USDx staking rewards vault.

    The StakedUSDx vault mints non-rebasing sUSDx shares for deposited USDx.
    Its share price grows as funded rewards vest. Ethereum V2 uses ERC-7540
    requests that are later serviced and claimed. Plasma V1 instead switches
    between direct ERC-4626 redemption and its own cooldown and unstake flow.

    - Product documentation: https://docs.axis.to/susdx-the-rewards-vault/susdx
    - Redemption guide: https://docs.axis.to/susdx-the-rewards-vault/stake-and-unstake
    """

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return the maintained strategy tags for this Axis vault.

        :return:
            Copy of the tag set, or ``None`` when this deployment has not been
            classified.
        """
        return lookup_strategy_tags(STRATEGY_TAGS, self.vault_address)

    @property
    def short_description(self) -> str:
        """Return the static product summary used in scanner output.

        The description is maintained with the address-routed Axis deployment
        constants so an automatic rescan preserves the same listing metadata
        as the historical-cache repair command.

        :return:
            Axis's concise StakedUSDx product description.
        """
        return AXIS_SHORT_DESCRIPTION

    def get_fee_mode(self) -> VaultFeeMode:  # noqa: PLR6301
        """Return the reviewed StakedUSDx contracts' explicit fee mode.

        The reviewed implementations do not deduct management, performance,
        deposit or withdrawal fees. Reward vesting changes the exchange rate;
        it is not itself a fee.

        :return:
            Feeless vault-contract accounting.
        """
        return VaultFeeMode.feeless

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent:  # noqa: PLR6301
        """Return Axis's zero management fee.

        The argument is retained for the common vault-reader interface.

        :param block_identifier:
            Block at which a fee would be read.
        :return:
            ``0.0``, the configured management fee fraction.
        """
        del block_identifier
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent:  # noqa: PLR6301
        """Return Axis's zero performance fee.

        The argument is retained for the common vault-reader interface.

        :param block_identifier:
            Block at which a fee would be read.
        :return:
            ``0.0``, the configured performance fee fraction.
        """
        del block_identifier
        return 0.0

    def fetch_cooldown_duration(self, block_identifier: BlockIdentifier | None = None) -> datetime.timedelta:
        """Read the deployment's configured default redemption cooldown.

        Both reviewed contracts expose ``cooldownDuration()`` even though their
        redemption interfaces differ. Ethereum V2 may assign account-specific
        policies, so its default is not a settlement guarantee.

        :param block_identifier:
            Block to inspect. Defaults to the adapter's configured metadata
            block.
        :return:
            Configured default cooldown.
        """
        block_identifier = self._get_block_identifier() if block_identifier is None else block_identifier
        try:
            result = self.web3.eth.call(
                {
                    "to": self.vault_address,
                    "data": COOLDOWN_DURATION_SELECTOR,
                },
                block_identifier=block_identifier,
            )
        except Web3Exception as error:
            raise ValueError(f"Could not read Axis cooldownDuration() at {block_identifier}") from error
        if len(result) != EVM_WORD_BYTES:
            raise ValueError(f"Axis cooldownDuration() returned {len(result)} bytes at {block_identifier}")
        return datetime.timedelta(seconds=int.from_bytes(result))

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """Return the deployment's current default cooldown.

        :return:
            Default cooldown read from contract state.
        """
        return self.fetch_cooldown_duration()

    def get_withdrawal_period(self) -> WithdrawalPeriod:
        """Describe the deployment-specific redemption timing model.

        Plasma V1 permits direct ERC-4626 redemption while its cooldown is
        zero. With a non-zero cooldown, its holder-controlled unstake becomes
        available after that fixed period. Ethereum V2 additionally requires a
        servicer after the account policy cooldown, so no universal minimum or
        maximum settlement time can be promised.

        :return:
            Current Plasma timing, or an unbounded asynchronous V2 period.
        """
        if self.chain_id == AXIS_PLASMA_CHAIN_ID:
            cooldown = self.fetch_cooldown_duration()
            if cooldown == datetime.timedelta(0):
                return INSTANT_WITHDRAWAL_PERIOD
            return WithdrawalPeriod(cooldown, cooldown, WithdrawalDelayType.delay)
        if self.chain_id == AXIS_ETHEREUM_CHAIN_ID:
            return WithdrawalPeriod(None, None, WithdrawalDelayType.delay)
        raise ValueError(f"Unsupported Axis deployment chain: {self.chain_id}")

    def get_link(self, referral: str | None = None) -> str:  # noqa: PLR6301
        """Return the public Axis application link.

        Axis publishes its user-facing staking interface under its application
        domain. Referral parameters are not part of Axis's documented URL
        format and are therefore ignored.

        :param referral:
            Unsupported referral code.
        :return:
            Axis's public application URL.
        """
        del referral
        return "https://app.axis.to/"
