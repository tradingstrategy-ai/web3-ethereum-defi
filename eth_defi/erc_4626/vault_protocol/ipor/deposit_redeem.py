"""Caller-aware deposit and redemption flow for IPOR Fusion vaults."""

# ruff: noqa: FBT001, FBT002, PLR0917

from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from eth_typing import HexAddress
from hexbytes import HexBytes

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626DepositRequest, ERC4626RedemptionRequest
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

if TYPE_CHECKING:
    from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault


class IPORDepositManager(ERC4626DepositManager):
    """IPOR Fusion manager with OpenZeppelin AccessManager pre-flights.

    IPOR uses standard ERC-4626 transaction functions after admission.  Its
    ``AccessManager`` can nevertheless reject a transaction based on its
    caller, target and selector, or require a scheduling delay.  This manager
    converts those predictable failures to :class:`VaultFlowUnavailable`
    before an approval or deposit transaction is broadcast.
    """

    def __init__(self, vault: "IPORVault") -> None:
        """Bind the manager to an IPOR vault.

        :param vault:
            IPOR Fusion vault exposing its AccessManager address.
        """
        super().__init__(vault)

    @property
    def vault(self) -> "IPORVault":
        """Return the manager's IPOR vault with a precise type."""
        return self._vault

    @vault.setter
    def vault(self, vault: "IPORVault") -> None:
        """Store the vault accepted by the common manager constructor."""
        self._vault = vault

    def _assert_immediate_access(
        self,
        owner: HexAddress,
        selector: HexBytes,
        direction: Literal["deposit", "redeem"],
    ) -> None:
        """Reject a caller that cannot immediately use an IPOR selector.

        :param owner:
            Account that will submit the transaction.
        :param selector:
            Four-byte ERC-4626 selector guarded by AccessManager.
        :param direction:
            Diagnostic flow direction.

        :raise VaultFlowUnavailable:
            If the selector is denied or requires scheduled execution.
        :raise NotImplementedError:
            If the deployment has no readable AccessManager.
        """
        immediate, delay = self.vault.fetch_selector_access(owner, selector)
        if immediate:
            return

        if delay > 0:
            reason = "IPOR access requires delayed execution"
            decoded_error = "AccessManagerNotScheduled"
        else:
            # ``canCall() == (False, 0)`` can mean an unauthorised caller, a
            # closed target, or an IPOR-specific temporary redemption lock.
            # It does not decode a particular revert error by itself.
            reason = "IPOR AccessManager does not allow immediate vault flow"
            decoded_error = None

        raise VaultFlowUnavailable(
            reason,
            protocol="IPOR Fusion",
            vault_address=self.vault.address,
            caller=owner,
            direction=direction,
            phase="preflight",
            decoded_error=decoded_error,
            function_selector=selector,
            access_delay=delay,
        )

    def create_deposit_request(
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        amount: Decimal | None = None,
        raw_amount: int | None = None,
        check_max_deposit: bool = True,
        check_enough_token: bool = True,
    ) -> ERC4626DepositRequest:  # noqa: PLR0917, FBT001, FBT002
        """Create a standard ERC-4626 deposit after access admission.

        :param owner:
            Account submitting and signing the deposit.
        :param to:
            Optional receiver, limited by the shared ERC-4626 manager.
        :param amount:
            Human-readable denomination amount.
        :param raw_amount:
            Raw denomination amount.
        :param check_max_deposit:
            Whether to run the shared ERC-4626 capacity check.
        :param check_enough_token:
            Whether to run the shared balance check.
        :return:
            Preflighted deposit request.
        """
        self._assert_immediate_access(owner, self.vault.get_deposit_function_selector(), "deposit")
        return super().create_deposit_request(
            owner=owner,
            to=to,
            amount=amount,
            raw_amount=raw_amount,
            check_max_deposit=check_max_deposit,
            check_enough_token=check_enough_token,
        )

    def create_redemption_request(
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        shares: Decimal | None = None,
        raw_shares: int | None = None,
        check_max_deposit: bool = True,
        check_enough_token: bool = True,
    ) -> ERC4626RedemptionRequest:  # noqa: PLR0917, FBT001, FBT002
        """Create a standard ERC-4626 redemption after access admission.

        :param owner:
            Account submitting and signing the redemption.
        :param to:
            Optional receiver, limited by the shared ERC-4626 manager.
        :param shares:
            Human-readable share amount.
        :param raw_shares:
            Raw share amount.
        :param check_max_deposit:
            Compatibility argument forwarded to the shared manager.
        :param check_enough_token:
            Whether to run the shared share-balance check.
        :return:
            Preflighted redemption request.
        """
        self._assert_immediate_access(owner, self.vault.get_redeem_function_selector(), "redeem")
        return super().create_redemption_request(
            owner=owner,
            to=to,
            shares=shares,
            raw_shares=raw_shares,
            check_max_deposit=check_max_deposit,
            check_enough_token=check_enough_token,
        )

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        """Return whether an account can immediately call IPOR deposit.

        :param owner:
            Account to evaluate.
        :return:
            ``True`` only for immediate selector access.
        """
        try:
            self._assert_immediate_access(owner, self.vault.get_deposit_function_selector(), "deposit")
        except (VaultFlowUnavailable, NotImplementedError):
            return False
        return True

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        """Return whether an account can immediately call IPOR redemption.

        :param owner:
            Account to evaluate.
        :return:
            ``True`` only for immediate selector access.
        """
        try:
            self._assert_immediate_access(owner, self.vault.get_redeem_function_selector(), "redeem")
        except (VaultFlowUnavailable, NotImplementedError):
            return False
        return True
