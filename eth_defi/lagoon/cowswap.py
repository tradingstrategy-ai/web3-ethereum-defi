"""Cow swap support for Lagoon vaults.

- Cow Swap Pythonn SDK https://github.com/cowdao-grants/cow-py
- See SwapCowSwap.sol and GuardV0Base.sol


Notes

- On Yearn and Cow integration, see https://medium.com/iearn/yearn-cow-swap-371b6d7cf3b3

"""

import datetime
import logging
from decimal import Decimal
from typing import TypeAlias, Callable, TypedDict, Any

import requests
from web3 import Web3
from web3.contract.contract import ContractFunction
from web3._utils.events import EventLogErrorFlags
from hexbytes import HexBytes
from eth_typing import HexAddress

from eth_defi.abi import get_contract
from eth_defi.cow.constants import COWSWAP_SETTLEMENT, get_cowswap_api
from eth_defi.hotwallet import HotWallet, SignedTransactionWithNonce
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation


logger = logging.getLogger(__name__)

#: How we broadcast and confirm our presigned tx
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


class GPv2OrderData(TypedDict):
    sell_token: str
    buy_token: str
    receiver: str
    sell_amount: int
    buy_amount: int
    valid_to: int
    app_data: bytes
    fee_amount: int
    kind: bytes
    partially_fillable: bool
    sell_token_balance: bytes
    buy_token_balance: bytes


def unpack_cow_order_data(raw_bytes: bytes) -> GPv2OrderData:
    """Decode order data we grab from the event."""

    assert type(raw_bytes) == bytes, f"Expected bytes, got {type(raw_bytes)}"

    if len(raw_bytes) != 384:
        raise ValueError("Input bytes must be exactly 384 bytes long (ABI-encoded struct)")

    def decode_address(offset: int) -> str:
        # Addresses are 20 bytes, padded left with zeros in 32-byte slot
        addr_bytes = raw_bytes[offset + 12 : offset + 32]
        return "0x" + addr_bytes.hex()

    def decode_uint(offset: int) -> int:
        # Decode 32-byte big-endian integer
        return int.from_bytes(raw_bytes[offset : offset + 32], "big")

    def decode_bytes32(offset: int) -> bytes:
        # bytes32 is exactly 32 bytes
        return "0x" + raw_bytes[offset : offset + 32]

    def decode_bool(offset: int) -> bool:
        # Bool is 0 or 1 in the last byte of the 32-byte slot
        return raw_bytes[offset + 31] == 1

    result: GPv2OrderData = {
        "sell_token": decode_address(0),
        "buy_token": decode_address(32),
        "receiver": decode_address(64),
        "sell_amount": decode_uint(96),
        "buy_amount": decode_uint(128),
        "valid_to": decode_uint(160),  # uint32 decoded as uint256, but value fits in uint32
        "app_data": decode_bytes32(192),
        "fee_amount": decode_uint(224),
        "kind": decode_bytes32(256),
        "partially_fillable": decode_bool(288),
        "sell_token_balance": decode_bytes32(320),
        "buy_token_balance": decode_bytes32(352),
    }

    return result


def presign_cowswap(
    vault: LagoonVault,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    min_amount_out: Decimal,
    app_data=b"\x00" * 32,
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
        app_data,
        sell_token.address,
        buy_token.address,
        amount_in_raw,
        min_amount_out_raw,
    )


def post_order(
    chain_id: int,
    order: dict,
    api_timeout: datetime.timedelta = datetime.timedelta(minutes=10),
):
    """Decode CowSwap order from event log and post to CowSwap API

    https://docs.cow.fi/cow-protocol/reference/apis/orderbook
    """

    base_url = get_cowswap_api(chain_id)

    # Javascript cannot  handle ints, so...
    crap_json = {k: str(v) for k, v in order.items()}

    response = requests.post(
        f"{base_url}/api/v1/orders",
        json=crap_json,
        timeout=api_timeout.total_seconds(),
    )

    response.raise_for_status()

    return response.json()


def presign_and_broadcast(
    web3: Web3,
    asset_manager: HotWallet | HexAddress,
    vault: LagoonVault,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    min_amount_out: Decimal,
    broadcast_callback: BroadcastCallback = _default_broadcast_callback,
) -> dict:
    """Broadcast presigned transcation onchain and return order payload.

    :return:
        Binary order data.
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

    return events[0]["args"]["order"]


def wait_order_complete(
    uid: str,
    trade_timeout: datetime.timedelta,
) -> dict:
    raise NotImplementedError()


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
) -> dict:
    chain_id = vault.web3.eth.chain_id

    order_data = presign_and_broadcast(
        asset_manager=asset_manager_wallet,
        vault=vault,
        buy_token=buy_token,
        sell_token=sell_token,
        amount_in=amount_in,
        min_amount_out=min_amount_out,
        broadcast_callback=broadcast_callback,
    )

    posted_order_uid = decode_and_post_order(
        chain_id,
        order_payload=order_data,
        api_timeout=api_timeout,
    )

    return wait_order_complete(
        posted_order_uid,
        trade_timeout,
    )
