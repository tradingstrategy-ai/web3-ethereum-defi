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



def get_prices(tick: int, in_token_decimals: int, out_token_decimals: int, reverse: bool = False):
    raw_price = tick_to_price(tick)
    price1 = method1(raw_price, in_token_decimals, out_token_decimals, reverse)
    price2 = method2(raw_price, in_token_decimals, out_token_decimals, reverse)

    return price1, price2

def test_methods():
    """Uses real values derived from analyse_trade_receipt usage in tradeexecutor"""
    # path = ["0xF2E246BB76DF876Cef8b38ae84130F4F55De395b", 3000, "0xB9816fC57977D5A786E654c7CF76767be63b966e"]
    price1, price2 = get_prices(-201931, 6, 18, True)
    
    print("")
    print(price1)
    print(price2)
    
    # path = ["0xB9816fC57977D5A786E654c7CF76767be63b966e", 3000, "0xF2E246BB76DF876Cef8b38ae84130F4F55De395b"]
    price1, price2 = get_prices(-201937, 18, 6, False)
    
    print(price1)
    print(price2)
    
