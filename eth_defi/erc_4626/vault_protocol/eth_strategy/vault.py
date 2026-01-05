"""ETH Strategy vault support."""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class EthStrategyVault(ERC4626Vault):
    """ETH Strategy vault support.

    ETH Strategy is a DeFi treasury protocol that offers leveraged ETH exposure
    without risk of liquidation or volatility decay. The ESPN (ETH Strategy Perpetual Note)
    vault is an ERC-4626 compliant vault that allows users to deposit USDS stablecoins.

    - Protocol takes no fees on deposits or redemptions
    - Withdrawals are disabled by default, exits through LP mechanisms
    - DAO-governed with rage quit functionality

    - `Website <https://www.ethstrat.xyz/>`__
    - `Documentation <https://docs.ethstrat.xyz/>`__
    - `GitHub <https://github.com/dangerousfood/ethstrategy>`__
    - `Twitter <https://x.com/eth_strategy>`__
    - `Audits <https://docs.ethstrat.xyz/references/audits>`__
    - `Example vault <https://etherscan.io/address/0xb250c9e0f7be4cff13f94374c993ac445a1385fe>`__
    """

    def has_custom_fees(self) -> bool:
        """ETH Strategy vaults do not have explicit deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current management fee.

        ETH Strategy protocol takes no fees.

        :return:
            0.0 as there are no fees
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee.

        ETH Strategy protocol takes no fees.

        :return:
            0.0 as there are no fees
        """
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get the estimated lock-up period.

        Withdrawals are disabled by default, exits occur through LP mechanisms.

        :return:
            None as lock-up is not a fixed time period
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link."""
        return "https://www.ethstrat.xyz/"
