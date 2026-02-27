"""GMX pending orders module.

Fetches pending limit orders (stop loss, take profit, limit increase) from the
GMX Reader contract. These orders sit in the DataStore waiting for trigger price
conditions to be met by keepers, and can be cancelled by the user.

Example::

    from eth_defi.gmx.order.pending_orders import fetch_pending_orders, fetch_pending_order_count

    # Count pending orders first
    count = fetch_pending_order_count(web3, "arbitrum", wallet_address)
    print(f"{count} pending orders")

    # Fetch all pending cancellable orders
    for order in fetch_pending_orders(web3, "arbitrum", wallet_address):
        print(f"{order.order_type.name}: key={order.order_key.hex()} trigger=${order.trigger_price_usd:.2f}")

    # Filter to stop losses only
    for sl in fetch_pending_orders(web3, "arbitrum", wallet_address, order_type_filter=OrderType.STOP_LOSS_DECREASE):
        print(f"SL at ${sl.trigger_price_usd:.2f}")
"""

import logging
from dataclasses import dataclass
from typing import Iterator

from eth_typing import HexAddress
from eth_utils import to_checksum_address
from web3 import Web3

from eth_defi.gmx.constants import PRECISION, OrderType
from eth_defi.gmx.contracts import get_contract_addresses, get_datastore_contract, get_reader_contract
from eth_defi.gmx.keys import account_order_list_key

logger = logging.getLogger(__name__)

#: Order types that can be cancelled by the user.
#: Market orders (MARKET_SWAP, MARKET_INCREASE, MARKET_DECREASE) execute
#: immediately via keepers and are not cancellable.
CANCELLABLE_ORDER_TYPES: frozenset[OrderType] = frozenset(
    {
        OrderType.LIMIT_INCREASE,
        OrderType.LIMIT_DECREASE,
        OrderType.STOP_LOSS_DECREASE,
    }
)


@dataclass(slots=True)
class PendingOrder:
    """A pending GMX limit order waiting to be executed by a keeper.

    Represents a limit order (stop loss, take profit, limit increase) stored
    in the DataStore pending trigger price conditions. These orders can be
    cancelled by the user before they are executed.
    """

    #: Unique 32-byte identifier for this order in the DataStore
    order_key: bytes

    #: GMX order type (LIMIT_INCREASE, LIMIT_DECREASE, STOP_LOSS_DECREASE)
    order_type: OrderType

    #: Address of the account that created this order
    account: HexAddress

    #: Address that receives the output tokens when the order executes
    receiver: HexAddress

    #: Address that receives tokens if the order is cancelled
    cancellation_receiver: HexAddress

    #: Market contract address (index token + long/short collateral pair)
    market: HexAddress

    #: Address of the collateral token provided when the order was created
    initial_collateral_token: HexAddress

    #: Position size delta in USD with 30-decimal precision (``$1 = 10^30``)
    size_delta_usd: int

    #: Initial collateral delta amount in token-native decimals
    initial_collateral_delta_amount: int

    #: Trigger price in 30-decimal precision at which keepers will execute
    trigger_price: int

    #: Acceptable execution price in 30-decimal precision (slippage bound)
    acceptable_price: int

    #: Execution fee in native token wei paid to the keeper on execution
    execution_fee: int

    #: Whether this order is for a long position
    is_long: bool

    #: Whether this order is frozen and cannot be executed until unfrozen
    is_frozen: bool

    #: Whether this order auto-cancels when the associated position is closed
    auto_cancel: bool

    #: Block timestamp when this order was last updated
    updated_at_time: int

    #: Swap path as a list of market addresses for output token routing
    swap_path: list[HexAddress]

    @property
    def trigger_price_usd(self) -> float:
        """Trigger price as a human-readable USD float.

        GMX stores prices with ``10^(30 - token_decimals)`` precision (``PRECISION = 30``).
        This property assumes an **18-decimal index token** (ETH, most ERC-20 alts) and
        therefore divides by ``10^12`` (= ``10^(30 - 18)``).

        .. warning::
            For non-18-decimal index tokens (e.g. WBTC with 8 decimals) this property
            returns a value that is orders of magnitude too small.  Use
            :meth:`trigger_price_usd_for_decimals` with the correct token decimals in
            those cases.

        :return: Approximate trigger price in USD (accurate for 18-decimal tokens only).
        """
        if self.trigger_price == 0:
            return 0.0
        return self.trigger_price / 10**12

    def trigger_price_usd_for_decimals(self, token_decimals: int) -> float:
        """Trigger price in USD for a token with *token_decimals* decimal places.

        GMX stores prices with ``10^(PRECISION - token_decimals)`` precision where
        ``PRECISION = 30``.  Use this method when working with tokens other than
        standard 18-decimal ERC-20s.

        :param token_decimals: Decimal places of the index token (e.g. 8 for WBTC, 18 for ETH).
        :return: Trigger price in USD.
        """
        if self.trigger_price == 0:
            return 0.0
        return self.trigger_price / 10 ** (PRECISION - token_decimals)

    @property
    def size_delta_usd_human(self) -> float:
        """Position size delta as a human-readable USD float.

        GMX stores USD amounts with 30-decimal precision (``$1 = 10^30``).

        :return: Size delta in USD.
        """
        return self.size_delta_usd / 10**PRECISION

    @property
    def is_stop_loss(self) -> bool:
        """Whether this order is a stop loss order.

        :return: ``True`` if ``order_type`` is ``STOP_LOSS_DECREASE``.
        """
        return self.order_type == OrderType.STOP_LOSS_DECREASE

    @property
    def is_take_profit(self) -> bool:
        """Whether this order is a take profit order.

        :return: ``True`` if ``order_type`` is ``LIMIT_DECREASE``.
        """
        return self.order_type == OrderType.LIMIT_DECREASE

    @property
    def is_limit_increase(self) -> bool:
        """Whether this order is a limit increase (entry) order.

        :return: ``True`` if ``order_type`` is ``LIMIT_INCREASE``.
        """
        return self.order_type == OrderType.LIMIT_INCREASE


def _parse_raw_order(raw_order: tuple) -> PendingOrder:
    """Parse a raw ``ReaderUtils.OrderInfo`` tuple into a :class:`PendingOrder`.

    The Reader contract returns ``ReaderUtils.OrderInfo[]`` where each element is:

    - ``[0]`` ``orderKey`` (bytes32)
    - ``[1]`` ``Order.Props`` tuple containing:

      - ``[0]`` ``Order.Addresses``: account, receiver, cancellationReceiver,
        callbackContract, uiFeeReceiver, market, initialCollateralToken, swapPath
      - ``[1]`` ``Order.Numbers``: orderType, decreasePositionSwapType,
        sizeDeltaUsd, initialCollateralDeltaAmount, triggerPrice, acceptablePrice,
        executionFee, callbackGasLimit, minOutputAmount, updatedAtTime,
        validFromTime, srcChainId
      - ``[2]`` ``Order.Flags``: isLong, shouldUnwrapNativeToken, isFrozen, autoCancel
      - ``[3]`` ``_dataList`` (bytes32[])

    :param raw_order:
        Raw tuple returned by ``Reader.getAccountOrders``.
    :return:
        Parsed :class:`PendingOrder` instance.
    """
    order_key: bytes = raw_order[0]
    props: tuple = raw_order[1]

    # Order.Addresses
    addresses: tuple = props[0]
    account = to_checksum_address(addresses[0])
    receiver = to_checksum_address(addresses[1])
    cancellation_receiver = to_checksum_address(addresses[2])
    market = to_checksum_address(addresses[5])
    initial_collateral_token = to_checksum_address(addresses[6])
    swap_path: list[HexAddress] = [to_checksum_address(addr) for addr in addresses[7]]

    # Order.Numbers
    numbers: tuple = props[1]
    order_type_raw: int = int(numbers[0])
    size_delta_usd: int = int(numbers[2])
    initial_collateral_delta_amount: int = int(numbers[3])
    trigger_price: int = int(numbers[4])
    acceptable_price: int = int(numbers[5])
    execution_fee: int = int(numbers[6])
    updated_at_time: int = int(numbers[9])

    # Order.Flags
    flags: tuple = props[2]
    is_long: bool = bool(flags[0])
    is_frozen: bool = bool(flags[2])
    auto_cancel: bool = bool(flags[3])

    return PendingOrder(
        order_key=order_key,
        order_type=OrderType(order_type_raw),
        account=account,
        receiver=receiver,
        cancellation_receiver=cancellation_receiver,
        market=market,
        initial_collateral_token=initial_collateral_token,
        size_delta_usd=size_delta_usd,
        initial_collateral_delta_amount=initial_collateral_delta_amount,
        trigger_price=trigger_price,
        acceptable_price=acceptable_price,
        execution_fee=execution_fee,
        is_long=is_long,
        is_frozen=is_frozen,
        auto_cancel=auto_cancel,
        updated_at_time=updated_at_time,
        swap_path=swap_path,
    )


def fetch_pending_orders(
    web3: Web3,
    chain: str,
    account: HexAddress,
    order_type_filter: OrderType | None = None,
    market_filter: HexAddress | None = None,
    is_long_filter: bool | None = None,
) -> Iterator[PendingOrder]:
    """Fetch pending cancellable limit orders for an account from the GMX Reader.

    Only yields cancellable order types:

    - ``LIMIT_INCREASE`` (3) — pending entry orders
    - ``LIMIT_DECREASE`` (5) — take profit orders
    - ``STOP_LOSS_DECREASE`` (6) — stop loss orders

    Market orders execute immediately and are excluded from results.

    :param web3:
        Web3 instance connected to the target chain.
    :param chain:
        Network name (e.g. ``"arbitrum"``, ``"avalanche"``).
    :param account:
        Wallet address to query pending orders for.
    :param order_type_filter:
        If provided, only yield orders of this specific type.
    :param market_filter:
        If provided, only yield orders for this market contract address.
    :param is_long_filter:
        If provided, only yield orders matching this direction.
    :return:
        Iterator of :class:`PendingOrder` instances matching the filters.
    """
    order_count = fetch_pending_order_count(web3, chain, account)

    if order_count == 0:
        logger.debug("No pending orders for account %s on %s", account, chain)
        return

    logger.info(
        "Fetching %d pending order(s) for account %s on %s",
        order_count,
        account,
        chain,
    )

    contract_addresses = get_contract_addresses(chain)
    reader = get_reader_contract(web3, chain)
    checksum_account = to_checksum_address(account)

    raw_orders: list[tuple] = reader.functions.getAccountOrders(
        contract_addresses.datastore,
        checksum_account,
        0,
        order_count,
    ).call()

    logger.debug(
        "Reader returned %d raw order(s) for account %s",
        len(raw_orders),
        account,
    )

    for raw_order in raw_orders:
        try:
            order = _parse_raw_order(raw_order)
        except (ValueError, KeyError) as exc:
            logger.warning(
                "Failed to parse raw order, skipping: %s",
                exc,
            )
            continue

        if order.order_type not in CANCELLABLE_ORDER_TYPES:
            continue

        if order_type_filter is not None and order.order_type != order_type_filter:
            continue

        if market_filter is not None and order.market.lower() != market_filter.lower():
            continue

        if is_long_filter is not None and order.is_long != is_long_filter:
            continue

        logger.debug(
            "Pending %s order key=%s market=%s trigger=$%.2f is_long=%s",
            order.order_type.name,
            order.order_key.hex(),
            order.market,
            order.trigger_price_usd,
            order.is_long,
        )

        yield order


def fetch_pending_order_count(
    web3: Web3,
    chain: str,
    account: HexAddress,
) -> int:
    """Fetch the number of pending orders for an account from the DataStore.

    Lightweight check that only calls ``DataStore.getBytes32Count`` without
    fetching full order details. Useful as a quick pre-check before calling
    :func:`fetch_pending_orders`.

    :param web3:
        Web3 instance connected to the target chain.
    :param chain:
        Network name (e.g. ``"arbitrum"``, ``"avalanche"``).
    :param account:
        Wallet address to count pending orders for.
    :return:
        Number of pending orders stored in the DataStore for this account.
    """
    datastore = get_datastore_contract(web3, chain)
    checksum_account = to_checksum_address(account)
    set_key = account_order_list_key(checksum_account)

    count: int = datastore.functions.getBytes32Count(set_key).call()

    logger.debug(
        "Account %s has %d pending order(s) on %s",
        account,
        count,
        chain,
    )

    return count
