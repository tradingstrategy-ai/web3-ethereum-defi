"""ERC-7540 vault support.

- Generic ERC-7540 request/redeem interface
"""

import datetime
import logging

from eth_typing import HexAddress
from web3.contract.contract import ContractFunction
from ..erc_4626.vault import ERC4626Vault


logger = logging.getLogger(__name__)


class ERC7540Vault(ERC4626Vault):
    """ERC-7540 deposit and redeem support."""

    def request_deposit(
        self,
        depositor: HexAddress,
        raw_amount: int,
        check_allowance=True,
        check_balance=True,
    ) -> ContractFunction:
        """Build a deposit transction.

        - Phase 1 of deposit before settlement
        - Used for testing
        - Must be approved() first
        - Uses the vault underlying token (USDC)

        .. note::

            Legacy. Use :py:meth:`get_deposit_manager` instead.

        :param raw_amount:
            Raw amount in underlying token
        """
        assert type(raw_amount) == int
        underlying = self.underlying_token
        existing_balance = underlying.fetch_raw_balance_of(depositor)
        if check_balance:
            assert existing_balance >= raw_amount, f"Cannot deposit {underlying.symbol} by {depositor}. Have: {existing_balance}, asked to deposit: {raw_amount}"
        existing_allowance = underlying.contract.functions.allowance(depositor, self.vault_address).call()
        if check_allowance:
            assert existing_allowance >= raw_amount, f"Cannot deposit {underlying.symbol} by {depositor}. Allowance: {existing_allowance}, asked to deposit: {raw_amount}"
        return self.vault_contract.functions.requestDeposit(
            raw_amount,
            depositor,
            depositor,
        )

    def finalise_deposit(self, depositor: HexAddress, raw_amount: int | None = None) -> ContractFunction:
        """Move shares we received to the user wallet.

        - Phase 2 of deposit after settlement
        """

        if raw_amount is None:
            raw_amount = self.vault_contract.functions.maxDeposit(depositor).call()

        return self.vault_contract.functions.deposit(raw_amount, depositor)

    def request_redeem(self, depositor: HexAddress, raw_amount: int) -> ContractFunction:
        """Build a redeem transction.

        - Phase 1 of redemption, before settlement
        - Used for testing
        - Sets up a redemption request for X shares

        :param raw_amount:
            Raw amount in share token
        """
        assert type(raw_amount) == int, f"Got {raw_amount} {type(raw_amount)}"
        shares = self.share_token
        block_number = self.web3.eth.block_number

        # Check we have shares
        owned_raw_amount = shares.fetch_raw_balance_of(depositor, block_number)
        assert owned_raw_amount >= raw_amount, f"Cannot redeem, has only {owned_raw_amount} shares when {raw_amount} needed"

        human_amount = shares.convert_to_decimals(raw_amount)
        total_shares = self.fetch_total_supply(block_number)
        logger.info("Setting up redemption for %s %s shares out of %s, for %s", human_amount, shares.symbol, total_shares, depositor)
        return self.vault_contract.functions.requestRedeem(
            raw_amount,
            depositor,
            depositor,
        )

    def finalise_redeem(self, depositor: HexAddress, raw_amount: int | None = None) -> ContractFunction:
        """Move redeemed assets to the user wallet.

        - Phase 2 of the redemption
        """

        assert type(depositor) == str, f"Got {depositor} {type(depositor)}"

        if raw_amount is None:
            raw_amount = self.vault_contract.functions.maxRedeem(depositor).call()

        return self.vault_contract.functions.redeem(raw_amount, depositor, depositor)

    def get_deposit_manager(self) -> "eth_defi.lagoon.deposit_redeem.ERC7540DepositManager":
        from eth_defi.lagoon.deposit_redeem import ERC7540DepositManager

        return ERC7540DepositManager(self)

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """ERC-7540 vaults have always a lock up."""
        return None
