"""Troublesome tokens"""


# https://tokensniffer.com/token/base/iof2ha9v86i69416go2kzasch62nqbn9ecxmcssn8ie5j0z1qqv8p1ncvqs2
# Valuation model failed GenericValuation failed for position <Open position #185 <Pair BOB-WETH spot_market_hold at 0x511088edf4c6fd71b48ca4fe4467d39a3c9e32e3 (1.0000% fee) on exchange uniswap-v3> $12.905446608819052>
# Position debug data: {'balance_updates': {63: {'asset': {'address': '0xd9ea811a51d6fe491d27c2a0442b3f577852874d',

#: Historical rug pulls.
#:
#: These tokens have their rug pulled.
#:
#: Sell at any available liquidity, with max slippage
#:
RUGPULLS = {
    "BOB": "0xd9ea811a51d6fe491d27c2a0442b3f577852874d"
}