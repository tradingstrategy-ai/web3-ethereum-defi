"""ERC-4626 estimations."""
from decimal import Decimal

from eth_typing import HexAddress

from eth_defi.abi import format_debug_instructions
from eth_defi.erc_4626.vault import ERC4626Vault


def estimate_4626_redeem(
    vault: ERC4626Vault,
    owner: HexAddress,
    share_amount: Decimal,
    receiver: HexAddress | None = None,
) -> Decimal:
    """Estimate how much denomination token (USDC) we get if we cash out the shares.

    - The vault should deduct its fees from this amount.

    - The estimation is done using `previewRedeem()`

    :return:
        Amount of USDC we get when existing the vault with the shares.
    """

    assert isinstance(vault, ERC4626Vault)
    assert isinstance(share_amount, Decimal)
    assert owner.startswith("0x")
    assert share_amount > 0

    if receiver is None:
        receiver = owner

    contract = vault.vault_contract

    # https://ethereum.org/en/developers/docs/standards/tokens/erc-4626/#events
    raw_share_amount = vault.share_token.convert_to_raw(share_amount)

    # Construct bound function
    redeem_call = contract.functions.previewRedeem(
        raw_share_amount,
    )

    raw_amount = redeem_call.call()

    return vault.denomination_token.convert_to_decimals(raw_amount)

