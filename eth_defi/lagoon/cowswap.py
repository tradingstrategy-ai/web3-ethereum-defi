"""Cow swap support for Lagoon vaults.

- Cow Swap Pythonn SDK https://github.com/cowdao-grants/cow-py
- See SwapCowSwap.sol and GuardV0Base.sol

.. note ::

    Because CowSwap does not offer any kind of testnet, end-to-end test or unit test support it is a bit hard to work with.
    You need to perform manual test to ensure the code is working.

    See `lagoon-cowswap-example.py` script for a manual test example.

Notes

- On Yearn and Cow integration, see https://medium.com/iearn/yearn-cow-swap-371b6d7cf3b3

"""

import datetime
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum
from pprint import pformat
from typing import TypeAlias, Callable, TypedDict, Any, Literal

import requests
from web3 import Web3
from web3.contract.contract import ContractFunction
from web3._utils.events import EventLogErrorFlags
from hexbytes import HexBytes
from eth_typing import HexAddress

from eth_defi.abi import get_contract
from eth_defi.compat import native_datetime_utc_now
from eth_defi.cow.constants import COWSWAP_SETTLEMENT, get_cowswap_api
from eth_defi.hotwallet import HotWallet
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation


logger = logging.getLogger(__name__)

#: How we broadcast and confirm our presigned tx
#:
#: def callback(web3, asset_manager: HexAddress | HotWallet, func: ContractFunction) -> tx hash:
BroadcastCallback: TypeAlias = Callable[[Web3, Any, ContractFunction], HexBytes]


class SigningScheme(IntEnum):
    # The EIP-712 typed data signing scheme. This is the preferred scheme as it
    # provides more infomation to wallets performing the signature on the data
    # being signed.
    #
    # https://github.com/ethereum/EIPs/blob/master/EIPS/eip-712.md#definition-of-domainseparator
    EIP712 = 0b00
    # Message signed using eth_sign RPC call.
    ETHSIGN = 0b01
    # Smart contract signatures as defined in EIP-1271.
    EIP1271 = 0b10
    # Pre-signed order.
    PRESIGN = 0b11


def _default_broadcast_callback(web3: Web3, asset_manager: HexAddress | HotWallet, func: ContractFunction) -> HexBytes:
    """Default broadcast callback which sends the signed transaction and waits for confirmation."""

    if isinstance(asset_manager, HotWallet):
        tx = asset_manager.sign_bound_call_with_new_nonce(func)
        tx_hash = web3.eth.send_raw_transaction(tx.raw_transaction)
    else:
        tx_hash = func.transact({"from": asset_manager})

    assert_transaction_success_with_explanation(web3, tx_hash)
    return tx_hash


class CowAPIError(Exception):
    """Error returned by CowSwap API."""


class GPv2OrderData(TypedDict):
    """See GPv2Order.Data struct in CowSwap contracts.

    Automatically decoded by Web3.py ABI machinery.
    """

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


@dataclass(slots=True, frozen=True)
class CowSwapResult:
    """A full result of a CowSwap order posting and status."""

    order_uid: HexBytes

    #: Order data we constructed for the swap
    order: GPv2OrderData

    #: The final result of the status endpoint
    #:
    #: See https://docs.cow.fi/cow-protocol/reference/apis/orderbook
    status: dict


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
        return "0x" + raw_bytes[offset : offset + 32].hex()

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
    app_data=HexBytes("0xb48d38f93eaa084033fc5970bf96e559c33c4cdc07d889ab00b4d63f9590739d"),
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
        HexBytes(app_data),
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
    final_url = f"{base_url}/api/v1/orders"

    # Javascript cannot  handle ints, so...
    crap_json = order.copy()
    crap_json["buyAmount"] = str(crap_json["buyAmount"])
    crap_json["sellAmount"] = str(crap_json["sellAmount"])
    crap_json["feeAmount"] = str(crap_json["feeAmount"])
    # https://docs.cow.fi/cow-protocol/reference/core/signing-schemes#presign
    # https://github.com/cowdao-grants/cow-py/blob/fd055fd647f56cf92ad0917c08b108a41d2a7e6c/cowdao_cowpy/cow/swap.py#L140
    crap_json["signature"] = "0x"
    crap_json["signingScheme"] = SigningScheme.PRESIGN.name.lower()
    crap_json["appData"] = "{}"  #

    logger.info(f"Posting CowSwap order to {final_url}: %s", pformat(crap_json))

    response = requests.post(
        final_url,
        json=crap_json,
        timeout=api_timeout.total_seconds(),
    )

    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        error_message = response.text
        logger.error(f"Error posting CowSwap order: {error_message}")
        raise CowAPIError(f"Error posting CowSwap order: {response.status_code} {error_message}\nData was:{pformat(crap_json)}\nEndpoint: {final_url}") from e

    return response.json()


def presign_and_broadcast(
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

    data = events[0]["args"]["order"]
    # TODO: appData unsupported for now
    del data["appData"]
    data["sellTokenBalance"] = "erc20"
    data["buyTokenBalance"] = "erc20"
    data["kind"] = "buy"
    data["from"] = vault.safe_address
    data["receiver"] = vault.safe_address
    return data


def wait_order_complete(
    uid: str,
    trade_timeout: datetime.timedelta = datetime.timedelta(minutes=10),
    api_timeout: datetime.timedelta = datetime.timedelta(seconds=60),
    poll_sleep: float = 10.0,
) -> dict:
    """Wait for CowSwap order to complete by polling status endpoint."""

    assert type(uid) == str, f"Expected str uid, got {type(uid)}"
    base_url = get_cowswap_api(chain_id)
    final_url = f"{base_url}/api/v1/orders/{uid}/status"

    #
    # {
    #   "type": "open",
    #   "value": [
    #     {
    #       "solver": "string",
    #       "executedAmounts": {
    #         "sell": "1234567890",
    #         "buy": "1234567890"
    #       }
    #     }
    #   ]
    # }

    started = native_datetime_utc_now()

    deadline = native_datetime_utc_now() + trade_timeout
    logger.info("Fetching order data %s, timeout is %s", final_url, trade_timeout)
    cycle = 0
    while native_datetime_utc_now() < deadline:
        response = requests.get(
            final_url,
            timeout=api_timeout.total_seconds(),
        )

        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            error_message = response.text
            logger.error(f"Error fetching CowSwap order status: {error_message}")
            raise CowAPIError(f"Error fetching CowSwap order status: {response.status_code} {error_message}") from e

        data = response.json()

        # [ presignaturePending, open, fulfilled, cancelled, expired ]
        status = data["status"]
        if status != "open":
            duration = native_datetime_utc_now() - started
            logger.info(f"CowSwap order {uid} completed with status {status} in {duration}")
            return data

        cycle += 1
        logger.info("Waiting for CowSwap to complete cycle %d, order %s still open, sleeping %s...", cycle, uid, poll_sleep)
        time.sleep(poll_sleep)

    raise CowAPIError(f"Timeout waiting for CowSwap order {uid} to complete after {trade_timeout}")


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
    chain_id = vault.web3.eth.chain_id

    order = presign_and_broadcast(
        asset_manager=asset_manager_wallet,
        vault=vault,
        buy_token=buy_token,
        sell_token=sell_token,
        amount_in=amount_in,
        min_amount_out=min_amount_out,
        broadcast_callback=broadcast_callback,
    )

    posted_order_uid = post_order(
        chain_id,
        order=order,
        api_timeout=api_timeout,
    )

    final_status = wait_order_complete(
        posted_order_uid,
        trade_timeout,
        api_timeout=api_timeout,
    )

    return CowSwapResult(
        order_uid=HexBytes(posted_order_uid),
        order=order,
        status=final_status,
    )


def fetch_quote(
    from_: HexAddress | str,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    min_amount_out: Decimal,
    api_timeout: datetime.timedelta = datetime.timedelta(seconds=30),
    price_quality: Literal["fast", "verified", "optional"] = "fast",
) -> dict:
    """Fetch a CowSwap quote for a given token pair and amounts.

    https://docs.cow.fi/cow-protocol/reference/apis/quote
    """

    chain_id = buy_token.chain_id
    base_url = get_cowswap_api(chain_id)
    final_url = f"{base_url}/api/v1/quote"

    # See OrderQuoteRequest
    # https://docs.cow.fi/cow-protocol/reference/apis/orderbook
    params = {
        "from": from_,
        "buyToken": buy_token.address,
        "sellToken": sell_token.address,
        "sellAmountBeforeFee": str(sell_token.convert_to_raw(amount_in)),
        "kind": "sell",
        "buyTokenBalance": "erc20",
        "sellTokenBalance": "erc20",
        "priceQuality": price_quality,
        "onchainOrder": True,
        "signingScheme": "presign",
    }
    response = requests.post(final_url, json=params, timeout=api_timeout.total_seconds())

    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        error_message = response.text
        logger.error(f"Error posting CowSwap order: {error_message}")
        raise CowAPIError(f"Error posting CowSwap order: {response.status_code} {error_message}\nData was:{pformat(params)}\nEndpoint: {final_url}") from e

    return response.json()
