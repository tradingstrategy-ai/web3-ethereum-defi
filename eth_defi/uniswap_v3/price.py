"""Uniswap v3 price calculations.

- You can estimate the future price w/slippage

- You can check for the past price impact and slippage

Here is an example how to use price calculation to calculate the historical price impact of WMATIC->USDC trade on Polygon using 5 BPS fee tier
for two different blocks:

.. code-block:: python

    import os
    from decimal import Decimal

    from web3 import Web3, HTTPProvider

    from eth_defi.token import fetch_erc20_details
    from eth_defi.uniswap_v3.deployment import fetch_deployment
    from eth_defi.uniswap_v3.price import get_onchain_price, estimate_sell_received_amount

    params = {"path":"0x0d500b1d8e8ef31e21c99d1db9a6444d3adf12700001f42791bca1f2de4661ed88a30c99a7a9449aa84174","recipient":"0x19f61a2cdebccbf500b24a1330c46b15e5f54cbc","deadline":"9223372036854775808","amountIn":"14975601230579683413","amountOutMinimum":"10799953"}

    amount_in = 14975601230579683413
    path = params["path"]
    # https://tradingstrategy.ai/trading-view/polygon/uniswap-v3/matic-usdc-fee-5
    pool_address = "0xa374094527e1673a86de625aa59517c5de346d32"
    block_estimated = 45_583_631
    block_executed = 45_583_635

    wmatic_address = "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270"
    usdc_address = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    wmatic_amount = Decimal("14.975601230579683413")
    fee_tier = 0.0005  # BPS

    # What is the max slippage value for Uniswap,
    # as slippage is irrelevant in our
    # calculations
    max_slippage = 10000

    json_rpc_url = os.environ["JSON_RPC_POLYGON"]
    web3 = Web3(HTTPProvider(json_rpc_url))

    wmatic = fetch_erc20_details(web3, wmatic_address)
    usdc = fetch_erc20_details(web3, usdc_address)

    wmatic_amount_raw = wmatic.convert_to_raw(wmatic_amount)

    mid_price_estimated = get_onchain_price(web3, pool_address, block_identifier=block_estimated)
    mid_price_executed = get_onchain_price(web3, pool_address, block_identifier=block_executed)

    print(f"Mid price when estimate at block {block_estimated:,}:", mid_price_estimated)
    print(f"Mid price at the time of execution at block {block_executed:,}:", mid_price_executed)
    print(f"Price difference {(mid_price_executed - mid_price_estimated) / mid_price_estimated * 100:.2f}%")

    # Uniswap v4 deployment addresses are the same across the chains
    # https://docs.uniswap.org/contracts/v3/reference/deployments
    uniswap = fetch_deployment(
        web3,
        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
    )

    estimated_sell_raw = estimate_sell_received_amount(
        uniswap,
        base_token_address=wmatic_address,
        quote_token_address=usdc_address,
        quantity=wmatic_amount_raw,
        target_pair_fee=int(fee_tier * 1_000_000),
        block_identifier=block_estimated,
        slippage=max_slippage,
    )
    estimated_sell = usdc.convert_to_decimals(estimated_sell_raw)

    print(f"Estimated quantity: {estimated_sell}")

    executed_sell_raw = estimate_sell_received_amount(
        uniswap,
        base_token_address=wmatic_address,
        quote_token_address=usdc_address,
        quantity=wmatic_amount_raw,
        target_pair_fee=int(fee_tier * 1_000_000),
        block_identifier=block_executed,
        slippage=max_slippage,
    )
    executed_sell = usdc.convert_to_decimals(executed_sell_raw)

    print(f"Executed quantity: {executed_sell}")

    print(f"Supposed price impact {(executed_sell - estimated_sell) / estimated_sell * 100:.2f}%")

"""

from decimal import Decimal

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.uniswap_v3.deployment import UniswapV3Deployment
from eth_defi.uniswap_v3.pool import fetch_pool_details
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
    :param slippage: Slippage express in bps
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
    :param slippage: Slippage express in bps
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
    """Get the current price of a Uniswap pool.

    :param web3: Web3 instance
    :param pool_contract_address: Contract address of the pool
    :param block_identifier: A specific block to query price
    :param reverse_token_order: If set, assume quote token is token0
    :return: Current price
    """
    pool_details = fetch_pool_details(web3, pool_contract_address)
    _, tick, *_ = pool_details.pool.functions.slot0().call(block_identifier=block_identifier)

    return pool_details.convert_price_to_human(tick, reverse_token_order)
