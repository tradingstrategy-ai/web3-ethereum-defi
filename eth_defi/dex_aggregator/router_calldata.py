"""Identify DEX aggregator routers and decode the user's slippage bound from their calldata.

Retail swaps rarely call a liquidity venue directly. A typical Base transaction
looks like ``EntryPoint → smart wallet → aggregator router → adapter → venue``.
The aggregator router frame is the one that carries the user's order: source
and destination token, input amount and the minimum output the user accepted
(their slippage tolerance applied to the aggregator's quote).

This module works on a ``debug_traceTransaction`` ``callTracer`` call path.
It labels the contracts on the path and decodes the first known aggregator
router call into a :py:class:`RouterOrder`.

Supported routers on Base (chain id 8453), ABIs under ``eth_defi/abi/dex_aggregator/``:

- OKX DEX ``DexRouter`` — https://web3.okx.com/build/dev-docs/dex-api/dex-smart-contract
- KyberSwap ``MetaAggregationRouterV2`` — https://docs.kyberswap.com/kyberswap-solutions/kyberswap-aggregator/contracts/aggregator-contract-addresses
- 1inch ``AggregationRouterV6`` and ``V5`` — https://portal.1inch.dev/documentation/contracts/aggregation-protocol/aggregation-introduction
- Paraswap (Velora) ``AugustusV6.2`` — https://developers.velora.xyz/augustus-swapper/augustus-v6.2
- 0x ``Settler`` behind ``AllowanceHolder`` — https://0x.org/docs/introduction/0x-cheat-sheet
- An unnamed verified ``Aggregator`` proxy at ``0x2f68…cfF7`` (``swapExactIn``)

Routers that are only labelled, not decoded: CoW Protocol settlement (batch
auction, per-order limits), LI.FI diamond (wraps other aggregators), Relay
approval proxy (wraps 0x), Binance Web3 Wallet router (unverified facets).

Example::

    from eth_defi.dex_aggregator.router_calldata import identify_call_path

    frames = [(frame["to"], frame["input"]) for frame in path_from_root_to_venue]
    ident = identify_call_path(frames)
    if ident.order:
        print(ident.order.aggregator, ident.order.min_amount_out)
"""

import json
import logging
from dataclasses import asdict, dataclass, field

import eth_abi
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_abi_by_filename

logger = logging.getLogger(__name__)

#: Router address (lowercase) → (aggregator slug, ABI file under eth_defi/abi/dex_aggregator/)
AGGREGATOR_ROUTERS: dict[str, tuple[str, str]] = {
    "0x67d03631fe51b741c0c00c4e16eb662ac84381df": ("okx", "OKXDexRouter.json"),
    "0x6131b5fae19ea4f9d964eac0408e4408b66337b5": ("kyberswap", "KyberSwapMetaAggregationRouterV2.json"),
    "0x111111125421ca6dc452d289314280a0f8842a65": ("1inch", "OneInchAggregationRouterV6.json"),
    "0x1111111254eeb25477b68fb85ed929f73a960582": ("1inch", "OneInchAggregationRouterV5.json"),
    "0x6a000f20005980200259b80c5102003040001068": ("paraswap", "ParaswapAugustusV6_2.json"),
    "0x4f6f91599858bf0d19fabcf2c5d591fe13f7c059": ("0x", "ZeroExSettler.json"),
    "0x7747f8d2a76bd6345cc29622a946a929647f2359": ("0x", "ZeroExSettler.json"),
    "0xc8f6b8ba0dc0f175b568b99440b0867f69a29265": ("okx", "OKXDexRouter.json"),
    "0x2f68417a18da681589f4ea64b9cc9839209acff7": ("aggregator-2f68", "Aggregator2f68.json"),
}

#: Other well-known contracts on the call path (lowercase) → label.
#:
#: These identify the front-end, wallet or wrapper layer. They are not decoded.
PATH_LABELS: dict[str, str] = {
    "0x0000000000001ff3684f28c67538d4d072c22734": "0x-allowance-holder",
    "0xc87de04e2ec1f4282dff2933a2d58199f688fc3d": "0x-aggregator-guard",
    "0xb92fe925dc43a0ecde6c8b1a2709c170ec4fff4f": "relay",
    "0xd62b33a7df4d0ca5edd373576e48f73366e36179": "nft-farm-strategy",
    "0xbdc020668ad2a69603a85ad35b5f9a20af906845": "nft-farm-strategy",
    "0x9008d19f58aabd9ed0d60971565aa8510560ab41": "cowswap",
    "0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae": "lifi",
    "0xccc88a9d1b4ed6b0eaba998850414b24f1c315be": "relay",
    "0xb300000b72deaeb607a12d5f54773d1c19c7028d": "binance-wallet",
    "0xb00000fdc6785dd211185092e2328106964ddbdb": "binance-wallet",
    "0x10e0069160efc35798c3a46a2044c7dcd8b02980": "kyberswap-frontend-10e0",
    "0xfdca340485675db2cc0b9e2869a058abf816c3a7": "kyberswap-frontend-fdca",
    "0x5ff137d4b0fdcd49dca30c7cf57e578a026d2789": "erc4337-entrypoint-v0.6",
    "0x0000000071727de22e5e9d8baf0edac6f37da032": "erc4337-entrypoint-v0.7",
    "0x000100abaad02f1cfc8bbe32bd5a564817339e72": "coinbase-smart-wallet",
    "0x00000110dcdedc9581cb5ecb8467282f2926534d": "coinbase-smart-wallet",
    "0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb": "flashloan-helper",
    "0xa654a1c821f7604b5500a2fe8de67a737497d10d": "bot-a654",
    "0xaf3cefe9fbfb4962ef010d4b880a31297ec8260a": "bot-af3c",
    "0x7b579d9d147e65dfbdb7abb6bf2e41c7ad8c2c46": "bot-7b57",
    # Reads Tessera's keeper price store and Chainlink feeds before trading, then trades via the flashloan helper;
    # thousands of sender wallets, fills at the bottom of the block better than the quote. Same operator rotates contracts.
    "0x2dec2fd5c3fca86249e9bc670ed3097be531fe78": "bot-2dec",
    "0x81a59fa98fc7d9d67d696a117f24c133f1d04908": "bot-81a5",
    "0xc0269fc72c0138a3a551ccf07f0819adabaa8973": "bot-c026",
    "0x9ab3e4b61dd4d8bb533732be7061f28a401df7ea": "bot-9ab3",
}

#: Labels that mark bot flow even when no ``bot-*`` contract is on the path
BOT_MARKER_LABELS = {"flashloan-helper"}

#: Wrapper labels that belong to exactly one aggregator, so the aggregator is known even when its router frame was not decoded
WRAPPER_AGGREGATOR = {
    "0x-allowance-holder": "0x",
    "0x-aggregator-guard": "0x",
}

#: Wallet-layer labels, neither aggregator nor front-end
WALLET_LABELS = {"erc4337-entrypoint-v0.6", "erc4337-entrypoint-v0.7", "coinbase-smart-wallet"}

#: Placeholder addresses aggregators use for the native token
NATIVE_TOKEN_ALIASES = {
    "0x0000000000000000000000000000000000000000",
    "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
}

_ADDRESS_MASK = (1 << 160) - 1


@dataclass(slots=True)
class RouterOrder:
    """The user's order as seen by the aggregator router frame."""

    #: Aggregator slug, e.g. ``okx``
    aggregator: str
    #: Router contract that received the call
    router: HexAddress
    #: Function selector
    selector: str
    #: Function name
    function: str
    #: Depth of the router frame in the call tree, root is 0
    frame_depth: int
    #: Source token, native token normalised to ``None``-safe alias address
    src_token: HexAddress | None = None
    #: Destination token
    dst_token: HexAddress | None = None
    #: Input amount, raw units
    amount_in: int | None = None
    #: Minimum output the user accepted, raw units. For exact-output orders this is the exact output.
    min_amount_out: int | None = None
    #: Maximum input for exact-output orders, raw units
    max_amount_in: int | None = None
    #: Aggregator's own quoted output when the router records it (Paraswap)
    quoted_amount_out: int | None = None
    #: Final recipient
    recipient: HexAddress | None = None
    #: Order deadline as UNIX timestamp
    deadline: int | None = None
    #: Front-end or partner identifier when the router carries one (KyberSwap clientData, 0x zid)
    client_data: str | None = None
    #: True when the order fixes the output amount instead of the input
    exact_output: bool = False
    #: Output amount the router reported back to the caller, raw units, when the frame output was supplied and decodable
    returned_amount_out: int | None = None


@dataclass(slots=True)
class CallPathIdentification:
    """What the call path from transaction root to the venue tells us."""

    #: Decoded aggregator order, if a supported router was on the path
    order: RouterOrder | None
    #: Labels for every recognised contract on the path, in call order
    labels: list[str] = field(default_factory=list)
    #: Aggregator slug even when its calldata could not be decoded
    aggregator: str | None = None
    #: ``erc4337``, ``eip7702-self``, ``eoa`` or ``contract``
    wallet_kind: str = "eoa"
    #: Best-effort description of the front-end layer: first label that is not a wallet or aggregator
    frontend: str | None = None
    #: Every contract address on the path from the transaction root to the venue, lowercase, in call order
    path_addresses: list[str] = field(default_factory=list)


_contracts: dict[str, Contract] = {}


def _router_contract(abi_file: str) -> Contract:
    """Get a provider-less contract object for calldata decoding."""
    contract = _contracts.get(abi_file)
    if contract is None:
        contract = Web3().eth.contract(abi=get_abi_by_filename(f"dex_aggregator/{abi_file}"))
        _contracts[abi_file] = contract
    return contract


def _addr(value: int | str | None) -> HexAddress | None:
    """Normalise an address that may be packed into a uint256 (OKX, 1inch)."""
    if value is None:
        return None
    if isinstance(value, int):
        value = "0x" + (value & _ADDRESS_MASK).to_bytes(20, "big").hex()
    return Web3.to_checksum_address(value)


def _find_struct(args: dict, required: set[str]) -> dict | None:
    """Find the first dict argument that has all required keys."""
    for value in args.values():
        if isinstance(value, dict) and required <= set(value.keys()):
            return value
    return None


def _decode_okx(order: RouterOrder, args: dict) -> None:
    base = _find_struct(args, {"fromToken", "toToken", "fromTokenAmount", "minReturnAmount"})
    if base is not None:
        order.src_token = _addr(base["fromToken"])
        order.dst_token = _addr(base["toToken"])
        order.amount_in = int(base["fromTokenAmount"])
        order.min_amount_out = int(base["minReturnAmount"])
        order.deadline = int(base.get("deadLine") or 0) or None
    elif "minReturn" in args:
        # unxswap* / uniswapV3SwapTo: (srcToken|receiver, amount, minReturn, pools)
        order.src_token = _addr(args["srcToken"]) if "srcToken" in args else None
        order.amount_in = int(args["amount"])
        order.min_amount_out = int(args["minReturn"])
    order.recipient = _addr(args.get("receiver")) if isinstance(args.get("receiver"), str) else None


def _decode_kyberswap(order: RouterOrder, args: dict) -> None:
    execution = args.get("execution")
    desc = execution.get("desc") if isinstance(execution, dict) else args.get("desc")
    if isinstance(desc, dict):
        order.src_token = _addr(desc["srcToken"])
        order.dst_token = _addr(desc["dstToken"])
        order.amount_in = int(desc["amount"])
        order.min_amount_out = int(desc["minReturnAmount"])
        order.recipient = _addr(desc.get("dstReceiver"))
    client_data = execution.get("clientData") if isinstance(execution, dict) else args.get("clientData")
    if client_data:
        try:
            order.client_data = bytes(client_data).decode("utf-8")[:200]
        except UnicodeDecodeError:
            order.client_data = "0x" + bytes(client_data).hex()[:200]


def _decode_1inch(order: RouterOrder, args: dict) -> None:
    desc = args.get("desc")
    if isinstance(desc, dict):
        order.src_token = _addr(desc["srcToken"])
        order.dst_token = _addr(desc["dstToken"])
        order.amount_in = int(desc["amount"])
        order.min_amount_out = int(desc["minReturnAmount"])
        order.recipient = _addr(desc.get("dstReceiver"))
    elif "minReturn" in args:
        # unoswap*, uniswapV3Swap*, ethUnoswap*
        token = args.get("token", args.get("srcToken"))
        order.src_token = _addr(token) if token is not None else None
        order.amount_in = int(args["amount"]) if "amount" in args else None
        order.min_amount_out = int(args["minReturn"])
        order.recipient = _addr(args.get("to", args.get("recipient"))) if isinstance(args.get("to", args.get("recipient")), str) else None


def _decode_paraswap(order: RouterOrder, args: dict) -> None:
    data = _find_struct(args, {"srcToken", "destToken", "fromAmount", "toAmount", "quotedAmount"})
    if data is None:
        return
    order.src_token = _addr(data["srcToken"])
    order.dst_token = _addr(data["destToken"])
    order.recipient = _addr(data.get("beneficiary"))
    order.quoted_amount_out = int(data["quotedAmount"])
    if order.function.startswith("swapExactAmountOut"):
        order.exact_output = True
        order.max_amount_in = int(data["fromAmount"])
        order.min_amount_out = int(data["toAmount"])
    else:
        order.amount_in = int(data["fromAmount"])
        order.min_amount_out = int(data["toAmount"])


def _decode_0x(order: RouterOrder, args: dict) -> None:
    slippage = args.get("slippage")
    if isinstance(slippage, dict):
        order.recipient = _addr(slippage["recipient"])
        order.dst_token = _addr(slippage["buyToken"])
        order.min_amount_out = int(slippage["minAmountOut"])
    # Third positional argument is the unnamed zid (bytes32) carrying the integrator id
    for value in args.values():
        if isinstance(value, bytes) and len(value) == 32:
            order.client_data = "0x" + value.hex()
            break


def _decode_aggregator_2f68(order: RouterOrder, args: dict) -> None:
    request = args.get("request")
    if isinstance(request, dict):
        order.src_token = _addr(request["inputToken"])
        order.dst_token = _addr(request["outputToken"])
        order.min_amount_out = int(request["minOutputAmount"])
        order.deadline = int(request.get("deadline") or 0) or None
    amounts = args.get("routesAmount")
    if isinstance(amounts, (list, tuple)):
        order.amount_in = sum(int(a) for a in amounts)
    order.recipient = _addr(args.get("recipient")) if isinstance(args.get("recipient"), str) else None


_DECODERS = {
    "okx": _decode_okx,
    "kyberswap": _decode_kyberswap,
    "1inch": _decode_1inch,
    "paraswap": _decode_paraswap,
    "0x": _decode_0x,
    "aggregator-2f68": _decode_aggregator_2f68,
}


#: Output names routers use for the amount delivered to the user
_RETURN_AMOUNT_NAMES = ("returnAmount", "receivedAmount", "amountOut", "outputAmount")


def _decode_returned_amount(fn_abi: dict, output: str | bytes | None) -> int | None:
    """Decode the delivered output amount from a router frame's return data."""
    if not output:
        return None
    outputs = fn_abi.get("outputs") or []
    if not outputs:
        return None
    if isinstance(output, str):
        output = bytes.fromhex(output[2:] if output.startswith("0x") else output)
    try:
        values = eth_abi.decode([o["type"] for o in outputs], output)
    except Exception as e:  # noqa: BLE001 - eth_abi raises several unrelated types on malformed return data
        logger.debug("Cannot decode router output for %s: %s", fn_abi.get("name"), e)
        return None
    for out, value in zip(outputs, values):
        if out["type"] == "uint256" and out.get("name") in _RETURN_AMOUNT_NAMES:
            return int(value)
    for out, value in zip(outputs, values):
        if out["type"] == "uint256":
            return int(value)
    return None


def decode_router_call(router: HexAddress | str, calldata: str | bytes, frame_depth: int = 0, output: str | bytes | None = None) -> RouterOrder | None:
    """Decode an aggregator router call into the user's order.

    :param router:
        Contract address that received the call.

    :param calldata:
        Full calldata including the 4-byte selector.

    :param frame_depth:
        Depth of the frame in the call tree, recorded on the result.

    :param output:
        Return data of the frame, if available, to recover the delivered output amount.

    :return:
        Decoded order, or ``None`` if the router is not supported or the
        function is not a swap entry point.
    """
    entry = AGGREGATOR_ROUTERS.get(str(router).lower())
    if entry is None:
        return None
    slug, abi_file = entry
    if isinstance(calldata, bytes):
        calldata = "0x" + calldata.hex()
    if len(calldata) < 10:
        return None
    contract = _router_contract(abi_file)
    try:
        fn, args = contract.decode_function_input(calldata)
    except (ValueError, eth_abi.exceptions.DecodingError) as e:
        # ValueError: selector not found on this ABI (a same-selector, different-shape call).
        # DecodingError (e.g. InsufficientDataBytes): a truncated trace -- some RPC providers
        # cap trace response size for large calldata, so the selector matches but the tail is short.
        logger.debug("Cannot decode %s call to %s: %s", slug, router, e)
        return None
    order = RouterOrder(
        aggregator=slug,
        router=Web3.to_checksum_address(router),
        selector=calldata[:10],
        function=fn.fn_name,
        frame_depth=frame_depth,
    )
    _DECODERS[slug](order, args)
    if order.min_amount_out is None:
        return None
    order.returned_amount_out = _decode_returned_amount(fn.abi, output)
    return order


def identify_call_path(frames: list[tuple], tx_from: str | None = None, tx_to: str | None = None) -> CallPathIdentification:
    """Label a call path and decode the first aggregator router call on it.

    :param frames:
        ``(to_address, calldata)`` or ``(to_address, calldata, output)`` tuples
        from the transaction root frame down to the venue frame, inclusive.
        Delegatecall frames may be included; the proxy frame preceding them
        carries the same calldata and is matched first.

    :param tx_from:
        Transaction sender, used for wallet kind detection.

    :param tx_to:
        Transaction target, used for wallet kind detection.

    :return:
        Identification with the decoded order when available.
    """
    labels: list[str] = []
    order: RouterOrder | None = None
    aggregator: str | None = None
    path_addresses: list[str] = []

    for depth, frame in enumerate(frames):
        to, calldata = frame[0], frame[1]
        output = frame[2] if len(frame) > 2 else None
        if not to:
            continue
        key = to.lower()
        path_addresses.append(key)
        if key in AGGREGATOR_ROUTERS:
            slug = AGGREGATOR_ROUTERS[key][0]
            labels.append(slug)
            if aggregator is None:
                aggregator = slug
            if order is None and calldata:
                order = decode_router_call(to, calldata, depth, output)
        elif key in PATH_LABELS:
            labels.append(PATH_LABELS[key])

    wallet_kind = "eoa"
    if any(label.startswith("erc4337-entrypoint") for label in labels):
        wallet_kind = "erc4337"
    elif tx_from and tx_to and tx_from.lower() == tx_to.lower():
        wallet_kind = "eip7702-self"

    if aggregator is None:
        for label in labels:
            if label.startswith("bot-") or label in BOT_MARKER_LABELS:
                aggregator = "bot"
                break
            if label in ("cowswap", "lifi", "binance-wallet"):
                aggregator = label
                break
            if label in WRAPPER_AGGREGATOR:
                aggregator = WRAPPER_AGGREGATOR[label]
                break

    frontend = next(
        (label for label in labels if label not in WALLET_LABELS and label not in _DECODERS and label not in WRAPPER_AGGREGATOR and label not in BOT_MARKER_LABELS),
        None,
    )

    return CallPathIdentification(order=order, labels=labels, aggregator=aggregator, wallet_kind=wallet_kind, frontend=frontend, path_addresses=path_addresses)


def router_label_rows() -> list[dict]:
    """Flatten the known-contract tables into rows for a database label table.

    :return:
        One dict per known address with ``address`` (checksummed), ``label``,
        ``kind`` (``aggregator``, ``bot``, ``wallet`` or ``wrapper``) and
        ``aggregator`` (the slug when the address implies exactly one aggregator).
    """
    rows = []
    for address, (slug, _abi) in AGGREGATOR_ROUTERS.items():
        rows.append({"address": Web3.to_checksum_address(address), "label": slug, "kind": "aggregator", "aggregator": slug})
    for address, label in PATH_LABELS.items():
        if label.startswith("bot-") or label in BOT_MARKER_LABELS:
            kind, slug = "bot", "bot"
        elif label in WALLET_LABELS:
            kind, slug = "wallet", None
        elif label in ("cowswap", "lifi", "binance-wallet"):
            kind, slug = "aggregator", label
        else:
            kind, slug = "wrapper", WRAPPER_AGGREGATOR.get(label)
        rows.append({"address": Web3.to_checksum_address(address), "label": label, "kind": kind, "aggregator": slug})
    return rows


def order_to_json(order: RouterOrder | None) -> str | None:
    """Serialise an order for storage, big ints as strings."""
    if order is None:
        return None
    payload = {k: (str(v) if isinstance(v, int) and not isinstance(v, bool) else v) for k, v in asdict(order).items()}
    return json.dumps(payload)
