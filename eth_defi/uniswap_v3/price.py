"""Uniswap v3 price calculations."""
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
