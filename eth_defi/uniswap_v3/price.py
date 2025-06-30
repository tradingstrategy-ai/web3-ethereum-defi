"""Uniswap v3 price calculations.

Helpers to calculate

- `Price impact <https://tradingstrategy.ai/glossary/price-impact>`__

- `Slippage <https://tradingstrategy.ai/glossary/slippage>`__

- `Mid price <https://tradingstrategy.ai/glossary/mid-price>`__

Example:

.. code-block:: python

    import os
    from decimal import Decimal

    from eth_defi.provider.multi_provider import create_multi_provider_web3
    from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS
    from eth_defi.uniswap_v3.deployment import fetch_deployment
    from eth_defi.uniswap_v3.pool import fetch_pool_details
    from eth_defi.uniswap_v3.price import get_onchain_price, estimate_buy_received_amount
    from eth_defi.uniswap_v3.tvl import fetch_uniswap_v3_pool_tvl


    def main():
        # You can pass your own endpoint in an environment variable
        json_rpc_url = os.environ.get("JSON_RPC_POLYGON", "https://polygon-rpc.com")

        # Search pair contract addresses using Trading Strategy search: https://tradingstrategy.ai/search
        # This one is:
        # https://tradingstrategy.ai/trading-view/polygon/uniswap-v3/eth-usdc-fee-5
        pool_address = os.environ.get("PAIR_ADDRESS", "0x45dda9cb7c25131df268515131f647d726f50608")

        # Create web3 connection instance
        web3 = create_multi_provider_web3(json_rpc_url)

        contract_details = UNISWAP_V3_DEPLOYMENTS["polygon"]
        uniswap = fetch_deployment(
            web3,
            factory_address=contract_details["factory"],
            router_address=contract_details["router"],
            position_manager_address=contract_details["position_manager"],
            quoter_address=contract_details["quoter"],
        )

        # Get Pool contract ABI file, prepackaged in eth_defi Python package
        # and convert it to a wrapped Python object
        pool = fetch_pool_details(web3, pool_address)

        inverse = True

        # Manually resolve token order from random Uniswap v3 order
        if inverse:
            base_token = pool.token1
            quote_token = pool.token0
        else:
            base_token = pool.token0
            quote_token = pool.token1

        # Print out pool details
        # token0 and token1 will be always in a random order
        # and may inverse the price
        print("-" * 80)
        print("Uniswap pool details")
        print("Chain", web3.eth.chain_id)
        print("Pool", pool_address)
        print("Token0", pool.token0.symbol)
        print("Token1", pool.token1.symbol)
        print("Base token", base_token.symbol)
        print("Quote token", quote_token.symbol)
        print("Fee (BPS)", pool.get_fee_bps())
        print("-" * 80)

        inverse = True  # Is price inverted for output

        # Record the block number close to our timestamp
        block_num = web3.eth.get_block_number()

        # Use get_onchain_price() to get a human readable price
        # in Python Decimal
        mid_price = get_onchain_price(
            web3,
            pool.address,
        )

        if inverse:
            mid_price = 1 / mid_price

        target_pair_fee_bps = 5

        # Attempt to buy ETH wit $1,000,000.50
        swap_amount = Decimal("1_000_000.50")
        swap_amount_raw = quote_token.convert_to_raw(swap_amount)

        received_amount_raw = estimate_buy_received_amount(
            uniswap=uniswap,
            base_token_address=base_token.address,
            quote_token_address=quote_token.address,
            quantity=swap_amount_raw,
            target_pair_fee=target_pair_fee_bps * 100,  # Uniswap v3 units
            block_identifier=block_num,
        )

        received_amount = base_token.convert_to_decimals(received_amount_raw)

        quoted_price = received_amount / swap_amount

        if inverse:
            quoted_price = 1 / quoted_price

        price_impact = (quoted_price - mid_price) / mid_price

        tvl_quote = fetch_uniswap_v3_pool_tvl(
            pool,
            quote_token,
            block_identifier=block_num,
        )

        tvl_base = fetch_uniswap_v3_pool_tvl(
            pool,
            base_token,
            block_identifier=block_num,
        )

        print(f"Block: {block_num:,}")
        print(f"Swap size: {swap_amount:,.2f} {quote_token.symbol}")
        print(f"Pool base token TVL: {tvl_base:,.2f} {base_token.symbol}")
        print(f"Pool quote token TVL: {tvl_quote:,.2f} {quote_token.symbol}")
        print(f"Mid price {base_token.symbol} / {quote_token.symbol}: {mid_price:,.2f}")
        print(f"Quoted amount to received: {received_amount:,.2f} {base_token.symbol}")
        print(f"Quoted price (no execution slippage): {quoted_price:,.2f} {quote_token.symbol}")
        print(f"Price impact: {price_impact * 100:.2f}%")


    if __name__ == "__main__":
        main()



See :ref:`slippage and price impact` tutorial for more information.

"""

import logging
from decimal import Decimal

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.uniswap_v3.deployment import UniswapV3Deployment
from eth_defi.uniswap_v3.pool import fetch_pool_details
from eth_defi.uniswap_v3.utils import encode_path


logger = logging.getLogger(__name__)


class QuotingFailed(Exception):
    """QuoterV2 pukes ouk revert."""


class UniswapV3PriceHelper:
    """Internal helper class for price calculations."""

    def __init__(
        self,
        uniswap_v3: UniswapV3Deployment,
    ):
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

        if self.deployment.quoter_v2:
            # https://github.com/Uniswap/v3-periphery/blob/main/contracts/lens/QuoterV2.sol
            # https://basescan.org/address/0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a#readContract
            encoded_path = encode_path(path, fees)

            logger.info(
                "QuoterV2: quoting get_amount_out(), path: %s, fees: %s, amount_in: %s",
                path,
                fees,
                amount_in,
            )

            try:
                quote_data = self.deployment.quoter.functions.quoteExactInput(
                    encoded_path,
                    amount_in,
                ).call(block_identifier=block_identifier)
            except ValueError as e:
                raise QuotingFailed(f"Quoting failed for QuoterV2. Path: {path}, fees: {fees}, amount_in: {amount_in}") from e

            # quote_data is
            #
            # [1328788900753233521503, [11265578586930540950876463126030008], [736], 38846907]
            #             uint256 amountOut,
            #             uint160[] memory sqrtPriceX96AfterList,
            #             uint32[] memory initializedTicksCrossedList,
            #             uint256 gasEstimate

            amount_out = quote_data[0]
        else:
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

        assert not self.deployment.quoter_v2, "QuoterV2 support not yet added to get_amount_in()"

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
    """Estimate how much we receive for buying with a certain quote token amount.

    Example:

    .. code-block:: python

        # Estimate the price of buying 1650 USDC worth of ETH
        eth_received = estimate_buy_received_amount(
            uniswap_v3,
            weth.address,
            usdc.address,
            1650 * 10**18,  # Must be raw token units
            500,  # 100 Uniswap v3 fee units = 1 BPS, this is 5 BPS
        )

        assert eth_received / (10**18) == pytest.approx(0.9667409780905836)

        # Calculate price of ETH as $ for our purchase
        price = (1650 * 10**18) / eth_received
        assert price == pytest.approx(Decimal(1706.7653460381143))

    :param quantity:
        How much of the base token we want to buy.

        Expressed in raw token.

    :param uniswap:
        Uniswap v3 deployment

    :param base_token_address:
        Base token address of the trading pair

    :param quote_token_address:
        Quote token address of the trading pair

    :param target_pair_fee:
        Trading fee of the target pair in Uniswap v3 fee units.

        100 units = 1 BPS.

    :param slippage:
        Slippage express in bps.
        The amount will be estimated for the maximum slippage.

    :param block_identifier:
        A specific block to estimate price.

        Either block number or a block hash.

    :param verbose:
        If True, return more debug info

    :return:
        Expected base token amount to receive

    :raise TokenDetailError:
        If we have an issue with ERC-20 contracts
    """
    price_helper = UniswapV3PriceHelper(uniswap)

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
        fees = [target_pair_fee, intermediate_pair_fee]
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
