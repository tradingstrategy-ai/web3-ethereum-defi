"""YieldNest vault support."""

import datetime
import logging
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


#: ynRWAx vault address on Ethereum
#:
#: This vault has a fixed maturity date of 15 Oct 2026.
YNRWAX_VAULT_ADDRESS: HexAddress = "0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8"

#: ynRWAx fixed maturity date
YNRWAX_MATURITY_DATE = datetime.datetime(2026, 10, 15)


class YieldNestVault(ERC4626Vault):
    """YieldNest vault support.

    YieldNest offers automated liquid restaking with AI-enhanced strategy optimisation.

    - Homepage: https://www.yieldnest.finance
    - Docs: https://docs.yieldnest.finance
    - Github: https://github.com/yieldnest
    - Example vault (ynRWAx): https://etherscan.io/address/0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8#readProxyContract
    - Implementation: https://etherscan.io/address/0xc1C5B18774d0282949331b719b5EA4A21CbC62C8#code
    - Fees: Withdrawal fees are dynamically calculated based on buffer availability, see baseWithdrawalFee() function
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get YieldNest vault implementation contract."""
        return get_deployed_contract(
            self.web3,
            fname="yieldnest/Vault.json",
            address=self.vault_address,
        )

    def get_withdrawal_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current withdrawal fee as a percent.

        YieldNest uses dynamic withdrawal fees based on buffer availability.

        :return:
            0.01 = 1%
        """
        try:
            # baseWithdrawalFee returns uint64 in basis points (10000 = 100%)
            fee_bps = self.vault_contract.functions.baseWithdrawalFee().call(block_identifier=block_identifier)
            return fee_bps / 10_000
        except Exception as e:
            logger.warning("Could not read withdrawal fee for %s: %s", self.vault_address, e)
            return None

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current management fee as a percent.

        YieldNest does not appear to have explicit management fees documented.

        :return:
            None if not available
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        YieldNest does not appear to have explicit performance fees documented.

        :return:
            None if not available
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """YieldNest vaults support instant withdrawals from buffer or queue-based withdrawals.

        The lock-up depends on buffer availability and queue position.

        For ynRWAx vault, there is a fixed maturity date of 15 Oct 2026.
        After this date, returns None.

        :return:
            Timedelta until maturity date for ynRWAx vault, None otherwise
        """
        if self.vault_address.lower() == YNRWAX_VAULT_ADDRESS:
            now = native_datetime_utc_now()
            if now < YNRWAX_MATURITY_DATE:
                return YNRWAX_MATURITY_DATE - now
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get the link to the vault page.

        :return:
            Link to YieldNest homepage as individual vault pages are not available
        """
        return "https://app.yieldnest.finance/"
