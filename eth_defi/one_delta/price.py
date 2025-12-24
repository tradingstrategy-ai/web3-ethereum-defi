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


def estimate_buy_received_amount(
    one_delta_deployment: OneDeltaDeployment,
    base_token_address: HexAddress,
    quote_token_address: HexAddress,
    quantity: Decimal | int,
    target_pair_fee: int,
    *,
    slippage: float = 0,
    intermediate_token_address: HexAddress | None = None,
    intermediate_pair_fee: int | None = None,
    block_identifier: int | None = None,
    verbose: bool = False,
) -> int | tuple[int, int]:
    """Estimate how much we receive for buying with a certain quote token amount.

    Example:

    .. code-block:: python

        # Estimate the price of buying 1650 USDC worth of ETH
        eth_received = estimate_buy_received_amount(
            one_delta_deployment,
            weth.address,
            usdc.address,
            1650 * 10**18,
            500,
        )

        assert eth_received / (10**18) == pytest.approx(0.9667409780905836)

        # Calculate price of ETH as $ for our purchase
        price = (1650 * 10**18) / eth_received
        assert price == pytest.approx(Decimal(1706.7653460381143))

    :param quantity: How much of the base token we want to buy
    :param one_delta_deployment: 1delta deployment
    :param base_token_address: Base token address of the trading pair
    :param quote_token_address: Quote token address of the trading pair
    :param target_pair_fee: Trading fee of the target pair in raw format

    :param slippage:
        Slippage express in bps.
        The amount will be estimated for the maximum slippage.

    :param block_identifier: A specific block to estimate price
    :param verbose: If True, return more debug info
    :return: Expected base token amount to receive
    :raise TokenDetailError: If we have an issue with ERC-20 contracts
    """
    price_helper = OneDeltaPriceHelper(one_delta_deployment)

    if intermediate_token_address:
        path = [quote_token_address, intermediate_token_address, base_token_address]
        fees = [intermediate_pair_fee, target_pair_fee]
    else:
        path = [quote_token_address, base_token_address]
        fees = [target_pair_fee]

    amount = price_helper.get_amount_out(
        quantity,
        path,
        fees,
        slippage=slippage,
        block_identifier=block_identifier,
    )

    # return more debug info in verbose mode
    if verbose:
        current_block = block_identifier or one_delta_deployment.web3.eth.block_number
        return amount, current_block

    return amount


def estimate_sell_received_amount(
    one_delta_deployment: OneDeltaDeployment,
    base_token_address: HexAddress | str,
    quote_token_address: HexAddress | str,
    quantity: Decimal | int,
    target_pair_fee: int,
    *,
    slippage: float = 0,
    intermediate_token_address: HexAddress | None = None,
    intermediate_pair_fee: int | None = None,
    block_identifier: int | None = None,
    verbose: bool = False,
) -> int | tuple[int, int]:
    """Estimate how much we receive for selling a certain base token amount.

    See example in :py:mod:`eth_defi.one_delta.price`.

    :param quantity: How much of the base token we want to sell

    :param base_token_address: Base token address of the trading pair
    :param quote_token_address: Quote token address of the trading pair
    :param target_pair_fee: Trading fee of the target pair in raw format

    :param slippage:
        Slippage express in bps.
        The amount will be estimated for the maximum slippage.

    :param block_identifier: A specific block to estimate price
    :param verbose: If True, return more debug info
    :return: Expected quote token amount to receive
    :raise TokenDetailError: If we have an issue with ERC-20 contracts
    """
    price_helper = OneDeltaPriceHelper(one_delta_deployment)

    if intermediate_token_address:
        path = [base_token_address, intermediate_token_address, quote_token_address]
        fees = [intermediate_pair_fee, target_pair_fee]
    else:
        path = [base_token_address, quote_token_address]
        fees = [target_pair_fee]

    amount = price_helper.get_amount_out(
        quantity,
        path,
        fees,
        slippage=slippage,
        block_identifier=block_identifier,
    )

    # return more debug info in verbose mode
    if verbose:
        current_block = block_identifier or one_delta_deployment.web3.eth.block_number
        return amount, current_block

    return amount
