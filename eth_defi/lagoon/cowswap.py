"""Cow swap support for Lagoon vaults.

- Cow Swap Pythonn SDK https://github.com/cowdao-grants/cow-py
- See SwapCowSwap.sol and GuardV0Base.sol


Notes

- On Yearn and Cow integration, see https://medium.com/iearn/yearn-cow-swap-371b6d7cf3b3

"""

from decimal import Decimal

from web3.contract.contract import ContractFunction

from eth_defi.cow.constants import COWSWAP_SETTLEMENT
from eth_defi.hotwallet import HotWallet
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation


def presign_cowswap(
    vault: LagoonVault,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    min_amount_out: Decimal,
) -> ContractFunction:
    """Construct a pre-signed CowSwap order for the offchain order book to execute using TradingStrategyModuleV0."""

    assert isinstance(vault, LagoonVault), f"Not a Lagoon vault: {type(vault)}"
    assert isinstance(buy_token, TokenDetails), f"Not a TokenDetails: {type(buy_token)}"
    assert isinstance(sell_token, TokenDetails), f"Not a TokenDetails: {type(sell_token)}"
    assert isinstance(amount_in, Decimal), f"Not a Decimal: {type(amount_in)}"
    assert isinstance(min_amount_out, Decimal), f"Not a Decimal: {type(min_amount_out)}"

    amount_in_raw = buy_token.convert_to_raw(amount_in)
    min_amount_out_raw = sell_token.convert_to_raw(min_amount_out)

    trading_strategy_module = vault.trading_strategy_module
    assert trading_strategy_module is not None, f"Vault has no trading strategy module: {vault}"

    return trading_strategy_module.functions.swapAndValidateCowSwap(
        COWSWAP_SETTLEMENT,
        vault.safe_address,
        buy_token.address,
        sell_token.address,
        amount_in_raw,
        min_amount_out_raw,
    )


def presign_and_broadcast(
    asset_manager_wallet: HotWallet,
    vault: LagoonVault,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    min_amount_out: Decimal,
):
    """Broadcast presigned transcation onchain and return order payload"""
    web3 = vault.web3
    bound_func = presign_cowswap(
        vault,
        buy_token,
        sell_token,
        amount_in,
        min_amount_out,
    )
    tx_hash = asset_manager_wallet.sign_bound_call_with_new_nonce(bound_func)
    assert_transaction_success_with_explanation(web3, tx_hash)

    #     event OrderSigned(
    #         uint256 indexed timestamp, bytes orderUid, GPv2Order.Data order, uint32 validTo, uint256 buyAmount, uint256 sellAmount
    #     );
    receipt = web3.eth.get_transaction_receipt(tx_hash)


def presign_and_execute():
    pass
