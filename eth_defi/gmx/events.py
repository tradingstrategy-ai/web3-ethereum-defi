"""GMX event log decoding and parsing.

This module provides utilities for decoding GMX protocol events from transaction
receipts. GMX emits all events through a centralised EventEmitter contract using
EventLog, EventLog1, and EventLog2 event types.

Key features:

- Decode complex EventLogData structure from transaction logs
- Extract order execution results (success/failure/frozen)
- Parse position increase/decrease events with execution prices, PnL, and fees
- Decode error reasons from failed orders

Example usage:

.. code-block:: python

    from web3 import Web3
    from eth_defi.gmx.events import (
        decode_gmx_events,
        extract_order_execution_result,
    )

    # Get transaction receipt
    receipt = web3.eth.get_transaction_receipt(tx_hash)

    # Decode all GMX events from receipt
    events = list(decode_gmx_events(web3, receipt))

    # Extract order execution result
    result = extract_order_execution_result(web3, receipt)
    if result:
        print(f"Order status: {result.status}")
        print(f"Execution price: {result.execution_price}")

For more information on GMX events, see:
https://docs.gmx.io/docs/api/contracts#event-monitoring
"""

import logging
from dataclasses import dataclass, field
from typing import Iterator, Literal

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract
from eth_utils import keccak

from eth_defi.gmx.constants import GMX_EVENT_EMITTER_ABI
from eth_defi.gmx.contracts import get_contract_addresses


logger = logging.getLogger(__name__)


def get_event_name_hash(event_name: str) -> str:
    """Compute the keccak256 hash of an event name.

    GMX uses the hash of event name strings as topic[1] in EventLog events
    for efficient filtering.

    :param event_name:
        The event name (e.g., "OrderCreated", "OrderExecuted")

    :return:
        The keccak256 hash as hex string without 0x prefix
    """
    hash_bytes = keccak(text=event_name)
    # bytes.hex() returns hex string WITHOUT 0x prefix
    return hash_bytes.hex()


#: GMX price precision (prices are stored with 12 decimal places)
GMX_PRICE_PRECISION = 10**12

#: GMX USD amount precision (USD values use 30 decimal places)
GMX_USD_PRECISION = 10**30

#: Common GMX event names for reference
GMX_EVENT_NAMES = {
    "OrderCreated",
    "OrderExecuted",
    "OrderFrozen",
    "OrderCancelled",
    "OrderUpdated",
    "PositionIncrease",
    "PositionDecrease",
    "DepositCreated",
    "DepositExecuted",
    "WithdrawalCreated",
    "WithdrawalExecuted",
}


@dataclass(slots=True)
class GMXEventData:
    """Parsed GMX event data from EventLogData structure.

    GMX events contain structured data in the following categories:

    - Address items: Contract addresses (account, market, tokens)
    - Uint items: Unsigned integers (sizes, prices, fees)
    - Int items: Signed integers (PnL, price impact)
    - Bool items: Boolean flags (isLong, etc.)
    - Bytes32 items: Order/position keys
    - Bytes items: Raw byte data (error reasons)
    - String items: String data (reasons)

    Each category has both single items and array items.
    """

    #: The event name (e.g., "OrderCreated", "OrderExecuted")
    event_name: str

    #: The message sender (usually a GMX contract)
    msg_sender: HexAddress | None = None

    #: Topic1 from EventLog1/EventLog2 (often order key)
    topic1: bytes | None = None

    #: Topic2 from EventLog2 (often account address as bytes32)
    topic2: bytes | None = None

    #: Single address values keyed by name
    address_items: dict[str, HexAddress] = field(default_factory=dict)

    #: Array address values keyed by name
    address_array_items: dict[str, list[HexAddress]] = field(default_factory=dict)

    #: Single uint256 values keyed by name
    uint_items: dict[str, int] = field(default_factory=dict)

    #: Array uint256 values keyed by name
    uint_array_items: dict[str, list[int]] = field(default_factory=dict)

    #: Single int256 values keyed by name
    int_items: dict[str, int] = field(default_factory=dict)

    #: Array int256 values keyed by name
    int_array_items: dict[str, list[int]] = field(default_factory=dict)

    #: Single bool values keyed by name
    bool_items: dict[str, bool] = field(default_factory=dict)

    #: Array bool values keyed by name
    bool_array_items: dict[str, list[bool]] = field(default_factory=dict)

    #: Single bytes32 values keyed by name
    bytes32_items: dict[str, bytes] = field(default_factory=dict)

    #: Array bytes32 values keyed by name
    bytes32_array_items: dict[str, list[bytes]] = field(default_factory=dict)

    #: Single bytes values keyed by name
    bytes_items: dict[str, bytes] = field(default_factory=dict)

    #: Array bytes values keyed by name
    bytes_array_items: dict[str, list[bytes]] = field(default_factory=dict)

    #: Single string values keyed by name
    string_items: dict[str, str] = field(default_factory=dict)

    #: Array string values keyed by name
    string_array_items: dict[str, list[str]] = field(default_factory=dict)

    def get_address(self, key: str, default: HexAddress | None = None) -> HexAddress | None:
        """Get an address item by key."""
        return self.address_items.get(key, default)

    def get_uint(self, key: str, default: int | None = None) -> int | None:
        """Get a uint item by key."""
        return self.uint_items.get(key, default)

    def get_int(self, key: str, default: int | None = None) -> int | None:
        """Get an int item by key."""
        return self.int_items.get(key, default)

    def get_bool(self, key: str, default: bool | None = None) -> bool | None:
        """Get a bool item by key."""
        return self.bool_items.get(key, default)

    def get_bytes32(self, key: str, default: bytes | None = None) -> bytes | None:
        """Get a bytes32 item by key."""
        return self.bytes32_items.get(key, default)

    def get_bytes(self, key: str, default: bytes | None = None) -> bytes | None:
        """Get a bytes item by key."""
        return self.bytes_items.get(key, default)

    def get_string(self, key: str, default: str | None = None) -> str | None:
        """Get a string item by key."""
        return self.string_items.get(key, default)


@dataclass(slots=True)
class OrderFees:
    """Fees from GMX order execution.

    All fee values are in token amounts (not USD).
    """

    #: Position fee amount
    position_fee: int = 0

    #: Borrowing fee amount
    borrowing_fee: int = 0

    #: Funding fee amount
    funding_fee: int = 0

    #: Liquidation fee amount (if applicable)
    liquidation_fee: int = 0


@dataclass(slots=True)
class OrderExecutionResult:
    """Result of GMX order execution.

    This dataclass aggregates information from order execution events
    (OrderExecuted, OrderFrozen, OrderCancelled) and position events
    (PositionIncrease, PositionDecrease).
    """

    #: The order key (32-byte identifier)
    order_key: bytes

    #: Execution status
    status: Literal["executed", "frozen", "cancelled"]

    #: Account address that owns the order
    account: HexAddress | None = None

    #: Execution price (30 decimal precision)
    execution_price: int | None = None

    #: Size delta in USD (30 decimal precision)
    size_delta_usd: int | None = None

    #: Size delta in tokens
    size_delta_in_tokens: int | None = None

    #: Collateral delta amount (can be negative for decreases)
    collateral_delta: int | None = None

    #: Realised PnL in USD (30 decimal precision, for decrease orders)
    pnl_usd: int | None = None

    #: Price impact in USD (30 decimal precision)
    price_impact_usd: int | None = None

    #: Execution fees
    fees: OrderFees | None = None

    #: Error reason string (for frozen/cancelled orders)
    reason: str | None = None

    #: Raw error reason bytes (for frozen/cancelled orders)
    reason_bytes: bytes | None = None

    #: Decoded error message from reason_bytes
    decoded_error: str | None = None

    #: Position key (if position was modified)
    position_key: bytes | None = None

    #: Whether the position is long
    is_long: bool | None = None


def _get_chain_name_from_id(chain_id: int) -> str:
    """Get chain name from chain ID.

    :param chain_id:
        The chain ID

    :return:
        Chain name for GMX contracts

    :raises ValueError:
        If chain ID is not supported
    """
    chain_id_to_name = {
        42161: "arbitrum",
        43114: "avalanche",
        421614: "arbitrum_sepolia",
    }
    if chain_id not in chain_id_to_name:
        raise ValueError(f"Unknown chain ID {chain_id} for GMX EventEmitter")
    return chain_id_to_name[chain_id]


def _get_event_emitter_contract(web3: Web3, chain_name: str | None = None) -> Contract:
    """Get the EventEmitter contract instance.

    :param web3:
        Web3 instance

    :param chain_name:
        Chain name (arbitrum, avalanche, arbitrum_sepolia). If None, auto-detects.

    :return:
        EventEmitter contract instance
    """
    if chain_name is None:
        chain_id = web3.eth.chain_id
        chain_name = _get_chain_name_from_id(chain_id)

    # Get address dynamically from GMX contracts registry
    contract_addresses = get_contract_addresses(chain_name)
    address = contract_addresses.eventemitter

    return web3.eth.contract(
        address=Web3.to_checksum_address(address),
        abi=GMX_EVENT_EMITTER_ABI,
    )


def _parse_event_data_items(items: list, is_array: bool = False) -> dict:
    """Parse EventLogData items array into a dictionary.

    :param items:
        List of items from decoded event. Each item can be:
        - A tuple/list: (key, value)
        - An AttributeDict: {'key': key, 'value': value}

    :param is_array:
        Whether these are arrayItems (value is a list)

    :return:
        Dictionary mapping key to value
    """
    result = {}
    for item in items:
        # Handle both tuple and AttributeDict access
        if hasattr(item, "get"):
            # AttributeDict from web3.py
            key = item.get("key")
            value = item.get("value")
        else:
            # Tuple/list access
            key = item[0]
            value = item[1]

        if key is None:
            continue

        # Convert bytes to proper format
        if isinstance(value, bytes):
            result[key] = value
        elif isinstance(value, (list, tuple)) and is_array:
            # For array items, value is already a list
            result[key] = list(value)
        else:
            result[key] = value

    return result


def _parse_event_log_data(event_data) -> dict:
    """Parse the EventLogData into categorised dictionaries.

    The EventLogData structure has 7 categories, each with items and arrayItems:
    addressItems, uintItems, intItems, boolItems, bytes32Items, bytesItems, stringItems

    Web3.py returns this as an AttributeDict with named keys.
    """
    parsed = {
        "address_items": {},
        "address_array_items": {},
        "uint_items": {},
        "uint_array_items": {},
        "int_items": {},
        "int_array_items": {},
        "bool_items": {},
        "bool_array_items": {},
        "bytes32_items": {},
        "bytes32_array_items": {},
        "bytes_items": {},
        "bytes_array_items": {},
        "string_items": {},
        "string_array_items": {},
    }

    if not event_data:
        return parsed

    # Category mapping: (python_name, solidity_name)
    categories = [
        ("address", "addressItems"),
        ("uint", "uintItems"),
        ("int", "intItems"),
        ("bool", "boolItems"),
        ("bytes32", "bytes32Items"),
        ("bytes", "bytesItems"),
        ("string", "stringItems"),
    ]

    for py_name, sol_name in categories:
        # Get category data by name (AttributeDict from web3.py)
        category_data = None
        if hasattr(event_data, "get"):
            category_data = event_data.get(sol_name)
        elif hasattr(event_data, "__getitem__"):
            # Fallback for tuple-based access
            try:
                idx = [c[0] for c in categories].index(py_name)
                category_data = event_data[idx]
            except (IndexError, KeyError):
                pass

        if not category_data:
            continue

        # Get items and arrayItems
        items = None
        array_items = None

        if hasattr(category_data, "get"):
            # AttributeDict access
            items = category_data.get("items", [])
            array_items = category_data.get("arrayItems", [])
        elif hasattr(category_data, "__getitem__") and len(category_data) >= 2:
            # Tuple access
            items = category_data[0]
            array_items = category_data[1]

        if items:
            parsed[f"{py_name}_items"] = _parse_event_data_items(items, is_array=False)
        if array_items:
            parsed[f"{py_name}_array_items"] = _parse_event_data_items(array_items, is_array=True)

    return parsed


def decode_gmx_event(web3: Web3, log: dict) -> GMXEventData | None:
    """Decode a single GMX EventLog from a transaction log entry.

    This function handles EventLog, EventLog1, and EventLog2 events
    emitted by the GMX EventEmitter contract.

    :param web3:
        Web3 instance

    :param log:
        A single log entry from transaction receipt

    :return:
        Parsed GMXEventData or None if not a GMX event
    """
    event_emitter = _get_event_emitter_contract(web3)

    # Get event signatures for EventLog, EventLog1, EventLog2
    event_log_sig = keccak(
        text="EventLog(address,string,string,(((string,address)[],(string,address[])[]),((string,uint256)[],(string,uint256[])[]),((string,int256)[],(string,int256[])[]),((string,bool)[],(string,bool[])[]),((string,bytes32)[],(string,bytes32[])[]),((string,bytes)[],(string,bytes[])[]),((string,string)[],(string,string[])[])))",
    ).hex()
    event_log1_sig = keccak(
        text="EventLog1(address,string,string,bytes32,(((string,address)[],(string,address[])[]),((string,uint256)[],(string,uint256[])[]),((string,int256)[],(string,int256[])[]),((string,bool)[],(string,bool[])[]),((string,bytes32)[],(string,bytes32[])[]),((string,bytes)[],(string,bytes[])[]),((string,string)[],(string,string[])[])))",
    ).hex()
    event_log2_sig = keccak(
        text="EventLog2(address,string,string,bytes32,bytes32,(((string,address)[],(string,address[])[]),((string,uint256)[],(string,uint256[])[]),((string,int256)[],(string,int256[])[]),((string,bool)[],(string,bool[])[]),((string,bytes32)[],(string,bytes32[])[]),((string,bytes)[],(string,bytes[])[]),((string,string)[],(string,string[])[])))",
    ).hex()

    topics = log.get("topics", [])
    if not topics:
        return None

    # Get the first topic (event signature)
    first_topic = topics[0]
    if isinstance(first_topic, bytes):
        first_topic = first_topic.hex()
    elif first_topic.startswith("0x"):
        first_topic = first_topic[2:]

    first_topic = first_topic.lower()

    # Determine event type
    # Note: keccak().hex() returns hex without 0x prefix, so no stripping needed
    event_type = None
    if first_topic == event_log_sig.lower():
        event_type = "EventLog"
    elif first_topic == event_log1_sig.lower():
        event_type = "EventLog1"
    elif first_topic == event_log2_sig.lower():
        event_type = "EventLog2"

    if not event_type:
        return None

    try:
        # Get the appropriate event from ABI
        event = getattr(event_emitter.events, event_type)
        decoded = event().process_log(log)

        args = decoded["args"]
        event_name = args.get("eventName", "")
        msg_sender = args.get("msgSender")
        event_data = args.get("eventData", ())

        # Parse topics
        topic1 = None
        topic2 = None
        if event_type in ("EventLog1", "EventLog2"):
            topic1 = args.get("topic1")
            if isinstance(topic1, bytes):
                pass  # Already bytes
            elif isinstance(topic1, str):
                topic1 = bytes.fromhex(topic1[2:] if topic1.startswith("0x") else topic1)
            elif isinstance(topic1, int):
                topic1 = topic1.to_bytes(32, "big")

        if event_type == "EventLog2":
            topic2 = args.get("topic2")
            if isinstance(topic2, bytes):
                pass
            elif isinstance(topic2, str):
                topic2 = bytes.fromhex(topic2[2:] if topic2.startswith("0x") else topic2)
            elif isinstance(topic2, int):
                topic2 = topic2.to_bytes(32, "big")

        # Parse event data
        parsed = _parse_event_log_data(event_data)

        return GMXEventData(
            event_name=event_name,
            msg_sender=msg_sender,
            topic1=topic1,
            topic2=topic2,
            **parsed,
        )

    except Exception as e:
        logger.warning("Failed to decode GMX event: %s", e)
        return None


def decode_gmx_events(web3: Web3, receipt: dict) -> Iterator[GMXEventData]:
    """Decode all GMX events from a transaction receipt.

    :param web3:
        Web3 instance

    :param receipt:
        Transaction receipt dictionary

    :yields:
        Parsed GMXEventData for each GMX event found
    """
    logs = receipt.get("logs", [])

    for log in logs:
        event = decode_gmx_event(web3, log)
        if event:
            yield event


def find_events_by_name(
    web3: Web3,
    receipt: dict,
    event_name: str,
) -> Iterator[GMXEventData]:
    """Find all events with a specific name from a transaction receipt.

    :param web3:
        Web3 instance

    :param receipt:
        Transaction receipt dictionary

    :param event_name:
        The event name to filter for (e.g., "OrderExecuted")

    :yields:
        Matching GMXEventData events
    """
    for event in decode_gmx_events(web3, receipt):
        if event.event_name == event_name:
            yield event


#: GMX error selectors (computed from keccak256("ErrorName(types...)")[:4])
#: Maps 4-byte selector hex string to (error_name, parameter_types) tuple
#: See: https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/error/Errors.sol
GMX_ERROR_SELECTORS: dict[str, tuple[str, list[str]]] = {
    # Order errors
    "9fbe2cbc": ("InvalidDecreaseOrderSize", ["uint256", "uint256"]),
    "e09ad0e9": ("OrderNotFulfillableAtAcceptablePrice", ["uint256", "uint256"]),
    "3784f834": ("UnsupportedOrderType", ["uint256"]),
    "59485ed9": ("OrderNotFound", ["bytes32"]),
    "730d44b1": ("OrderAlreadyFrozen", []),
    "30779725": ("EmptyOrder", []),
    "feddc084": ("InvalidKeeperForFrozenOrder", ["address"]),
    "f4253177": ("EmptySizeDeltaInTokens", []),
    "9319d603": ("OrderValidFromTimeNotReached", ["uint256", "uint256"]),
    "794a604a": ("MaxAutoCancelOrdersExceeded", ["uint256", "uint256"]),
    "0481a15a": ("InvalidOrderPrices", ["uint256", "uint256", "uint256", "uint256"]),
    # Position errors
    "4dfbbff3": ("EmptyPosition", []),
    "426cfff0": ("PositionNotFound", ["bytes32"]),
    "85efb31a": ("MinPositionSize", ["uint256", "uint256"]),
    "bff65b3f": ("InvalidPositionSizeValues", ["uint256", "uint256"]),
    "10811ceb": ("InvalidPositionMarket", []),
    "74cc815b": ("InsufficientCollateralAmount", ["uint256", "int256"]),
    "2159b161": ("InsufficientCollateralUsd", ["int256"]),
    "3a61a4a9": ("UnableToWithdrawCollateral", ["int256"]),
    "12110872": ("LiquidatablePosition", ["string", "int256", "int256", "int256"]),
    "9c693e4e": ("InvalidCollateralTokenForMarket", ["address", "address"]),
    "919dd98a": ("PositionShouldNotBeLiquidated", ["string", "int256", "int256", "int256"]),
    "be2cbc10": ("InvalidDecreaseOrderSize", ["uint256", "uint256"]),
    # Price/execution errors
    "cc32db99": ("NegativeExecutionPrice", ["int256", "uint256", "uint256", "int256", "uint256"]),
    "f0641c92": ("PriceImpactLargerThanOrderSize", ["int256", "uint256"]),
    "6514b64e": ("InvalidFeedPrice", ["address", "int256"]),
    "677abf1c": ("OracleTimestampsAreSmallerThanRequired", ["uint256", "uint256"]),
    # Pool/reserve errors
    "23090a31": ("InsufficientPoolAmount", ["uint256", "uint256"]),
    "315276c9": ("InsufficientReserve", ["uint256", "uint256"]),
    "109ef850": ("MaxLongExceeded", ["uint256", "uint256"]),
    "5ba53cd3": ("MaxShortExceeded", ["uint256", "uint256"]),
    "2bf127cf": ("MaxOpenInterestExceeded", ["uint256", "uint256"]),
    "29ff3fc8": ("MaxPoolAmountExceeded", ["uint256", "uint256"]),
    "a942ab62": ("MaxCollateralSumExceeded", ["uint256", "uint256"]),
    "169f0412": ("MaxPoolUsdForDepositExceeded", ["uint256", "uint256"]),
    "8c617982": ("InsufficientReserveForOpenInterest", ["uint256", "uint256"]),
    "f8c937db": ("DisabledMarket", ["address"]),
    # Output/swap errors
    "d28d3eb5": ("InsufficientOutputAmount", ["uint256", "uint256"]),
    "a7aebadc": ("InsufficientSwapOutputAmount", ["uint256", "uint256"]),
    "75885d69": ("SwapPriceImpactExceedsAmountIn", ["uint256", "int256"]),
    "f817118e": ("InvalidTokenIn", ["address", "address"]),
    "c78b78fa": ("DuplicatedMarketInSwapPath", ["address"]),
    # Token/market errors
    "6ce23460": ("MinMarketTokens", ["uint256", "uint256"]),
    "f442c0bc": ("MinLongTokens", ["uint256", "uint256"]),
    "b4a196af": ("MinShortTokens", ["uint256", "uint256"]),
    "e234604a": ("MinMarketTokens", ["uint256", "uint256"]),
    "42c0bc84": ("MinLongTokens", ["uint256", "uint256"]),
    "a196af45": ("MinShortTokens", ["uint256", "uint256"]),
    # Oracle errors
    "cd64a025": ("EmptyPrimaryPrice", ["address"]),
    "2b6e7c3f": ("MaxPriceAgeExceeded", ["uint256", "uint256"]),
    # Gas/execution errors
    "ac504dbb": ("InsufficientExecutionFee", ["uint256", "uint256"]),
    "78cd7e7a": ("InsufficientWntAmountForExecutionFee", ["uint256", "uint256"]),
    "416f9306": ("InsufficientExecutionGas", ["uint256", "uint256", "uint256"]),
    # Standard revert
    "08c379a0": ("Error", ["string"]),
}


def decode_error_reason(reason_bytes: bytes) -> str | None:
    """Decode GMX error reason from reasonBytes.

    GMX uses custom error selectors. This function attempts to decode
    common error types and their parameters.

    :param reason_bytes:
        The raw reasonBytes from OrderFrozen/OrderCancelled events

    :return:
        Decoded error message with parameters, or None if cannot decode
    """
    if not reason_bytes or len(reason_bytes) < 4:
        return None

    # Extract the 4-byte selector
    selector = reason_bytes[:4].hex()

    error_info = GMX_ERROR_SELECTORS.get(selector)
    if error_info:
        error_name, param_types = error_info

        # Try to decode parameters
        if param_types and len(reason_bytes) > 4:
            try:
                params = _decode_error_params(reason_bytes[4:], param_types)
                if params:
                    # Format params nicely
                    param_strs = []
                    for i, (ptype, value) in enumerate(zip(param_types, params)):
                        if ptype == "uint256":
                            # For USD values, try to format nicely
                            if value > 10**25:
                                # Likely a USD value with 30 decimals
                                param_strs.append(f"${value / GMX_USD_PRECISION:,.4f}")
                            elif value > 10**10:
                                # Possibly a price with 12 decimals
                                param_strs.append(f"${value / GMX_PRICE_PRECISION:,.2f}")
                            else:
                                param_strs.append(str(value))
                        elif ptype == "int256":
                            if abs(value) > 10**25:
                                param_strs.append(f"${value / GMX_USD_PRECISION:,.4f}")
                            else:
                                param_strs.append(str(value))
                        elif ptype == "address":
                            param_strs.append(f"0x{value[-40:]}")
                        elif ptype == "bytes32":
                            param_strs.append(f"0x{value.hex()[:16]}...")
                        elif ptype == "string":
                            param_strs.append(f'"{value}"')
                        else:
                            param_strs.append(str(value))

                    return f"{error_name}({', '.join(param_strs)})"
            except Exception as e:
                logger.debug("Failed to decode error params: %s", e)

        return error_name

    return f"Unknown error (selector: 0x{selector})"


def _decode_error_params(data: bytes, param_types: list[str]) -> list | None:
    """Decode error parameters from ABI-encoded data.

    :param data:
        ABI-encoded parameter data (without selector)

    :param param_types:
        List of parameter types (e.g., ["uint256", "uint256"])

    :return:
        List of decoded values, or None if decoding fails
    """
    if not data:
        return None

    values = []
    offset = 0

    for ptype in param_types:
        if offset + 32 > len(data):
            break

        chunk = data[offset : offset + 32]

        if ptype == "uint256":
            values.append(int.from_bytes(chunk, "big"))
        elif ptype == "int256":
            value = int.from_bytes(chunk, "big")
            # Handle two's complement for negative values
            if value >= 2**255:
                value -= 2**256
            values.append(value)
        elif ptype == "address":
            values.append(chunk[-20:].hex())
        elif ptype == "bytes32":
            values.append(chunk)
        elif ptype == "string":
            # String is dynamic, needs special handling
            str_offset = int.from_bytes(chunk, "big")
            if str_offset + 32 <= len(data):
                str_len = int.from_bytes(data[str_offset : str_offset + 32], "big")
                if str_offset + 32 + str_len <= len(data):
                    values.append(data[str_offset + 32 : str_offset + 32 + str_len].decode("utf-8", errors="replace"))
                else:
                    values.append("<truncated>")
            else:
                values.append("<invalid offset>")
        else:
            values.append(chunk)

        offset += 32

    return values if values else None


def extract_order_execution_result(
    web3: Web3,
    receipt: dict,
    order_key: bytes | None = None,
) -> OrderExecutionResult | None:
    """Extract order execution result from a keeper transaction receipt.

    This function looks for OrderExecuted, OrderFrozen, or OrderCancelled
    events and extracts the relevant execution data. For successful orders,
    it also extracts PositionIncrease/PositionDecrease data.

    :param web3:
        Web3 instance

    :param receipt:
        Transaction receipt from keeper execution

    :param order_key:
        Optional order key to filter for. If not provided, returns the
        first order event found.

    :return:
        OrderExecutionResult or None if no order events found
    """
    result = None

    # First, look for order status events
    for event in decode_gmx_events(web3, receipt):
        # Check if this event matches our order key (if specified)
        event_order_key = event.get_bytes32("key") or event.topic1
        if order_key and event_order_key != order_key:
            continue

        if event.event_name == "OrderExecuted":
            result = OrderExecutionResult(
                order_key=event_order_key or b"",
                status="executed",
                account=event.get_address("account"),
            )
            break

        elif event.event_name == "OrderFrozen":
            reason = event.get_string("reason")
            reason_bytes = event.get_bytes("reasonBytes")
            decoded_error = decode_error_reason(reason_bytes) if reason_bytes else None

            result = OrderExecutionResult(
                order_key=event_order_key or b"",
                status="frozen",
                account=event.get_address("account"),
                reason=reason,
                reason_bytes=reason_bytes,
                decoded_error=decoded_error,
            )
            break

        elif event.event_name == "OrderCancelled":
            reason = event.get_string("reason")
            reason_bytes = event.get_bytes("reasonBytes")
            decoded_error = decode_error_reason(reason_bytes) if reason_bytes else None

            result = OrderExecutionResult(
                order_key=event_order_key or b"",
                status="cancelled",
                account=event.get_address("account"),
                reason=reason,
                reason_bytes=reason_bytes,
                decoded_error=decoded_error,
            )
            break

    if not result:
        return None

    # For executed orders, try to find position event data
    if result.status == "executed":
        for event in decode_gmx_events(web3, receipt):
            if event.event_name in ("PositionIncrease", "PositionDecrease"):
                # Check order key matches
                event_order_key = event.get_bytes32("orderKey")
                if order_key and event_order_key != order_key:
                    continue

                # Extract execution data
                result.execution_price = event.get_uint("executionPrice")
                result.size_delta_usd = event.get_uint("sizeDeltaUsd")
                result.size_delta_in_tokens = event.get_uint("sizeDeltaInTokens")
                result.position_key = event.get_bytes32("positionKey")
                result.is_long = event.get_bool("isLong")
                result.price_impact_usd = event.get_int("priceImpactUsd")

                # Collateral delta
                result.collateral_delta = event.get_int("collateralDeltaAmount")

                # For decrease orders, get PnL
                if event.event_name == "PositionDecrease":
                    result.pnl_usd = event.get_int("basePnlUsd")

                # Note: Fees are extracted from PositionFeesCollected event below
                break

        # Extract fees from PositionFeesCollected event (GMX V2)
        # Fees are emitted in a separate event, not in PositionIncrease/Decrease
        for event in decode_gmx_events(web3, receipt):
            if event.event_name == "PositionFeesCollected":
                # Check order key matches
                event_order_key = event.get_bytes32("orderKey")
                if order_key and event_order_key != order_key:
                    continue

                # Extract fees (all fees are combined in positionFeeAmount in GMX V2)
                # The event also has borrowingFeeAmount and fundingFeeAmount fields
                result.fees = OrderFees(
                    position_fee=event.get_uint("positionFeeAmount") or 0,
                    borrowing_fee=event.get_uint("borrowingFeeAmount") or 0,
                    funding_fee=event.get_uint("fundingFeeAmount") or 0,
                    liquidation_fee=event.get_uint("liquidationFeeAmount") or 0,
                )
                break

    return result


def extract_order_key_from_receipt(web3: Web3, receipt: dict) -> bytes:
    """Extract order key from OrderCreated or OrderExecuted event in receipt.

    This function handles both GMX order execution models:

    - **Two-phase orders** (limit orders): Look for OrderCreated event,
      order is pending until keeper executes it in a separate transaction.

    - **Single-phase orders** (market orders): Look for OrderExecuted event,
      order is created and executed atomically in the same transaction.

    :param web3:
        Web3 instance

    :param receipt:
        Transaction receipt from order creation/execution

    :return:
        The 32-byte order key

    :raises ValueError:
        If no OrderCreated or OrderExecuted event found in receipt
    """
    # First try OrderCreated (two-phase orders like limit orders)
    for event in find_events_by_name(web3, receipt, "OrderCreated"):
        # The order key is in topic1 for EventLog2
        if event.topic1:
            return event.topic1

        # Or it might be in the bytes32 items
        key = event.get_bytes32("key")
        if key:
            return key

    # Then try OrderExecuted (single-phase immediate execution)
    # GMX market orders execute atomically - no separate OrderCreated event
    for event in find_events_by_name(web3, receipt, "OrderExecuted"):
        # The order key is in topic1 for EventLog2
        if event.topic1:
            return event.topic1

        # Or it might be in the bytes32 items
        key = event.get_bytes32("key")
        if key:
            return key

    raise ValueError(
        "Could not extract order key from receipt - no OrderCreated or OrderExecuted event found",
    )
