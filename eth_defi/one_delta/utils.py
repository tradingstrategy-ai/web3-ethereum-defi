from eth_typing import HexAddress


def encode_path(
    path: list[HexAddress],
    fees: list,
    actions: list[int],
    markets: list[int],
    flag: int = -1,
    exact_output: bool = False,
) -> bytes:
    """Encode the routing path and other info for 1delta flash swap.

    TODO

    `Read more <https://github.com/Uniswap/v3-periphery/blob/22a7ead071fff53f00d9ddc13434f285f4ed5c7d/contracts/libraries/Path.sol>`__.

    :param path: List of token addresses how to route the trade
    :param fees: List of trading fees of the pools in the route
    :param exact_output: Whether the encoded path be used for exactOutput quote or swap
    """
    assert len(fees) == len(path) - 1

    if exact_output:
        path.reverse()
        fees.reverse()

    encoded = b""
    for index, token in enumerate(path):
        encoded += bytes.fromhex(token[2:])
        if token != path[-1]:
            encoded += int.to_bytes(fees[index], 3, "big")
            if len(markets) > index:
                encoded += int.to_bytes(markets[index], 1, "big")
            if len(actions) > index:
                encoded += int.to_bytes(actions[index], 1, "big")

    if flag >= 0:
        encoded += int.to_bytes(flag, 1, "big")

    return encoded
