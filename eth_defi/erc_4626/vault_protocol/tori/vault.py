"""Tori Finance's native strUSD staking vault.

strUSD is an ERC-4626 share over trUSD. Rewards vest into the exchange rate,
and withdrawals require a cooldown followed by a separate claim when the
cooldown is enabled. This reader does not certify a transaction manager.

The only protocol-specific read uses the canonical ``cooldownDuration()``
selector. Its authoritative interface and implementation are published in
`Tori's contract source <https://github.com/Tori-Finance/tori-contracts/blob/main/contracts/core/StakedTrUSD.sol>`__.
See also the `staking terms <https://docs.tori.finance/products/strusd>`__
and `verified deployment <https://etherscan.io/address/0x280839980a7eD0D7717F64125fE241012E5F5815#code>`__.
"""

import datetime

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.tori.tags import STRATEGY_TAGS
from eth_defi.types import Percent
from eth_defi.vault.base import INSTANT_WITHDRAWAL_PERIOD, WithdrawalDelayType, WithdrawalPeriod, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

#: Canonical selector for ``cooldownDuration() -> uint24``.
COOLDOWN_DURATION_SELECTOR = bytes.fromhex("35269315")

#: Byte length of an ABI-encoded integer result.
EVM_WORD_BYTES = 32


class ToriVault(ERC4626Vault):
    """Read Tori's native, protocol-operated strUSD vault.

    Fees retained from gross trading income are not exposed as a numerical
    performance-fee rate. Keep that component unknown rather than treating
    free staking and unstaking as proof that all income reaches holders.
    """

    @property
    def short_description(self) -> str:
        return normalise_vault_short_description("Stake Tori trUSD to receive strUSD and earn variable rewards from money markets and trades that seek to profit from price differences.")

    @property
    def description(self) -> str:
        return normalise_vault_description(
            "[Tori Finance](https://tori.finance/) accepts trUSD and issues strUSD shares whose value reflects rewards from its [money-market and trading strategies](https://docs.tori.finance/strategy/overview). The strategies combine short-term lending instruments with offsetting purchases and sales that seek to earn pricing differences while limiting market exposure. Tori operates this staking vault; RockawayX curates the separate Upshift ecosystem vault. As at 29 October 2026, the [staking terms](https://docs.tori.finance/products/strusd) describe a seven-day withdrawal cooldown followed by a claim for trUSD; the operator can change this delay. Staking and unstaking have no separate charge, but rewards are paid after Tori retains part of trading income at an unspecified rate. [Backing assets](https://docs.tori.finance/solution/backing) include positions held with external custodians; trading losses, custodian failures and contract faults can reduce value or delay access to funds."
        )

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return the maintained tags for this exact vault.

        Address lookup returns a copy, preserving the maintained table when
        callers modify the result.

        :return: Strategy tags, or ``None`` for an unmapped address.
        """
        return lookup_strategy_tags(STRATEGY_TAGS, self.vault_address)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent:  # noqa: PLR6301
        """Return the zero charge on the staked token amount.

        Tori's staking terms distinguish this from the share of trading income
        retained before rewards are distributed.

        :param block_identifier: Block retained for the common fee interface.
        :return: Zero management fee as a fraction.
        """
        del block_identifier
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:  # noqa: PLR6301
        """Keep Tori's unspecified retained income share unknown.

        The staking terms publish net rewards but no numerical rate for the
        income retained by the protocol.

        :param block_identifier: Block retained for the common fee interface.
        :return: ``None`` because the retained rate is not disclosed.
        """
        del block_identifier
        return None

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> Percent:  # noqa: PLR6301
        """Return the zero staking charge.

        This concerns trUSD staking, separate from acquiring or redeeming
        trUSD through Tori's minting service.

        :param block_identifier: Block retained for the common fee interface.
        :return: Zero deposit fee as a fraction.
        """
        del block_identifier
        return 0.0

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> Percent:  # noqa: PLR6301
        """Return the zero unstaking charge.

        A cooldown still applies; the absence of a charge does not imply
        immediate redemption or free conversion of trUSD into USDC.

        :param block_identifier: Block retained for the common fee interface.
        :return: Zero withdrawal fee as a fraction.
        """
        del block_identifier
        return 0.0

    def fetch_cooldown_duration(self, block_identifier: BlockIdentifier | None = None) -> datetime.timedelta:
        """Read the configured cooldown from the staking contract.

        See the implementation linked in the module documentation. Reverts,
        malformed responses and provider failures propagate to the caller;
        the documented seven days is not used as a substitute for a read.

        :param block_identifier: Block to read; defaults to the metadata block.
        :return: Current cooldown duration, including zero when disabled.
        :raises ValueError: If the response is not an ABI-encoded uint24.
        """
        raw = self.web3.eth.call(
            {"to": self.vault_address, "data": COOLDOWN_DURATION_SELECTOR},
            block_identifier=block_identifier if block_identifier is not None else self._get_block_identifier(),
        )
        if len(raw) != EVM_WORD_BYTES or int.from_bytes(raw, "big") >= 2**24:
            raise ValueError(f"Invalid cooldownDuration response for {self.vault_address}")
        return datetime.timedelta(seconds=int.from_bytes(raw, "big"))

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """Read the current contract-enforced cooldown.

        Use the same live value as withdrawal metadata so scanner consumers
        do not receive a stale hardcoded estimate.

        :return: Configured cooldown duration.
        """
        return self.fetch_cooldown_duration()

    def get_withdrawal_period(self) -> WithdrawalPeriod:
        """Describe the current cooldown or immediate withdrawal rule.

        The contract permits direct redemption when the cooldown is zero.
        These timing bounds do not guarantee liquidity or wallet eligibility.

        :return: Exact delay bounds, or the explicit instant-withdrawal value.
        """
        period = self.fetch_cooldown_duration()
        if not period:
            return INSTANT_WITHDRAWAL_PERIOD
        return WithdrawalPeriod(period, period, WithdrawalDelayType.delay)

    def get_link(self, referral: str | None = None) -> str:  # noqa: PLR6301
        """Return Tori's official staking application.

        The application provides staking, cooldown requests and claims.

        :param referral: Unused optional referral identifier.
        :return: Official application URL.
        """
        del referral
        return "https://app.tori.finance/"
