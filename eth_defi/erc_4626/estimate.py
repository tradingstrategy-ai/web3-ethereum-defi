"""ERC-4626 estimations."""
from decimal import Decimal

from eth_typing import HexAddress
from web3.types import BlockIdentifier

from eth_defi.abi import format_debug_instructions
from eth_defi.erc_4626.vault import ERC4626Vault


def estimate_4626_deposit(
    vault: ERC4626Vault,
    denomination_token_amount: Decimal,
    block_identifier: BlockIdentifier = "latest",
) -> Decimal:
    """Estimate how much shares we get for a deposit.

    - The vault should deduct its fees from this amount.

    - The estimation is done using `previewRedeem()`

    :return:
        Amount of USDC we get when existing the vault with the shares.
    """

    assert isinstance(vault, ERC4626Vault)
    assert isinstance(denomination_token_amount, Decimal)
    assert denomination_token_amount > 0
    contract = vault.vault_contract
    deposit_call = contract.functions.previewDeposit(
        vault.denomination_token.convert_to_raw(denomination_token_amount),
    )
    raw_amount = deposit_call.call(block_identifier=block_identifier)
    return vault.share_token.convert_to_decimals(raw_amount)


def estimate_4626_redeem(
    vault: ERC4626Vault,
    owner: HexAddress | None,
    share_amount: Decimal,
    receiver: HexAddress | None = None,
    block_identifier: BlockIdentifier = "latest",
) -> Decimal:
    """Estimate how much denomination token (USDC) we get if we cash out the shares.

    - The vault should deduct its fees from this amount.

    - The estimation is done using `previewRedeem()`

    :return:
        Amount of USDC we get when existing the vault with the shares.
    """

    assert isinstance(vault, ERC4626Vault)
    assert isinstance(share_amount, Decimal)
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

    raw_amount = redeem_call.call(block_identifier=block_identifier)

    return vault.denomination_token.convert_to_decimals(raw_amount)

