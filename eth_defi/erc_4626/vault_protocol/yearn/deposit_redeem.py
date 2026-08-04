"""Yearn V3 deposit preflight extensions."""

from decimal import Decimal

from eth_typing import HexAddress
from web3.exceptions import ABIFunctionNotFound, BadFunctionCallOutput, ContractLogicError

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626DepositRequest
from eth_defi.vault.deposit_redeem import UnsupportedVaultSimulation, VaultFlowUnavailable, extract_revert_data

ERROR_SELECTOR_LENGTH = 4


class YearnV3DepositManager(ERC4626DepositManager):
    """Recognise Yearn V3 closures and rejected approved deposits.

    Yearn deliberately returns zero for ``maxDeposit(address(0))``, so the
    generic ERC-4626 global-closure reader cannot be used. A vault without a
    deposit-limit module has a vault-wide ``deposit_limit``; an owner-specific
    zero ``maxDeposit`` then means shutdown or a full global limit. A module
    can impose account-specific policy. Once an owner has already approved the
    requested amount, the manager simulates its exact ``deposit()`` call to
    expose a vault-specific admission rejection before broadcast.
    """

    def fetch_global_deposit_closure_reason(self, owner: HexAddress) -> str | None:
        """Identify a Yearn closure that is independent of account policy.

        :param owner:
            Account for the real deposit attempt and simulated receiver.
        :return:
            A stable closure reason, or ``None`` when the state is open or a
            per-account deposit-limit module prevents a safe determination.
        """
        try:
            contract = self.vault.vault_contract
            if contract.functions.isShutdown().call():
                return "Yearn vault is shut down"

            deposit_limit_module = contract.functions.deposit_limit_module().call()
            if deposit_limit_module.lower() != ZERO_ADDRESS_STR.lower():
                return None

            if contract.functions.maxDeposit(owner).call() == 0:
                return "Yearn deposit limit reached (maxDeposit=0)"
        except (ABIFunctionNotFound, BadFunctionCallOutput, ContractLogicError, ValueError):
            return None
        return None

    def fetch_depositable_raw_assets(self, owner: HexAddress) -> int:
        """Read Yearn's owner-specific capacity without normalising zero.

        :param owner:
            Account that would receive the Yearn shares.
        :return:
            Current raw capacity, including a zero result from an unknown
            deposit-limit module, which must remain a normal preflight refusal.
        """
        return int(self.vault.vault_contract.functions.maxDeposit(owner).call())

    def fetch_deposit_rejection(self, owner: HexAddress, raw_amount: int) -> VaultFlowUnavailable | None:
        """Simulate an approved Yearn deposit using the actual sender and amount.

        A new request normally includes an ERC-20 approval, so simulating before
        that approval would only produce a false allowance failure. When the
        owner already has sufficient allowance, ``eth_call`` is the strongest
        admission check available for vault-specific Yearn limits. Transport
        failures are propagated instead of being reported as an admission
        rejection.

        :param owner:
            Account that has approved the vault and would submit the deposit.
        :param raw_amount:
            Native denomination-token amount to deposit.
        :return:
            A structured preflight rejection, or ``None`` if the caller has no
            sufficient allowance yet or the exact call succeeds.
        :raise ValueError:
            If the provider fails without EVM revert data.
        """
        token = self.vault.denomination_token
        assert token is not None, "Vault denomination token data missing"
        allowance = token.contract.functions.allowance(owner, self.vault.address).call()
        if allowance < raw_amount:
            return None
        try:
            self.vault.vault_contract.functions.deposit(raw_amount, owner).call({"from": owner})
        except (ContractLogicError, ValueError) as error:
            revert_data = extract_revert_data(error)
            if revert_data is None:
                raise
            error_selector = revert_data[:ERROR_SELECTOR_LENGTH] if revert_data and len(revert_data) >= ERROR_SELECTOR_LENGTH else None
            reason = "Yearn vault rejected the approved deposit call"
            return VaultFlowUnavailable(
                reason,
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                caller=owner,
                direction="deposit",
                phase="preflight",
                decoded_error=str(error),
                preflight_result="deposit_admission_rejected",
                raw_revert_data=revert_data,
                requested_raw_amount=raw_amount,
                error_selector=error_selector,
            )
        return None

    def create_deposit_request(  # noqa: PLR0917
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        amount: Decimal | None = None,
        raw_amount: int | None = None,
        check_max_deposit: bool = True,  # noqa: FBT001, FBT002
        check_enough_token: bool = True,  # noqa: FBT001, FBT002
    ) -> ERC4626DepositRequest:
        """Build a Yearn deposit or expose a predictable refusal before broadcast.

        :param owner:
            Account funding the assets and receiving shares.
        :param to:
            Unsupported separate receiver; inherited behaviour requires owner.
        :param amount:
            Decimal denomination-token amount.
        :param raw_amount:
            Raw denomination-token amount.
        :param check_max_deposit:
            Enable the normal global-closure and capacity preflight.
        :param check_enough_token:
            Enable the inherited denomination-token balance preflight.
        :return:
            Production Yearn ERC-4626 deposit request.
        :raise VaultFlowUnavailable:
            When Yearn is shut down, its global deposit cap is full, or an
            already-approved exact deposit call is rejected.
        """
        if check_max_deposit:
            # Preserve admission priority: an account-policy denial must never
            # be converted to a temporary closed-deposit simulation outcome.
            self.check_deposit_whitelist(owner)
            requested_raw_amount = raw_amount
            if requested_raw_amount is None:
                assert self.vault.denomination_token is not None, "Vault denomination token data missing: likely flaky RPC"
                requested_raw_amount = self.vault.denomination_token.convert_to_raw(amount)
            closed_reason = self.fetch_global_deposit_closure_reason(owner)
            if closed_reason is not None:
                raise VaultFlowUnavailable(
                    closed_reason,
                    protocol=self.vault.get_protocol_name(),
                    vault_address=self.vault.address,
                    caller=owner,
                    direction="deposit",
                    phase="preflight",
                    preflight_result="deposit_closed",
                    requested_raw_amount=requested_raw_amount,
                    available_raw_amount=0,
                )
            rejection = self.fetch_deposit_rejection(owner, requested_raw_amount)
            if rejection is not None:
                raise rejection
        return super().create_deposit_request(owner, to, amount, raw_amount, check_max_deposit, check_enough_token)

    def create_deposit_request_for_guard_validation(
        self,
        owner: HexAddress,
        raw_amount: int,
    ) -> ERC4626DepositRequest:
        """Build Yearn deposit calldata after a proven global closure.

        :param owner:
            SimpleVaultV0/Safe address that would receive the Yearn shares.
        :param raw_amount:
            Raw denomination-token amount from the rejected live preflight.
        :return:
            Deposit calldata for non-broadcast GuardV0 validation.
        :raise UnsupportedVaultSimulation:
            If the vault has no proven global closure or provider is not Anvil.
        """
        self._assert_anvil_guard_validation()
        if self.fetch_global_deposit_closure_reason(owner) is None:
            raise UnsupportedVaultSimulation(
                f"{self.__class__.__name__} cannot validate a Yearn deposit without a proven global closure",
                unsupported_reason="closed_deposit_guard_validation_not_closed",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="deposit",
                phase="guard_validation",
            )
        return self.create_deposit_request(
            owner=owner,
            to=owner,
            raw_amount=raw_amount,
            check_max_deposit=False,
            check_enough_token=False,
        )
