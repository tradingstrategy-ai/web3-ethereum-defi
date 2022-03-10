import math
from decimal import Decimal


def get_sqrt_price_x96(amount0: int, amount1: int) -> int:
    """Calculate `sqrtPriceX96` (or sometimes called `sqrtRatioX96`) from 2 token amounts

    `Read more details here <https://docs.uniswap.org/sdk/guides/fetching-prices#understanding-sqrtprice>`_.
    """
    return int((Decimal(amount1) / Decimal(amount0)).sqrt() * 2**96)
