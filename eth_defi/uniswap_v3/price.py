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


def estimate_buy_received_amount(
    uniswap: UniswapV3Deployment,
    base_token_address: HexAddress,
    quote_token_address: HexAddress,
    quantity: Decimal | int,
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
    return fee_helper.get_amount_out(quantity, path, fees, slippage=slippage)


def estimate_sell_received_amount(
    uniswap: UniswapV3Deployment,
    base_token_address: HexAddress,
    quote_token_address: HexAddress,
    quantity: Decimal | int,
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

    return price_helper.get_amount_out(quantity, path, fees, slippage=slippage)
