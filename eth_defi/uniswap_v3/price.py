"""Uniswap v3 price calculations."""

from decimal import Decimal

from eth_typing import HexAddress

from eth_defi.uniswap_v3.deployment import UniswapV3Deployment
from eth_defi.uniswap_v3.utils import encode_path


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
        self.validate_args(path, fees, slippage, amount_in)

        encoded_path = encode_path(path, fees)
        amount_out = self.deployment.quoter.functions.quoteExactInput(encoded_path, amount_in).call()

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
        self.validate_args(path, fees, slippage, amount_out)

        encoded_path = encode_path(path, fees, exact_output=True)
        amount_in = self.deployment.quoter.functions.quoteExactOutput(encoded_path, amount_out).call()

        return int(amount_in * (10_000 + slippage) // 10_000)

    @staticmethod
    def validate_args(path, fees, slippage, amount):
        assert len(path) >= 2
        assert len(fees) == len(path) - 1
        assert slippage >= 0
        assert type(amount) == int, "Incorrect type provided for amount. Require int"


def estimate_buy_quantity(
    uniswap: UniswapV3Deployment,
    base_token_address: HexAddress,
    quote_token_address: HexAddress,
    quantity: int,
    target_pair_fee: int,
    *,
    slippage: float = 0,
    intermediate_token_address: HexAddress | None = None,
    intermediate_pair_fee: int | None = None,
) -> int:
    """Estimate how many tokens we are going to receive when doing a buy.

    Good for price impact calculations.

    Calls the on-chain contract to get the current liquidity and estimates the
    the price based on it.

    Example:

    .. code-block:: python

        # Estimate how much ETH we will receive for 500 USDC.
        # In this case the pool ETH price is $1700 so this should be below ~1/4 of ETH
        amount_eth = estimate_buy_quantity(
            uniswap_v3,
            weth.address,
            usdc.address,
            500*10**18,
            500,
        )
        assert amount_eth / 1e18 == pytest.approx(0.28488156127668085)

    :param quantity: How much of the quote token we have to use
    :param uniswap: Uniswap v3 deployment
    :param base_token_address: Address of the base token of the trading pair
    :param quote_token_address: Address of the quote token of the trading pair
    :param target_pair_fee: Trading fee express in raw format for the target pair
    :param slippage: Slippage express in bps
    :return: Expected base token to receive
    """
    price_helper = UniswapV3PriceHelper(uniswap)
    
    if intermediate_token_address:
        path = [quote_token_address, intermediate_token_address, base_token_address]
        fees = [intermediate_pair_fee, target_pair_fee]
    else:
        path = [quote_token_address, base_token_address]
        fees = [target_pair_fee]

    return price_helper.get_amount_out(quantity, path, fees, slippage=slippage)


def estimate_buy_price(
    uniswap: UniswapV3Deployment,
    base_token_address: HexAddress,
    quote_token_address: HexAddress,
    quantity: int,
    target_pair_fee: int,
    *,
    slippage: float = 0,
    intermediate_token_address: HexAddress | None = None,
    intermediate_pair_fee: int | None = None,
) -> int:
    """Estimate how much we are going to need to pay when doing buy.

    Calls the on-chain contract to get the current liquidity and estimates the
    the price based on it.

    Example:

    .. code-block:: python

        # Estimate how much ETH we will receive for 500 USDC.
        # In this case the pool ETH price is $1700 so this should be below ~1/4 of ETH
        amount_eth = estimate_buy_price(
            uniswap_v3,
            weth.address,
            usdc.address,
            1*10**18,
            500,
        )
        assert amount_eth / 1e18 == pytest.approx(0.28488156127668085)

    :param uniswap: Uniswap v2 deployment
    :param base_token_address: Base token address of the trading pair
    :param quote_token_address: Quote token addressof the trading pair
    :param quantity: How much of the base token we want to buy
    :param target_pair_fee: Trading fee express in raw format for the target pair
    :param slippage: Slippage express in bps
    :return: Expected base token to receive
    """
    price_helper = UniswapV3PriceHelper(uniswap)
    if intermediate_token_address:
        path = [quote_token_address, intermediate_token_address, base_token_address]
        fees = [intermediate_pair_fee, target_pair_fee]
    else:
        path = [quote_token_address, base_token_address]
        fees = [target_pair_fee]

    return price_helper.get_amount_in(quantity, path, fees, slippage=slippage)


def estimate_sell_price(
    uniswap: UniswapV3Deployment,
    base_token_address: HexAddress,
    quote_token_address: HexAddress,
    quantity: int,
    target_pair_fee: int,
    *,
    slippage: float = 0,
    intermediate_token_address: HexAddress | None = None,
    intermediate_pair_fee: int | None = None,
) -> int:
    """Estimate how much we are going to get paid when doing a sell.

    Calls the on-chain contract to get the current liquidity and estimates the
    the price based on it.

    .. note ::

        The price of an asset depends on how much you are selling it. More you sell,
        more there will be price impact.

    To get a price of an asset, ask for quantity 1 of it:

    :param quantity: How much of the base token we want to sell
    :param uniswap: Uniswap v3 deployment
    :param base_token_address: Base token address of the trading pair
    :param quote_token_address: Quote token address of the trading pair
    :param target_pair_fee: Trading fee of the target pair expressed in raw
    :param slippage: Slippage express in bps
    :return: Expected quote token amount to receive
    """
    price_helper = UniswapV3PriceHelper(uniswap)
    if intermediate_token_address:
        path = [base_token_address, intermediate_token_address, quote_token_address]
        fees = [intermediate_pair_fee, target_pair_fee]
    else:
        path = [base_token_address, quote_token_address]
        fees = [target_pair_fee]

    return price_helper.get_amount_out(quantity, path, fees, slippage=slippage)


def estimate_buy_received_amount_raw(
    uniswap: UniswapV3Deployment,
    base_token_address: HexAddress,
    quote_token_address: HexAddress,
    quantity_raw: Decimal,
    target_pair_fee: int,
    *,
    slippage: float = 0,
    intermediate_token_address: HexAddress | None = None,
    intermediate_pair_fee: int | None = None,
) -> int:
    
    fee_helper = UniswapV3PriceHelper(uniswap)

    if intermediate_token_address:
        path = [quote_token_address, intermediate_token_address, base_token_address]
        fees = [intermediate_pair_fee, target_pair_fee]
    else:
        path = [quote_token_address, base_token_address]
        fees = [target_pair_fee]

    # We will receive equal number of amounts as there are items in the path
    return fee_helper.get_amount_out(quantity_raw, path, fees, slippage=slippage)


def estimate_sell_received_amount_raw(
    uniswap: UniswapV3Deployment,
    base_token_address: HexAddress,
    quote_token_address: HexAddress,
    quantity_raw: Decimal,
    target_pair_fee: int,
    *,
    slippage: float = 0,
    intermediate_token_address: HexAddress | None = None,
    intermediate_pair_fee: int | None = None,
) -> int:
    """Estimate how much we receive for a certain cash amount.

    Example:

    .. code-block:: python

        # Estimate the price of buying 1650 USDC worth of ETH
        eth_received = estimate_buy_received_amount_raw(
            uniswap_v3,
            weth.address,
            usdc.address,
            1650 * 10**18,
            500,
        )

        assert eth_received / (10**18) == pytest.approx(0.9667409780905836)

        # Calculate price of ETH as $ for our purchase
        price = (1650*10**18) / eth_received
        assert price == pytest.approx(Decimal(1706.7653460381143))

    :param quantity: How much of the base token we want to buy
    :param uniswap: Uniswap v3 deployment
    :param base_token_address: Base token address of the trading pair
    :param quote_token_address: Quote token address of the trading pair
    :param target_pair_fee: Trading fee of the target pair in raw format
    :param slippage: Slippage express in bps
    :return: Expected quote token amount to receive
    :raise TokenDetailError: If we have an issue with ERC-20 contracts
    """
    price_helper = UniswapV3PriceHelper(uniswap)

    if intermediate_token_address:
        path = [base_token_address, intermediate_token_address, quote_token_address]
        fees = [intermediate_pair_fee, target_pair_fee]
    else:
        path = [base_token_address, quote_token_address]
        fees = [target_pair_fee]

    return price_helper.get_amount_out(quantity_raw, path, fees, slippage=slippage)