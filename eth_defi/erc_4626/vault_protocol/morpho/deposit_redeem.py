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

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626RedemptionRequest
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable, extract_revert_data

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
            raise VaultFlowUnavailable(
                (f"Morpho Vault V2 redemption asset transfer failed for vault {self.vault.address} on chain {self.vault.chain_id}" if transfer_failed else f"Morpho Vault V2 redemption call reverted for vault {self.vault.address} on chain {self.vault.chain_id}"),
                protocol=self.vault.get_protocol_name(),
                vault_address=self.vault.address,
                caller=owner,
                direction="redeem",
                phase="preflight",
                decoded_error="TransferReverted" if transfer_failed else None,
                preflight_result="redemption_asset_transfer_failed" if transfer_failed else "redemption_call_reverted",
                raw_revert_data=revert_data,
                requested_raw_amount=request.raw_shares,
                error_selector=error_selector,
            ) from error
        return request
