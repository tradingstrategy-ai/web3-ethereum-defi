"""Morpho Vault V2 redemption transaction preflights.

Morpho Vault V2 deliberately returns zero from its ERC-4626 maximum functions,
so exact transaction simulation is needed to expose failures before broadcast.
See the `official Vault V2 liquidity and ERC-4626 documentation
<https://github.com/morpho-org/vault-v2#liquidity>`__.
"""

from decimal import Decimal

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3.exceptions import ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626RedemptionRequest
from eth_defi.provider.anvil import fund_erc20_on_anvil, is_anvil
from eth_defi.vault.deposit_redeem import UnsupportedVaultSimulation, VaultFlowUnavailable, VaultRedemptionSimulationIntervention, extract_revert_data

#: ``TransferReverted()`` from Morpho Vault V2's ``SafeERC20Lib.safeTransfer``.
#: The library is used for the final redemption asset payout:
#: https://github.com/morpho-org/vault-v2/blob/main/src/libraries/SafeERC20Lib.sol
MORPHO_V2_TRANSFER_REVERTED_SELECTOR = HexBytes("0xace2a47e")


class MorphoV2DepositManager(ERC4626DepositManager):
    """Simulate Morpho Vault V2 redemptions before transaction broadcast.

    Morpho's maximum functions cannot advertise live availability. This manager
    therefore retains the generic request construction and adds an ``eth_call``
    of the exact redemption from its eventual caller.
    """

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
        the helper adds one native unit for retry-time fee rounding. No gate,
        minimum or time condition is bypassed.
        """
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
        morpho_v2_contract = get_deployed_contract(
            self.web3,
            "morpho/IVaultV2.json",
            self.vault.address,
        )
        liquidity_adapter = morpho_v2_contract.functions.liquidityAdapter().call()
        target = liquidity_adapter if int(liquidity_adapter, 16) != 0 else self.vault.address
        injected_raw = 0
        for _attempt in range(4):
            required_raw = int(self.vault.vault_contract.functions.previewRedeem(raw_shares).call())
            vault_balance_raw = token.fetch_raw_balance_of(self.vault.address)
            target_balance_raw = token.fetch_raw_balance_of(target)
            available_raw = vault_balance_raw if target == self.vault.address else vault_balance_raw + target_balance_raw
            if available_raw >= required_raw:
                break
            # Include one native unit because Vault V2's fee/accrual rounding
            # can increase previewRedeem between the provisioning reads.
            top_up_raw = required_raw - available_raw + 1
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
            required_raw = int(self.vault.vault_contract.functions.previewRedeem(request.raw_shares).call())
            available_raw = self.vault.denomination_token.fetch_raw_balance_of(self.vault.address)
            liquidity_shortfall = transfer_failed and available_raw < required_raw
            raise VaultFlowUnavailable(
                (f"Morpho Vault V2 redemption asset transfer failed for vault {self.vault.address} on chain {self.vault.chain_id}" if transfer_failed else f"Morpho Vault V2 redemption call reverted for vault {self.vault.address} on chain {self.vault.chain_id}"),
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                caller=owner,
                direction="redeem",
                phase="preflight",
                decoded_error="TransferReverted" if transfer_failed else None,
                preflight_result="redemption_liquidity_unavailable" if liquidity_shortfall else "redemption_asset_transfer_failed" if transfer_failed else "redemption_call_reverted",
                raw_revert_data=revert_data,
                requested_raw_amount=request.raw_shares,
                available_raw_amount=available_raw if transfer_failed else None,
                error_selector=error_selector,
            ) from error
        return request
