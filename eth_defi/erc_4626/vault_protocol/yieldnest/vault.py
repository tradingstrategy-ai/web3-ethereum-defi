"""YieldNest vault support."""

import datetime
import logging
from functools import cached_property
from typing import TYPE_CHECKING

from eth_typing import BlockIdentifier, HexAddress
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.vault.deposit_redeem import VaultDepositManagerCapability

if TYPE_CHECKING:
    from eth_defi.erc_4626.vault_protocol.yieldnest.deposit_redeem import YieldNestDepositManager

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

        YieldNest does not charge management fees.

        :return:
            0.0
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current performance fee as a percent.

        YieldNest does not charge performance fees.

        :return:
            0.0
        """
        return 0.0

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

    def get_deposit_manager(self) -> "YieldNestDepositManager":
        """Return the YieldNest manager with a buffer-limited redemption preflight.

        Deposits are synchronous ERC-4626; the redemption path adds a
        ``maxRedeem(owner)`` capacity preflight so an over-buffer redemption is
        refused as a typed ``VaultFlowUnavailable`` (decoded
        ``ExceededMaxRedeem``) rather than a raw ``0xb8b8b59c`` revert.
        The explicit ``ignore_liquidity`` test option is available only through
        the manager's local ``MockYieldNestVault`` settlement driver; it does
        not alter this live vault adapter's advertised capability.

        :return:
            YieldNest deposit/redemption manager.
        """
        from eth_defi.erc_4626.vault_protocol.yieldnest.deposit_redeem import YieldNestDepositManager

        return YieldNestDepositManager(self)

    def can_check_deposit(self) -> bool:
        """YieldNest doesn't support address(0) checks for maxDeposit.

        The contract returns empty data for maxDeposit(address(0)).
        """
        return False

    def is_whitelisted_deposit(self) -> bool:
        """Report the public policy of the supported YieldNest implementation.

        The verified ynRWAx ``BaseVault.deposit`` route checks only the global
        pause state before performing the ERC-4626 deposit. Administrative
        roles govern configuration and pausing, but no depositor role or
        account-admission predicate is present in this implementation.

        :return:
            ``False`` because ynRWAx deposits are permissionless when open.
        """
        return False

    def get_deposit_manager_capability(self) -> VaultDepositManagerCapability:
        """Declare YieldNest's implemented synchronous lifecycle.

        The bundled ABI contains the standard ERC-4626 ``Deposit`` event, so
        the inherited manager can decode the tested deposit receipt without a
        YieldNest-only parser. The specialised manager implements the standard
        synchronous redemption and reads an owner's immediate ``maxRedeem``
        capacity. Capability describes the adapter implementation, not current
        live redemption capacity. For ynRWAx the manager first enforces the product's 15 October 2026
        maturity as a typed ``redemption_not_yet_matured`` preflight instead of
        misreporting the implemented direction as adapter-unsupported.
        The manager's ``ignore_liquidity`` simulation option operates only on
        ``MockYieldNestVault`` in a local Anvil test. It proves GuardV0's
        standard ERC-4626 redemption validation, not a live YieldNest buffer
        or a public redemption capability.

        .. note::

            Trade-executor must use the selected manager's receipt-analysis
            hooks rather than a generic analyser and preserve typed live-state
            redemption refusals.

        :return:
            Synchronous deposit and redemption capability.
        """
        return VaultDepositManagerCapability(
            can_deposit=True,
            can_redeem=True,
            deposit_flow="synchronous",
            redemption_flow="synchronous",
        )
