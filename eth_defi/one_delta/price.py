"""1delta price calculations.

See :ref:`slippage and price impact` tutorial.

Helpers to calculate

- `price impact <https://tradingstrategy.ai/glossary/price-impact>`__

- `slippage <https://tradingstrategy.ai/glossary/slippage>`__

- `mid price <https://tradingstrategy.ai/glossary/mid-price>`__

"""

from decimal import Decimal

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.aave_v3.constants import AaveV3InterestRateMode
from eth_defi.one_delta.constants import Exchange
from eth_defi.one_delta.deployment import OneDeltaDeployment
from eth_defi.one_delta.utils import encode_quoter_path


class OneDeltaPriceHelper:
    """Internal helper class for price calculations."""

    def __init__(self, one_delta: OneDeltaDeployment):
        self.deployment = one_delta

    def get_amount_out(
        self,
        amount_in: int,
        path: list[HexAddress],
        fees: list[int],
        exchange: Exchange = Exchange.UNISWAP_V3,
        *,
        slippage: float = 0,
        block_identifier: int | None = None,
    ) -> int:
        """Get how much token we are going to receive.

        Example:

        .. code-block:: python

            # Estimate how much DAI we will receive for 1000 WETH
            # using the route of 2 pools: WETH/USDC 0.3% and USDC/DAI 1%
            # with slippage tolerance is 0.5%
            price_helper = OneDeltaPriceHelper(uniswap_v3_deployment)
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
        :param exchange: exchange to be used for the swap
        :param slippage: Slippage express in bps
        :param block_identifier: A specific block to estimate price
        """
        self._validate_args(path, fees, slippage, amount_in)

        encoded_path = encode_quoter_path(
            path=path,
            fees=fees,
            exchanges=[exchange],
        )

        amount_out = self.deployment.quoter.functions.quoteExactInput(
            encoded_path,
            amount_in,
        ).call(block_identifier=block_identifier)

        return int(amount_out * 10_000 // (10_000 + slippage))

    def get_amount_in(
        self,
        amount_out: int,
        path: list[HexAddress],
        fees: list[int],
        exchange: Exchange = Exchange.UNISWAP_V3,
        *,
        slippage: float = 0,
        block_identifier: int | None = None,
    ) -> int:
        """Get how much token we are going to spend.

        :param amount_in: Amount of output asset.
        :param path: List of token addresses how to route the trade
        :param fees: List of trading fees of the pools in the route
        :param exchange: exchange to be used for the swap
        :param slippage: Slippage express in bps
        :param block_identifier: A specific block to estimate price
        """
        self._validate_args(path, fees, slippage, amount_out)

        encoded_path = encode_quoter_path(
            path=path,
            fees=fees,
            exchanges=[exchange],
        )

        amount_in = self.deployment.quoter.functions.quoteExactOutput(
            encoded_path,
            amount_out,
        ).call(block_identifier=block_identifier)

        return int(amount_in * (10_000 + slippage) // 10_000)

    def _validate_args(self, path, fees, slippage, amount):
        assert len(path) >= 2
        assert len(fees) == len(path) - 1
        assert slippage >= 0
        assert type(amount) == int, "Incorrect type provided for amount. Require int"
