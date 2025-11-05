"""Cow swap support for Lagoon vaults.

- Cow Swap Pythonn SDK https://github.com/cowdao-grants/cow-py
- See SwapCowSwap.sol and GuardV0Base.sol

.. note ::

    Because CowSwap does not offer any kind of testnet, end-to-end test or unit test support it is a bit hard to work with.
    You need to perform manual test to ensure the code is working.

    See `lagoon-cowswap-example.py` script for a manual test example.

Notes

- On Yearn and Cow integration, see https://medium.com/iearn/yearn-cow-swap-371b6d7cf3b3
- `About CowSwap PreSign scheme <https://docs.cow.fi/cow-protocol/reference/core/signing-schemes#presign>`__

"""

import datetime
import logging
from decimal import Decimal
from typing import TypeAlias, Callable, Any

from web3 import Web3
from web3.contract.contract import ContractFunction
from web3._utils.events import EventLogErrorFlags
from hexbytes import HexBytes
from eth_typing import HexAddress

from eth_defi.abi import get_contract
from eth_defi.cow.constants import COWSWAP_SETTLEMENT, COWSWAP_VAULT_RELAYER
from eth_defi.cow.order import GPv2OrderData, post_order
from eth_defi.cow.status import wait_order_complete, CowSwapResult
from eth_defi.hotwallet import HotWallet
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)

#: How we broadcast and confirm our presigned tx
#:
#: def callback(web3, asset_manager: HexAddress | HotWallet, func: ContractFunction) -> tx hash:
BroadcastCallback: TypeAlias = Callable[[Web3, Any, ContractFunction], HexBytes]


def _default_broadcast_callback(web3: Web3, asset_manager: HexAddress | HotWallet, func: ContractFunction) -> HexBytes:
    """Default broadcast callback which sends the signed transaction and waits for confirmation."""

    if isinstance(asset_manager, HotWallet):
        tx = asset_manager.sign_bound_call_with_new_nonce(func)
        tx_hash = web3.eth.send_raw_transaction(tx.raw_transaction)
    else:
        tx_hash = func.transact({"from": asset_manager})

    assert_transaction_success_with_explanation(web3, tx_hash)
    return tx_hash


def approve_cow_swap(
    vault: LagoonVault,
    token: TokenDetails,
    amount: Decimal,
) -> ContractFunction:
    """Approve cowswap settlement contract to spend tokens on the behalf of Lagoon vault.

    See https://github.com/cowprotocol/cow-sdk/blob/5dd3bf5659852590d5d46317bfc19c56e125ca59/packages/trading/src/tradingSdk.ts#L290
    """

    assert isinstance(token, TokenDetails), f"Not a TokenDetails: {type(token)}"

    func = token.approve(COWSWAP_VAULT_RELAYER, amount)
    return vault.transact_via_trading_strategy_module(func)


def presign_cowswap(
    vault: LagoonVault,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    min_amount_out: Decimal,
    app_data=HexBytes("0x0000000000000000000000000000000000000000000000000000000000000000"),
) -> ContractFunction:
    """Construct a pre-signed CowSwap order for the offchain order book to execute using TradingStrategyModuleV0.

        :param app_data:
            From docs:  If you do not care about appData, set this field to "{}" and make sure that the order you signed for this request had its appData field set to 0xb48d38f93eaa084033fc5970bf96e559c33c4cdc07d889ab00b4d63f9590739d.
    tt
    """

    assert isinstance(vault, LagoonVault), f"Not a Lagoon vault: {type(vault)}"
    assert isinstance(buy_token, TokenDetails), f"Not a TokenDetails: {type(buy_token)}"
    assert isinstance(sell_token, TokenDetails), f"Not a TokenDetails: {type(sell_token)}"
    assert isinstance(amount_in, Decimal), f"Not a Decimal: {type(amount_in)}"
    assert isinstance(min_amount_out, Decimal), f"Not a Decimal: {type(min_amount_out)}"

    assert vault.safe_address is not None, f"Vault has no safe address: {vault}"

    amount_in_raw = sell_token.convert_to_raw(amount_in)
    min_amount_out_raw = buy_token.convert_to_raw(min_amount_out)

    trading_strategy_module = vault.trading_strategy_module
    assert trading_strategy_module is not None, f"Vault has no trading strategy module: {vault}"

    logger.info(
        f"CowSwap swap %s -> %s for %f (min out %f) via vault %s",
        sell_token.symbol,
        buy_token.symbol,
        amount_in,
        min_amount_out,
        vault.vault_address,
    )

    return trading_strategy_module.functions.swapAndValidateCowSwap(
        COWSWAP_SETTLEMENT,
        vault.safe_address,
        HexBytes(app_data),
        sell_token.address,
        buy_token.address,
        amount_in_raw,
        min_amount_out_raw,
    )


def presign_and_broadcast(
    asset_manager: HotWallet | HexAddress,
    vault: LagoonVault,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    min_amount_out: Decimal,
    broadcast_callback: BroadcastCallback = _default_broadcast_callback,
) -> GPv2OrderData:
    """Broadcast presigned transcation onchain and return order payload.

    - Create an order using onchain TradingStrategyModuleV0
    - Broadcast the onchain transaction
    - Extract the order data from the event log after the transaction is confirmed

    .. note ::

        You need to approve the correct amount from the vault on CoW Swap settlemetn contract before executing this.

    :return:
        Order data
    """
    web3 = vault.web3
    bound_func = presign_cowswap(
        vault,
        buy_token,
        sell_token,
        amount_in,
        min_amount_out,
    )
    tx_hash = broadcast_callback(web3, asset_manager, bound_func)

    #     event OrderSigned(
    #         uint256 indexed timestamp, bytes orderUid, GPv2Order.Data order, uint32 validTo, uint256 buyAmount, uint256 sellAmount
    #     );
    receipt = web3.eth.get_transaction_receipt(tx_hash)

    TradingStrategyModuleV0 = get_contract(
        web3,
        "safe-integration/TradingStrategyModuleV0.json",
    )

    events = list(TradingStrategyModuleV0.events.OrderSigned().process_receipt(receipt, EventLogErrorFlags.Discard))

    assert len(events) == 1, f"Expected exactly one OrderSigned event, got {len(events)} for {receipt}"

    data = events[0]["args"]["order"]
    uid = events[0]["args"]["orderUid"]
    # TODO: appData unsupported for now
    data["appData"] = "0x0000000000000000000000000000000000000000000000000000000000000000"
    # data["appDataHash"] = "0x0000000000000000000000000000000000000000000000000000000000000000"
    data["sellTokenBalance"] = "erc20"
    data["buyTokenBalance"] = "erc20"
    data["kind"] = "sell"  # TODO: Currently hardcoded to sell only
    data["from"] = vault.safe_address
    data["receiver"] = vault.safe_address

    #: Attach presigned tx hash for reference
    data["tx_hash"] = tx_hash.hex()
    data["uid"] = "0x" + uid.hex()

    return data


def execute_presigned_cowswap_order(
    chain_id: int,
    order: GPv2OrderData,
    trade_timeout: datetime.timedelta = datetime.timedelta(minutes=10),
    api_timeout: datetime.timedelta = datetime.timedelta(seconds=60),
) -> CowSwapResult:
    """Execute a presigned CowSwap order.

    - Post the order to CowSwap API
    - Wait for the order to complete

    :return:
        CowSwapResult with order UID and final status
    """
    post_order_reply = post_order(
        chain_id,
        order=order,
        api_timeout=api_timeout,
    )

    posted_order_uid = post_order_reply.order_uid

    final_status = wait_order_complete(
        chain_id=chain_id,
        uid=posted_order_uid,
        trade_timeout=trade_timeout,
        api_timeout=api_timeout,
    )

    return CowSwapResult(
        order_uid=HexBytes(posted_order_uid),
        order=order,
        final_status_reply=final_status,
    )


def presign_and_execute_cowswap(
    asset_manager_wallet: HotWallet,
    vault: LagoonVault,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    min_amount_out: Decimal,
    broadcast_callback: BroadcastCallback = _default_broadcast_callback,
    api_timeout=datetime.timedelta(seconds=60),
    trade_timeout: datetime.timedelta = datetime.timedelta(minutes=10),
) -> CowSwapResult:
    """Creates a sell order from the vault and executes it on CowSwap.

    Blocks until the order is completed or failed or timed out.

    :raise:
        Various exceptions from broadcasting and order execution fails/timeouts..
    """
    order = presign_and_broadcast(
        asset_manager=asset_manager_wallet,
        vault=vault,
        buy_token=buy_token,
        sell_token=sell_token,
        amount_in=amount_in,
        min_amount_out=min_amount_out,
        broadcast_callback=broadcast_callback,
    )
    return execute_presigned_cowswap_order(
        order,
        api_timeout=api_timeout,
        trade_timeout=trade_timeout,
    )
