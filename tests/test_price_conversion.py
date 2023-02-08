from eth_defi.uniswap_v3.utils import tick_to_price


def method1(raw_price: float, in_token_decimals: int, out_token_decimals: int, reverse: bool = False):

    if reverse:
        return raw_price / 10 ** (in_token_decimals - out_token_decimals)
    else:
        return raw_price / 10 ** (out_token_decimals - in_token_decimals)


def method2(raw_price: float, in_token_decimals: int, out_token_decimals: int, reverse: bool = False):
    """As seen in uniswap_v3.pool"""

    if reverse:
        return (1 / raw_price) / 10 ** (in_token_decimals - out_token_decimals)
    else:
        return raw_price / 10 ** (out_token_decimals - in_token_decimals)


def test_methods():
    # path = ["0xF2E246BB76DF876Cef8b38ae84130F4F55De395b", 3000, "0xB9816fC57977D5A786E654c7CF76767be63b966e"]
    tick = -201931
    raw_price = tick_to_price(tick)
    price1 = method1(raw_price, 6, 18, True)
    price2 = method2(raw_price, 6, 18, True)
    # assert price1 == price2

    # path = ["0xB9816fC57977D5A786E654c7CF76767be63b966e", 3000, "0xF2E246BB76DF876Cef8b38ae84130F4F55De395b"]
    tick = -201937
    raw_price = tick_to_price(tick)
    price1 = method1(raw_price, 18, 6, False)
    price2 = method2(raw_price, 18, 6, False)
    # assert price1 == price2
