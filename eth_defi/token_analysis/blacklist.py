"""Troublesome tokens"""

from eth_typing import HexAddress

# https://tokensniffer.com/token/base/iof2ha9v86i69416go2kzasch62nqbn9ecxmcssn8ie5j0z1qqv8p1ncvqs2
# Valuation model failed GenericValuation failed for position <Open position #185 <Pair BOB-WETH spot_market_hold at 0x511088edf4c6fd71b48ca4fe4467d39a3c9e32e3 (1.0000% fee) on exchange uniswap-v3> $12.905446608819052>
# Position debug data: {'balance_updates': {63: {'asset': {'address': '0xd9ea811a51d6fe491d27c2a0442b3f577852874d',

# Uniswap v3 quoter error for BOB is
# base-memecoin-index  |   File "/usr/src/trade-executor/deps/web3-ethereum-defi/eth_defi/provider/fallback.py", line 194, in make_request
# base-memecoin-index  |     raise ValueError(resp_data["error"])
# base-memecoin-index  | ValueError: {'message': 'execution reverted: revert: Unexpected error', 'code': 3, 'data': '0x08c379a000000000000000000000000000000000000000000000000000000000000000200000000000000000000000000000000000000000000000000000000000000010556e6578706563746564206572726f7200000000000000000000000000000000'}

#: Historical rug pulls.
#:
#: These tokens have their rug pulled.
#:
#: Sell at any available liquidity, with max slippage
#:
#: Symbol -> Address mapping
#:
RUGPULLS = {
    # https://tradingstrategy.ai/trading-view/base/uniswap-v3/bob-usdc-fee-100
    # https://tokensniffer.com/token/base/iof2ha9v86i69416go2kzasch62nqbn9ecxmcssn8ie5j0z1qqv8p1ncvqs2
    "BOB": "0xd9ea811a51d6fe491d27c2a0442b3f577852874d"
}

_rugpulls_by_address = {v: k for k, v in RUGPULLS.items()}


def is_blacklisted_address(
    address: str | HexAddress,
    chain_id: int = None,
):
    assert isinstance(address, str)
    assert address.startswith("0x")
    return address in _rugpulls_by_address


def is_blacklisted_symbol(
    symbol: str | HexAddress,
    chain_id: int = None,
):
    return symbol in RUGPULLS
