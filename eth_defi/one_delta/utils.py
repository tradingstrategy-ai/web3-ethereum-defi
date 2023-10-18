from eth_typing import HexAddress

from eth_defi.aave_v3.constants import AaveV3InterestRateMode
from eth_defi.one_delta.constants import Exchange, TradeOperation, TradeType
from eth_defi.uniswap_v3.constants import DEFAULT_FEES


def encode_path(
    *,
    path: list[HexAddress],
    fees: list,
    operation: TradeOperation,
    exchanges: list[Exchange],
    interest_mode: AaveV3InterestRateMode,
    trade_type: TradeType = TradeType.EXACT_INPUT,
    exact_output: bool = False,
) -> bytes:
    """Encode the routing path and other info for 1delta flash swap.

    `Read more <https://github.com/1delta-DAO/contracts-delegation/blob/467593f5c457b2eefab8a0bb9cb75b399efcb16a/test/1delta/shared/aggregatorPath.ts#L58>`__.

    :param path: List of token addresses how to route the trade
    :param fees: List of trading fees of the pools in the route
    :param exact_output: Whether the encoded path be used for exactOutput quote or swap

    NOTE: we don't support all 1delta operations to keep this function simple.
    """
    assert len(fees) == len(path) - 1
    assert len(exchanges) == len(fees)
    for fee in fees:
        assert fee in DEFAULT_FEES

    if trade_type == TradeType.EXACT_OUTPUT:
        path.reverse()
        fees.reverse()

    match operation:
        case TradeOperation.OPEN:
            actions = [6]
            flag = interest_mode
        case TradeOperation.TRIM:
            actions = [7]
            flag = 3
        case _:
            raise ValueError(f"Unsupported operation: {operation}")

    # pad the action list with trade type
    actions += [trade_type] * (len(fees) - 1)

    encoded = b""
    for index, token in enumerate(path):
        encoded += bytes.fromhex(token[2:])
        if token != path[-1]:
            encoded += int.to_bytes(fees[index], 3, "big")
            if len(exchanges) > index:
                encoded += int.to_bytes(exchanges[index], 1, "big")
            if len(actions) > index:
                encoded += int.to_bytes(actions[index], 1, "big")

    encoded += int.to_bytes(flag, 1, "big")

    return encoded