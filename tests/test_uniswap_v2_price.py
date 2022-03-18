from re import T

from eth_hentai.uniswap_v2.fees import UniswapV2FeeCalculator


def test_get_amount_in():
    assert UniswapV2FeeCalculator.get_amount_in(100, 1000, 1000) == 112
    assert UniswapV2FeeCalculator.get_amount_in(100, 10000, 10000) == 102
    assert UniswapV2FeeCalculator.get_amount_in(100, 10000, 10000, slippage=1000) == 113


def test_get_amount_out():
    assert UniswapV2FeeCalculator.get_amount_out(100, 1000, 1000) == 90
    assert UniswapV2FeeCalculator.get_amount_out(100, 10000, 10000) == 98
    assert UniswapV2FeeCalculator.get_amount_out(100, 1000, 1000, slippage=500) == 86
