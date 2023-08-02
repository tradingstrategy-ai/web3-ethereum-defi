"""Uniswap v3 price calculations.

See :ref:`slippage and price impact` tutorial.

Helpers to calculate

- `price impact <https://tradingstrategy.ai/glossary/price-impact>`__

- `slippage <https://tradingstrategy.ai/glossary/slippage>`__

- `mid price <https://tradingstrategy.ai/glossary/mid-price>`__

"""

from decimal import Decimal

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.uniswap_v3.deployment import UniswapV3Deployment
from eth_defi.uniswap_v3.pool import fetch_pool_details
from eth_defi.uniswap_v3.utils import encode_path


class UniswapV3PriceHelper:
    """Internal helper class for price calculations."""

    def __init__(self, uniswap_v3: UniswapV3Deployment):
        self.deployment = uniswap_v3

    def get_amount_out(
        self,
        amount_in: int,
        path: list[HexAddress],
        fees: list[int],
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
        :param block_identifier: A specific block to estimate price
        """
        self.validate_args(path, fees, slippage, amount_in)

        encoded_path = encode_path(path, fees)
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
        *,
        slippage: float = 0,
        block_identifier: int | None = None,
    ) -> int:
        """Get how much token we are going to spend.

        :param amount_in: Amount of output asset.
        :param path: List of token addresses how to route the trade
        :param fees: List of trading fees of the pools in the route
        :param slippage: Slippage express in bps
        :param block_identifier: A specific block to estimate price
        """
        self.validate_args(path, fees, slippage, amount_out)

        encoded_path = encode_path(path, fees, exact_output=True)
        amount_in = self.deployment.quoter.functions.quoteExactOutput(
            encoded_path,
            amount_out,
        ).call(block_identifier=block_identifier)

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
    block_identifier: int | None = None,
    verbose: bool = False,
) -> int | tuple[int, int]:
    """Estimate how much we receive for buying with a certain quote token amount.

    Example:

    .. code-block:: python

        # Estimate the price of buying 1650 USDC worth of ETH
        eth_received = estimate_buy_received_amount(
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

    See another example in :py:mod:`eth_defi.uniswap_v3.price`.

    :param quantity: How much of the base token we want to buy
    :param uniswap: Uniswap v3 deployment
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
    fee_helper = UniswapV3PriceHelper(uniswap)

    if intermediate_token_address:
        path = [quote_token_address, intermediate_token_address, base_token_address]
        fees = [intermediate_pair_fee, target_pair_fee]
    else:
        path = [quote_token_address, base_token_address]
        fees = [target_pair_fee]

    # We will receive equal number of amounts as there are items in the path
    amount = fee_helper.get_amount_out(
        quantity,
        path,
        fees,
        slippage=slippage,
        block_identifier=block_identifier,
    )

    # return more debug info in verbose mode
    if verbose:
        current_block = block_identifier or uniswap.web3.eth.block_number
        return amount, current_block

    return amount


def estimate_sell_received_amount(
    uniswap: UniswapV3Deployment,
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

    See example in :py:mod:`eth_defi.uniswap_v3.price`.

    :param quantity: How much of the base token we want to buy
    :param uniswap: Uniswap v3 deployment
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
    price_helper = UniswapV3PriceHelper(uniswap)

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
        current_block = block_identifier or uniswap.web3.eth.block_number
        return amount, current_block

    return amount


def get_onchain_price(
    web3: Web3,
    pool_contract_address: str,
    *,
    block_identifier: int | None = None,
    reverse_token_order: bool = False,
):
    """Get the current price of a Uniswap v3 pool.

    Reads Uniswap v3 "slot 0" price.

    - This is the `current price <https://blog.uniswap.org/uniswap-v3-math-primer#how-do-i-calculate-the-current-exchange-rate>`__
      according to Uniswap team explanation, which we assume is the mid-price

    - See `mid price <https://tradingstrategy.ai/glossary/mid-price>`__

    To read the latest ETH-USDC price on Polygon:

    .. code-block:: python

        import os
        from web3 import Web3, HTTPProvider

        from eth_defi.uniswap_v3.price import get_onchain_price

        json_rpc_url = os.environ["JSON_RPC_POLYGON"]
        web3 = Web3(HTTPProvider(json_rpc_url))

        # ETH-USDC 5 BPS pool address
        # https://tradingstrategy.ai/trading-view/polygon/uniswap-v3/eth-usdc-fee-5
        pool_address = "0x45dda9cb7c25131df268515131f647d726f50608"

        price = get_onchain_price(web3, pool_address, reverse_token_order=True)
        print(f"ETH price is {price:.2f} USD")

    :param web3:
        Web3 instance

    :param pool_contract_address:
        Contract address of the Uniswap v3 pool

    :param block_identifier:
        A specific block to query price.

        Block number or block hash.

    :param reverse_token_order:
        For switching the pair ticker around to make it human readable.

        - If set, assume quote token is token0, and the human price is 1/price
        - If not set assumes base token is token0

    :return:
        Current price in human-readable Decimal format.
    """
    pool_details = fetch_pool_details(web3, pool_contract_address)
    _, tick, *_ = pool_details.pool.functions.slot0().call(block_identifier=block_identifier)

    return pool_details.convert_price_to_human(tick, reverse_token_order)
