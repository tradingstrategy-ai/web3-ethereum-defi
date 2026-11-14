"""Morpho Vault V1 and V2 redemption transaction preflights.

Morpho Vault V2 deliberately returns zero from its ERC-4626 maximum functions,
so exact transaction simulation is needed to expose failures before broadcast.
See the `official Vault V2 liquidity and ERC-4626 documentation
<https://github.com/morpho-org/vault-v2#liquidity>`__.
"""

from decimal import Decimal

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError
from eth_defi.provider.fallback import ExtraValueError

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626DepositRequest, ERC4626RedemptionRequest
from eth_defi.provider.anvil import fund_erc20_on_anvil, is_anvil
from eth_defi.vault.deposit_redeem import UnsupportedVaultSimulation, VaultFlowUnavailable, WhitelistingRequired, VaultRedemptionSimulationIntervention, extract_revert_data

#: ``TransferReverted()`` from Morpho Vault V2's ``SafeERC20Lib.safeTransfer``.
#: The library is used for the final redemption asset payout:
#: https://github.com/morpho-org/vault-v2/blob/main/src/libraries/SafeERC20Lib.sol
MORPHO_V2_TRANSFER_REVERTED_SELECTOR = HexBytes("0xace2a47e")

#: Divisor for the Anvil liquidity injection margin: ``10_000`` adds one basis
#: point of the required payout to cover interest accrued before the retry block.
MORPHO_V2_REDEMPTION_MARGIN_DIVISOR = 10_000


class MorphoV1DepositManager(ERC4626DepositManager):
    """Preserve requested shares across MetaMorpho's capacity conversion rounding."""

    def _is_redemption_capacity_rounding(self, raw_shares: int, maximum: int) -> bool:
        """Allow at most one raw asset unit of shortfall after exact simulation."""
        return 0 < maximum < raw_shares and self.vault.vault_contract.functions.previewRedeem(raw_shares - maximum).call() <= 1


class MorphoV2DepositManager(ERC4626DepositManager):
    """Simulate Morpho Vault V2 redemptions before transaction broadcast.

    Morpho's maximum functions cannot advertise live availability. This manager
    therefore retains the generic request construction and adds an ``eth_call``
    of the exact redemption from its eventual caller.
    """

    def create_deposit_request(self, owner: HexAddress, to: HexAddress | None = None, amount: Decimal | None = None, raw_amount: int | None = None, check_max_deposit: bool = True, check_enough_token: bool = True) -> ERC4626DepositRequest:
        """Check the depositor actually used when Morpho forwards assets to another vault.

        Morpho's liquidity adapter sends assets to the underlying vault and
        receives its shares. Checking the user's account there would miss a
        restriction on the adapter itself. For recognised adapters, check both
        permissions using the adapter address. Missing optional functions leave
        the final decision to the deposit transaction's contract checks.

        :param owner: Outer asset sender.
        :param to: Outer share receiver.
        :param amount: Decimal deposit amount.
        :param raw_amount: Deposit in the token's smallest integer units.
        :param check_max_deposit: Retain ordinary deposit-limit checks.
        :param check_enough_token: Retain ordinary funding checks.
        :return: Standard ERC-4626 deposit request.
        """
        contract = get_deployed_contract(self.web3, "morpho/IVaultV2.json", self.vault.address)
        adapter = contract.functions.liquidityAdapter().call()
        if int(adapter, 16):
            # This optional adapter-identification getter has no stored ABI.
            try:
                result = self.web3.eth.call({"to": adapter, "data": Web3.keccak(text="morphoVaultV1()")[:4]})
            except (ContractLogicError, ExtraValueError):
                result = b""
            if len(result) == 32 and int.from_bytes(result, "big"):
                inner = Web3.to_checksum_address(result[-20:])
                # The V2 ABI provides these optional predicates; inner vaults
                # without them fall through to normal onchain validation.
                inner_contract = get_deployed_contract(self.web3, "morpho/IVaultV2.json", inner)
                for predicate, decoded_error in (
                    (inner_contract.functions.canSendAssets(adapter), "CannotSendAssets"),
                    (inner_contract.functions.canReceiveShares(adapter), "CannotReceiveShares"),
                ):
                    try:
                        allowed = predicate.call()
                    except (BadFunctionCallOutput, ContractLogicError, ExtraValueError):
                        continue
                    if not allowed:
                        raise WhitelistingRequired(f"Morpho outer vault {self.vault.address} on chain {self.vault.chain_id}: liquidity adapter depositor {adapter} is denied by inner vault {inner} ({predicate.fn_name})", protocol="Morpho", vault_address=inner, caller=adapter, direction="deposit", phase="preflight", decoded_error=decoded_error)
        return super().create_deposit_request(owner, to, amount, raw_amount, check_max_deposit, check_enough_token)

    def fetch_redemption_liquidity(self) -> tuple[HexAddress, int, int]:
        """Read balances that can satisfy a Morpho V2 redemption payout.

        Vault V2 may hold idle assets directly or pull them through its
        configured liquidity adapter. This helper reports both the funding
        target and the combined immediately visible token balance.

        See `Morpho Vault V2 liquidity documentation <https://github.com/morpho-org/vault-v2#liquidity>`__.

        :return:
            ``(target, target_balance, available_balance)`` in raw denomination
            token units. ``target`` is the liquidity adapter when configured,
            otherwise the vault itself.
        """
        morpho_v2_contract = get_deployed_contract(
            self.web3,
            "morpho/IVaultV2.json",
            self.vault.address,
        )
        liquidity_adapter = morpho_v2_contract.functions.liquidityAdapter().call()
        target = liquidity_adapter if int(liquidity_adapter, 16) != 0 else self.vault.address
        token = self.vault.denomination_token
        vault_balance_raw = token.fetch_raw_balance_of(self.vault.address)
        target_balance_raw = vault_balance_raw if target == self.vault.address else token.fetch_raw_balance_of(target)
        available_raw = vault_balance_raw if target == self.vault.address else vault_balance_raw + target_balance_raw
        return target, target_balance_raw, available_raw

    def force_redemption_liquidity(
        self,
        owner: HexAddress,
        raw_shares: int,
        failure: VaultFlowUnavailable,
    ) -> VaultRedemptionSimulationIntervention:
        """Inject only the missing payout asset for a proven transfer failure.

        Morpho Vault V2 deallocates a redemption shortfall from its configured
        liquidity adapter before paying from the vault. When the exact call
        fails in that SafeERC20 transfer chain on an Anvil fork, provision the
        adapter with only the shortfall returned by ``previewRedeem`` and let
        the caller retry the unchanged redemption transaction. A deployment
        without a liquidity adapter is provisioned at the vault's idle-asset
        balance instead. Vault V2 accounts through its stored ``_totalAssets``;
        the helper adds a one basis point margin for interest accrued before the
        retry block. No gate,
        minimum or time condition is bypassed.

        :param owner:
            Redemption owner. Retained for the common intervention interface.
        :param raw_shares:
            Exact raw share quantity that the unchanged retry will redeem.
        :param failure:
            Source preflight failure proving the transfer path reverted.
        :return:
            Structured evidence describing the Anvil-only token injection.
        :raise UnsupportedVaultSimulation:
            If the provider is not Anvil, the failure is not the recognised
            transfer error, or balance provisioning does not reproduce safely.
        """
        del owner
        if not is_anvil(self.web3):
            raise UnsupportedVaultSimulation(
                "Morpho Vault V2 redemption liquidity injection requires Anvil",
                unsupported_reason="anvil_provider_required",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )
        if failure.decoded_error != "TransferReverted" or failure.error_selector != MORPHO_V2_TRANSFER_REVERTED_SELECTOR:
            raise UnsupportedVaultSimulation(
                "Morpho Vault V2 liquidity injection requires a proven TransferReverted redemption preflight",
                unsupported_reason="redemption_failure_not_liquidity_transfer",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )

        token = self.vault.denomination_token
        injected_raw = 0
        for _attempt in range(4):
            required_raw = int(self.vault.vault_contract.functions.previewRedeem(raw_shares).call())
            target, target_balance_raw, available_raw = self.fetch_redemption_liquidity()
            if available_raw >= required_raw:
                break
            # Vault V2 accrues interest per second, and Anvil stamps the retry
            # block with wall-clock time, so previewRedeem can grow by a few
            # units between these reads and the mined redemption. A one basis
            # point margin covers hours of accrual.
            top_up_raw = required_raw - available_raw + max(1, required_raw // MORPHO_V2_REDEMPTION_MARGIN_DIVISOR)
            fund_erc20_on_anvil(
                self.web3,
                token.address,
                target,
                target_balance_raw + top_up_raw,
            )
            injected_raw += top_up_raw
        else:
            raise UnsupportedVaultSimulation(
                "Morpho Vault V2 redemption liquidity did not converge after provisioning",
                unsupported_reason="morpho_v2_redemption_liquidity_not_reproducible",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )
        if injected_raw == 0:
            raise UnsupportedVaultSimulation(
                "Morpho Vault V2 transfer failed despite sufficient redemption liquidity",
                unsupported_reason="redemption_transfer_failure_not_balance_shortfall",
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                direction="redeem",
            )
        return VaultRedemptionSimulationIntervention(
            kind="liquidity_injected",
            token=token.address,
            target=target,
            raw_amount=injected_raw,
            original_reason=failure.reason,
            original_preflight_result=failure.preflight_result,
        )

    def create_redemption_request(  # noqa: PLR0917
        self,
        owner: HexAddress,
        to: HexAddress | None = None,
        shares: Decimal | None = None,
        raw_shares: int | None = None,
        check_max_deposit: bool = True,  # noqa: FBT001, FBT002
        check_enough_token: bool = True,  # noqa: FBT001, FBT002
        check_max_redeem: bool = True,  # noqa: FBT001, FBT002
    ) -> ERC4626RedemptionRequest:
        """Build a redemption only when its exact call passes simulation.

        Morpho Vault V2 may accept request construction even when its final
        denomination-token payout reverts. The protocol's
        ``SafeERC20Lib.safeTransfer`` maps that condition to
        ``TransferReverted()``. This preflight reports the protocol error without
        claiming whether liquidity, token policy, or another token-level
        condition caused the transfer to revert.

        :param owner:
            Share owner and eventual redemption caller.
        :param to:
            Asset receiver. The generic Morpho V2 request currently requires it
            to default to ``owner``.
        :param shares:
            Human-readable share amount, mutually exclusive with ``raw_shares``.
        :param raw_shares:
            Raw share amount, mutually exclusive with ``shares``.
        :param check_max_deposit:
            Compatibility argument forwarded to the generic manager.
        :param check_enough_token:
            Check the owner's current share balance.
        :param check_max_redeem:
            Compatibility argument forwarded to the generic manager.
        :return:
            A redemption request whose exact call completed successfully under
            ``eth_call``.
        :raise VaultFlowUnavailable:
            If the exact Morpho redemption call reverts.
        """
        request = super().create_redemption_request(
            owner=owner,
            to=to,
            shares=shares,
            raw_shares=raw_shares,
            check_max_deposit=check_max_deposit,
            check_enough_token=check_enough_token,
            check_max_redeem=check_max_redeem,
        )
        try:
            request.funcs[0].call({"from": owner})
        except (ContractLogicError, ValueError) as error:
            revert_data = extract_revert_data(error)
            if revert_data is None and isinstance(error, ValueError):
                raise

            selector_length = len(MORPHO_V2_TRANSFER_REVERTED_SELECTOR)
            error_selector = revert_data[:selector_length] if revert_data and len(revert_data) >= selector_length else None
            transfer_failed = error_selector == MORPHO_V2_TRANSFER_REVERTED_SELECTOR
            available_raw = None
            liquidity_shortfall = False
            if transfer_failed:
                required_raw = int(self.vault.vault_contract.functions.previewRedeem(request.raw_shares).call())
                _target, _target_balance_raw, available_raw = self.fetch_redemption_liquidity()
                liquidity_shortfall = available_raw < required_raw
            if liquidity_shortfall:
                preflight_result = "redemption_liquidity_unavailable"
            elif transfer_failed:
                preflight_result = "redemption_asset_transfer_failed"
            else:
                preflight_result = "redemption_call_reverted"
            raise VaultFlowUnavailable(
                (f"Morpho Vault V2 redemption asset transfer failed for vault {self.vault.address} on chain {self.vault.chain_id}" if transfer_failed else f"Morpho Vault V2 redemption call reverted for vault {self.vault.address} on chain {self.vault.chain_id}"),
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                caller=owner,
                direction="redeem",
                phase="preflight",
                decoded_error="TransferReverted" if transfer_failed else None,
                preflight_result=preflight_result,
                raw_revert_data=revert_data,
                requested_raw_amount=request.raw_shares,
                available_raw_amount=available_raw if transfer_failed else None,
                error_selector=error_selector,
            ) from error
        return request
