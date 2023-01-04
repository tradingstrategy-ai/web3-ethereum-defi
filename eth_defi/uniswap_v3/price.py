"""Uniswap v3 price calculations."""
from decimal import Decimal

from eth_typing import HexAddress
from eth_defi.uniswap_v3.deployment import UniswapV3Deployment
from eth_defi.uniswap_v3.utils import encode_path
from eth_defi.uniswap_v3.pool import fetch_pool_details, PoolDetails
from eth_defi.token import fetch_erc20_details

from web3 import Web3

from typing import Optional


class UniswapV3PriceHelper:
    def __init__(self, uniswap_v3: UniswapV3Deployment):
        self.deployment = uniswap_v3

    def get_amount_out(
        self,
        amount_in: int,
        path: list[HexAddress],
        fees: list[int],
        *,
        slippage: float = 0,
    ) -> int:
        """Get how much token we are going to receive.

        Example:

        .. code-block:: python

            # Estimate how much DAI we will receive for 1000 WETH
            # using the route of 2 pools: WETH/USDC 0.3% and USDC/DAI 1%
            # with slippage tolerance is 0.5%
            price_helper = UniswapV3PriceHelper(uniswap_v3_deployment)
            amount_out = price_helper.get_amount_out(
                1000,
                [
                    weth.address,
                    usdc.address,
                    dai.address,
                ],
                [
                    3000,
                    10000,
                ],
                slippage=50,
            )

        :param amount_in: Amount of input asset.
        :param path: List of token addresses how to route the trade
        :param fees: List of trading fees of the pools in the route
        :param slippage: Slippage express in bps
        """
        assert len(path) >= 2
        assert len(fees) == len(path) - 1
        assert slippage >= 0

        encoded_path = encode_path(path, fees)
        amount_out = self.deployment.quoter.functions.quoteExactInput(
            encoded_path, amount_in
        ).call()

        return int(amount_out * 10_000 // (10_000 + slippage))

    def get_amount_in(
        self,
        amount_out: int,
        path: list[HexAddress],
        fees: list[int],
        *,
        slippage: float = 0,
    ) -> int:
        """Get how much token we are going to spend.

        :param amount_in: Amount of output asset.
        :param path: List of token addresses how to route the trade
        :param fees: List of trading fees of the pools in the route
        :param slippage: Slippage express in bps
        """
        assert len(path) >= 2
        assert len(fees) == len(path) - 1
        assert slippage >= 0

        encoded_path = encode_path(path, fees, exact_output=True)
        amount_in = self.deployment.quoter.functions.quoteExactOutput(
            encoded_path, amount_out
        ).call()

        return int(amount_in * (10_000 + slippage) // 10_000)

def get_path_and_fees(pool0: PoolDetails, pool1: PoolDetails | None = None) -> tuple[list[HexAddress], list[int]]:
    """Given either one or two pools, constructs the path and fees list.
    
    :param pool0:
        The first/only pool to be used in the swap

    :param pool1:
        The second (optional) pool to be used in the swap. Only needs to be provided if an intemediary
        token is being used for the swap

    :return:
        path, fees 
    """

    path = []
    fees = []

    # first token
    path = [pool0.token0, pool0.token1]
    fees = [pool0.fee]

    # second token
    if pool1:
        assert pool0.token1 == pool1.token0, "pool0.token1 must be the same as pool1.token0"
        path.append(pool1.token1)
        fees.append(pool1.fee)
    
    return path, fees

def estimate_buy_quantity(
    uniswap: UniswapV3Deployment,
    amount_in: int,
    pool0: PoolDetails,
    pool1: PoolDetails | None = None,
    slippage: Optional[float] = 0,
) -> int:
    """Estimate how many tokens we are going to receive when doing a buy.

    Good for doing a price impact calculations.

    Calls the on-chain contract to get the current liquidity and estimates the
    the price based on it.

    :param amount_in: How much of the quote token we have to use
    :param uniswap: Uniswap v3 deployment
    :param pool0: The first/only pool to be used in the swap
    :param pool1: The second (optional) pool to be used in the swap. Only needs to be provided if an intemediary
        token is being used for the swap
    :param slippage: Slippage express in bps
    :return: Expected base token to receive
    """

    path, fees = get_path_and_fees(pool0, pool1)
    price_helper = UniswapV3PriceHelper(uniswap)
    return price_helper.get_amount_out(amount_in, path, fees, slippage=slippage)


def estimate_buy_price(
    uniswap: UniswapV3Deployment,
    amount_out: int,
    pool0: PoolDetails,
    pool1: PoolDetails | None = None,
    slippage: Optional[float] = 0,
) -> int:
    """Estimate how much we are going to need to pay when doing buy.

    Calls the on-chain contract to get the current liquidity and estimates the
    the price based on it.

    :param amount_out: How much of the base token we want to buy
    :param uniswap: Uniswap v3 deployment
    :param pool0: The first/only pool to be used in the swap
    :param pool1: The second (optional) pool to be used in the swap. 
        Only needs to be provided if an intemediary token is being used for the swap
    :param slippage: Slippage express in bps
    :return: Expected base token to receive
    """

    path, fees = get_path_and_fees(pool0, pool1)
    price_helper = UniswapV3PriceHelper(uniswap)
    return price_helper.get_amount_in(amount_out, path, fees, slippage=slippage)


def estimate_sell_price(
    uniswap: UniswapV3Deployment,
    amount_in: int,
    pool0: PoolDetails,
    pool1: PoolDetails | None = None,
    slippage: Optional[float] = 0,
) -> int:
    """Estimate how much we are going to get paid when doing a sell.

    Good for doing a price impact calculations.

    Calls the on-chain contract to get the current liquidity and estimates the
    the price based on it.

    :param amount_in: How much of the quote token we have to use
    :param uniswap: Uniswap v3 deployment
    :param pool0: The first/only pool to be used in the swap
    :param pool1: The second (optional) pool to be used in the swap. Only needs to be provided if an intemediary
        token is being used for the swap
    :param slippage: Slippage express in bps
    :return: Expected base token to receive
    """

    path, fees = get_path_and_fees(pool0, pool1)
    price_helper = UniswapV3PriceHelper(uniswap)
    return price_helper.get_amount_out(amount_in, path, fees, slippage=slippage)


def estimate_buy_price_decimals(
    uniswap: UniswapV3Deployment,
    amount_out: int,
    pool0: PoolDetails,
    pool1: PoolDetails | None = None,
    slippage: Optional[float] = 0,
) -> Decimal:
    """Estimate how much we are going to need to pay when doing buy.    

    :param amount_out: How much of the base token we want to buy
    :param uniswap: Uniswap v3 deployment
    :param pool0: The first/only pool to be used in the swap
    :param pool1: The second (optional) pool to be used in the swap. Only needs to be provided if an intemediary
        token is being used for the swap
    :param slippage: Slippage express in bps
    :return: Expected quote token amount to receive
    :raise TokenDetailError: If we have an issue with ERC-20 contracts
    """

    web3 = uniswap.web3

    quote = (
        fetch_erc20_details(web3, pool1.token1, raise_on_error=False)
        if pool1
        else fetch_erc20_details(web3, pool0.token1, raise_on_error=False)
    )
    quantity_raw = quote.convert_to_raw(amount_out)

    path, fees = get_path_and_fees(pool0, pool1)
    price_helper = UniswapV3PriceHelper(uniswap)

    in_raw = price_helper.get_amount_in(quantity_raw, path, fees, slippage=slippage)
    return quote.convert_to_decimals(in_raw)


def estimate_sell_price_decimals(
    uniswap: UniswapV3Deployment,
    amount_in: int,
    pool0: PoolDetails,
    pool1: PoolDetails | None = None,
    slippage: Optional[float] = 0,
) -> Decimal:
    """Estimate how much we are going to get paid when doing a sell.

    Much like :py:func:`estimate_sell_price` but in/out is expressed as python Decimal units.
    Furthermore, no ERC-20 token contract needed ABI, but it is loaded by the function.

    :param amount_in: How much of the quote token we have to sell
    :param uniswap: Uniswap v3 deployment
    :param pool0: The first/only pool to be used in the swap
    :param pool1: The second (optional) pool to be used in the swap. Only needs to be provided if an intemediary
        token is being used for the swap
    :param slippage: Slippage express in bps
    :return: Expected quote token amount to receive in quota tokens (decimal converted)
    :raise TokenDetailError: If we have an issue with ERC-20 contracts
    """
    web3 = uniswap.web3

    quote = fetch_erc20_details(web3, pool0.token0, raise_on_error=False)
    quantity_raw = quote.convert_to_raw(amount_in)

    path, fees = get_path_and_fees(pool0, pool1)
    price_helper = UniswapV3PriceHelper(uniswap)

    out_raw = price_helper.get_amount_out(quantity_raw, path, fees, slippage=slippage)
    return quote.convert_to_decimals(out_raw)

def estimate_buy_received_amount_raw(
    uniswap: UniswapV3Deployment,
    amount_out_raw: int,
    pool0: PoolDetails,
    pool1: PoolDetails | None = None,
    slippage: Optional[float] = 0,
) -> int:
    """Estimate how much we receive for a certain cash amount.

    :param amount_out_raw: How much of the base token we want to buy. Use raw format i.e. without decimals
    :param uniswap: Uniswap v3 deployment
    :param pool0: The first/only pool to be used in the swap
    :param pool1: The second (optional) pool to be used in the swap. Only needs to be provided if an intemediary
        token is being used for the swap
    :param slippage: Slippage express in bps
    :return: Expected quote token amount to receive
    :raise TokenDetailError: If we have an issue with ERC-20 contracts
    """
    path, fees = get_path_and_fees(pool0, pool1)
    price_helper = UniswapV3PriceHelper(uniswap)
    return price_helper.get_amount_out(amount_out_raw, path, fees, slippage=slippage)


def estimate_sell_received_amount_raw(
    uniswap: UniswapV3Deployment,
    amount_in_raw: int,
    pool0: PoolDetails,
    pool1: PoolDetails | None = None,
    slippage: Optional[float] = 0,
) -> int:
    """Estimate how much cash we receive for a certain quantity of tokens sold.

    :param amount_in: How much of the quote token we have to sell
    :param uniswap: Uniswap v3 deployment
    :param pool0: The first/only pool to be used in the swap
    :param pool1: The second (optional) pool to be used in the swap. Only needs to be provided if an intemediary
        token is being used for the swap
    :param slippage: Slippage express in bps
    :return: Expected quote token amount to receive
    :raise TokenDetailError: If we have an issue with ERC-20 contracts
    """
    path, fees = get_path_and_fees(pool1, pool0)
    price_helper = UniswapV3PriceHelper(uniswap)
    return price_helper.get_amount_out(amount_in_raw, path, fees, slippage=slippage)
