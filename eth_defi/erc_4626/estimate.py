"""ERC-4626 estimations.

- Deposit: estimate number of shares we are going to receive
- Redeem: estimate how much underlying denomination we are going to receive when burning shares
"""

from decimal import Decimal
import logging

from eth_typing import HexAddress
from web3.types import BlockIdentifier

from eth_defi.abi import format_debug_instructions
from eth_defi.erc_4626.vault import ERC4626Vault


logger = logging.getLogger(__name__)


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

    # revert ERC7540PreviewDepositDisabled();
    assert not vault.erc_7540, f"previewDeposit() is not supported for ERC-7540 vaults: {vault}"

    contract = vault.vault_contract
    raw_amount = vault.denomination_token.convert_to_raw(denomination_token_amount)

    assert raw_amount > 0, f"Denomination token amount must be greater than 0, got {raw_amount} for {denomination_token_amount} in vault {vault.name} ({vault.vault_address})"

    deposit_call = contract.functions.previewDeposit(
        raw_amount,
    )
    try:
        raw_amount = deposit_call.call(block_identifier=block_identifier)
    except Exception as e:
        raise RuntimeError(f"previewDeposit() failed at vault {vault} with amount {denomination_token_amount} ({raw_amount} raw) @ {block_identifier}\nRevert reason: {e}\n{format_debug_instructions(deposit_call)}") from e
    return vault.share_token.convert_to_decimals(raw_amount)


def estimate_value_by_share_price(
    vault: ERC4626Vault,
    share_amount: Decimal,
    block_identifier: BlockIdentifier = "latest",
):
    """Estimate ownership value by the share price."""

    assert isinstance(vault, ERC4626Vault)
    assert isinstance(share_amount, Decimal)
    assert share_amount > 0, f"Got non-positive amount as a share price estimate for {vault.name} ({vault.vault_address}): {share_amount}"

    share_price = vault.fetch_share_price(block_identifier=block_identifier)
    return share_amount * share_price


def estimate_4626_redeem(
    vault: ERC4626Vault,
    owner: HexAddress | None,
    share_amount: Decimal,
    receiver: HexAddress | None = None,
    block_identifier: BlockIdentifier = "latest",
    fallback_using_share_price=True,
) -> Decimal:
    """Estimate how much denomination token (USDC) we get if we cash out the shares.

    - The vault should deduct its fees from this amount.

    - The estimation is done using `previewRedeem()`

    See also

    - :py:func:`eth_defi.erc_4626.flow.redeem_4626` for the transaction crafting and notes.

    :param fallback_using_share_price:
        If `previewRedeem()` fails (some vaults may not implement it properly),
        fall back to estimating the value using the share price.

        This will also happen if the vault has lockups (Plutus) and shares cannot be redeemed at the moment.

        See :py:func:`estimate_value_by_share_price`.

    :return:
        Amount of USDC we get when existing the vault with the shares.
    """

    assert isinstance(vault, ERC4626Vault)
    assert isinstance(share_amount, Decimal)
    assert share_amount > 0, f"Got non-positive amount as a sell estimate for {vault.name} ({vault.vault_address}): {share_amount}"

    if receiver is None:
        receiver = owner

    contract = vault.vault_contract

    # https://ethereum.org/en/developers/docs/standards/tokens/erc-4626/#events
    raw_share_amount = vault.share_token.convert_to_raw(share_amount)

    assert raw_share_amount > 0, f"Share amount must be greater than 0, got {raw_share_amount} for {share_amount} in vault {vault.name} ({vault.vault_address})"

    # Construct bound function
    redeem_call = contract.functions.previewRedeem(
        raw_share_amount,
    )

    raw_amount = redeem_call.call(block_identifier=block_identifier)

    if raw_amount == 0 and fallback_using_share_price:
        logger.info(f"previewRedeem() returned 0 for vault {vault.name} {vault.vault_address}, falling back to share price estimation.")
        return estimate_value_by_share_price(
            vault,
            share_amount,
            block_identifier=block_identifier,
        )

    if raw_amount == 0:
        total_assets = vault.vault_contract.functions.totalAssets().call(block_identifier=block_identifier)
        total_supply = vault.vault_contract.functions.totalSupply().call(block_identifier=block_identifier)
        msg = f"previewRedeem() returned 0, this may indicate a problem with vault {vault.name} {vault.vault_address}.\nTotal assets: {total_assets}, total supply: {total_supply}.\nShare amount: {share_amount}, share amount raw: {raw_amount}.\nBlock identifier: {block_identifier}\n"
        raise RuntimeError(msg)

    return vault.denomination_token.convert_to_decimals(raw_amount)
