"""Protocol-neutral ERC-7540 asynchronous vault support."""

import datetime
import logging
from typing import TYPE_CHECKING

from eth_typing import HexAddress
from web3.contract.contract import ContractFunction

from eth_defi.erc_4626.vault import ERC4626Vault

if TYPE_CHECKING:
    from eth_defi.erc_7540.deposit_redeem import ERC7540DepositManager
    from eth_defi.vault.deposit_redeem import VaultDepositManagerCapability


logger = logging.getLogger(__name__)


class ERC7540Vault(ERC4626Vault):
    """Protocol-neutral ERC-7540 asynchronous vault support.

    Protocol adapters inherit this class to reuse standard request, claim and
    manager behaviour. Protocol-specific admission rules and settlement
    drivers belong in adapter subclasses.

    See the canonical `ERC-7540 specification
    <https://eips.ethereum.org/EIPS/eip-7540>`__.
    """

    def request_deposit(
        self,
        depositor: HexAddress,
        raw_amount: int,
        check_allowance=True,
        check_balance=True,
    ) -> ContractFunction:
        """Build a deposit transaction.

        This is phase one of the asynchronous ERC-7540 flow. The depositor
        must hold enough underlying tokens and approve the vault unless the
        corresponding checks are disabled.

        .. note::

            Legacy. Use :py:meth:`get_deposit_manager` instead.

        :param depositor:
            Token owner and request controller.
        :param raw_amount:
            Underlying-token amount in raw units.
        :param check_allowance:
            Check that the vault can transfer ``raw_amount`` from the
            depositor.
        :param check_balance:
            Check that the depositor owns at least ``raw_amount``.
        :return:
            Bound ``requestDeposit`` contract function.
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
        """Build the transaction that claims settled deposit shares.

        This is phase two of an asynchronous deposit. The three-argument
        ERC-7540 ``deposit(assets, receiver, controller)`` call uses
        ``depositor`` as both receiver and controller.

        :param depositor:
            Request controller and share receiver.
        :param raw_amount:
            Settled underlying assets to claim. When omitted, use
            ``maxDeposit(depositor)``.
        :return:
            Bound ERC-7540 deposit-claim function.
        """

        if raw_amount is None:
            raw_amount = self.vault_contract.functions.maxDeposit(depositor).call()

        return self.vault_contract.functions.deposit(raw_amount, depositor, depositor)

    def request_redeem(
        self,
        depositor: HexAddress,
        raw_amount: int,
        check_enough_token: bool = True,
    ) -> ContractFunction:
        """Build a redemption-request transaction.

        This is phase one of the asynchronous ERC-7540 redemption flow. The
        depositor acts as both owner and controller.

        :param depositor:
            Share owner and request controller.
        :param raw_amount:
            Vault shares to redeem in raw units.

        :param check_enough_token:
            Whether to verify the depositor's current share balance. Disable
            only when reconstructing an already-broadcast request.
        :return:
            Bound ``requestRedeem`` contract function.
        """
        assert type(raw_amount) == int, f"Got {raw_amount} {type(raw_amount)}"
        shares = self.share_token
        block_number = self.web3.eth.block_number

        # Check we have shares
        if check_enough_token:
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
        """Build the transaction that claims settled redemption assets.

        This is phase two of an asynchronous redemption. The three-argument
        ERC-7540 ``redeem(shares, receiver, controller)`` call uses
        ``depositor`` as both receiver and controller.

        :param depositor:
            Request controller and underlying-token receiver.
        :param raw_amount:
            Settled shares to claim in raw units. When omitted, use
            ``maxRedeem(depositor)``.
        :return:
            Bound ERC-7540 redemption-claim function.
        """

        assert type(depositor) == str, f"Got {depositor} {type(depositor)}"

        if raw_amount is None:
            raw_amount = self.vault_contract.functions.maxRedeem(depositor).call()

        return self.vault_contract.functions.redeem(raw_amount, depositor, depositor)

    def get_deposit_manager(self) -> "ERC7540DepositManager":
        """Create the protocol-neutral ERC-7540 manager.

        Protocol adapters with additional admission or settlement behaviour
        may override this factory with a specialised manager.

        :return:
            Generic ERC-7540 asynchronous deposit manager.
        """
        from eth_defi.erc_7540.deposit_redeem import ERC7540DepositManager

        return ERC7540DepositManager(self)

    def get_deposit_manager_capability(self) -> "VaultDepositManagerCapability":
        """Declare the standard ERC-7540 request-and-claim lifecycle.

        Both directions require a request followed by operator settlement and
        a separate claim transaction.

        :return:
            Two-way asynchronous capability.
        """
        from eth_defi.vault.deposit_redeem import VaultDepositManagerCapability

        return VaultDepositManagerCapability(
            can_deposit=True,
            can_redeem=True,
            deposit_flow="asynchronous",
            redemption_flow="asynchronous",
        )

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Return an unknown operator-controlled ERC-7540 lock-up.

        ERC-7540 standardises request state, but not the operator's settlement
        schedule. A generic adapter therefore cannot provide a duration.

        :return:
            ``None`` because no generic lock-up estimate exists.
        """
        return None
