import logging
import random
from collections import Counter
from decimal import Decimal

from eth_tester import EthereumTester
from eth_typing import HexAddress
from web3 import Web3, EthereumTesterProvider

from eth_defi.token import TokenDetails
from eth_defi.uniswap_v2.deployment import deploy_trading_pair, UniswapV2Deployment
from eth_defi.uniswap_v2.fees import estimate_sell_price_decimals
from eth_defi.uniswap_v2.pair import fetch_pair_details
from eth_defi.uniswap_v2.swap import swap_with_slippage_protection


logger = logging.getLogger(__name__)


def generate_fake_uniswap_v2_data(
    uniswap_v2: UniswapV2Deployment,
    deployer: HexAddress,
    base_token: TokenDetails,
    quote_token: TokenDetails,
    base_liquidity=100 * 10**18,  # 100 ETH liquidity
    quote_liquidity=1600 * 100 * 10**6,  # 170,000 USDC liquidity,
    number_of_blocks=int(5*60 / 12),  # 5 minutes, 12 sec block time
    block_time=12,  # 12 sec block time
    trades_per_block=3,  # Max 3 trades per block
    min_trade=-500,  # Max sell 500 USD
    max_trade=500,  # Max buy 500 USD
    random_seed=1,
) -> dict:
    """Create trades on EthereumTester Uniswap v2 instance.

    - Deterministic random number generator used

    - Generate random trading data for the price feeds tests

    - Uses Uniswap smart contracts on EtheruemTester chain for actual trading,
      is ABI compatible with a real deployment

    - Quote slow, around 2 trades per second,
      so use scarcely

    :return:
        Dictionary of some statistics about the generated trades
    """

    random_gen = random.Random(random_seed)

    web3 = uniswap_v2.web3

    eth_tester_provider = web3.provider

    assert isinstance(eth_tester_provider, EthereumTesterProvider)

    eth_tester: EthereumTester = eth_tester_provider.ethereum_tester

    stats = {
        "buys": 0,
        "sells": 0,
        "initial_price": Decimal(0),
        "min_price": Decimal(2**63),
        "max_price": Decimal(-2**63),
    }

    # Create the trading pair and add initial liquidity
    pair_address = deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        base_token.contract,
        quote_token.contract,
        base_liquidity,
        quote_liquidity
    )

    pair_details = fetch_pair_details(web3, pair_address)

    initial_price = estimate_sell_price_decimals(
        uniswap_v2,
        base_token.address,
        quote_token.address,
        quantity=Decimal(1),
    )

    logger.info("Deployed pair %s with initial price %s %s/%s", pair_details, initial_price, quote_token.symbol, base_token.symbol)

    stats["initial_price"] = initial_price

    trader = deployer

    # Set infinite approvals
    base_token.contract.functions.approve(uniswap_v2.router.address, 2**256-1).transact({"from": trader})
    quote_token.contract.functions.approve(uniswap_v2.router.address, 2**256-1).transact({"from": trader})

    eth_tester.disable_auto_mine_transactions()

    for block in range(number_of_blocks):

        trade_count = random_gen.randint(0, trades_per_block)
        block_number = web3.eth.block_number

        for trade in range(trade_count):

            quote_amount = Decimal(random_gen.uniform(min_trade, max_trade))

            # Sell base token
            price = estimate_sell_price_decimals(
                uniswap_v2,
                base_token.address,
                quote_token.address,
                quantity=Decimal(1),
            )

            if quote_amount > 0:
                # Sell base token

                # Convert from quote to base amount
                base_amount = quote_amount / price

                logger.info("Selling %s at %s for %s %s, block %d", base_token.symbol, price, base_amount, base_token.symbol, block_number)

                swap_func = swap_with_slippage_protection(
                    uniswap_v2_deployment=uniswap_v2,
                    recipient_address=trader,
                    base_token=base_token.contract,
                    quote_token=quote_token.contract,
                    amount_out=base_token.convert_to_raw(base_amount),
                    max_slippage=9999,  # 99%
                )

                stats["sells"] = stats["sells"] + 1

            else:
                quote_amount = abs(quote_amount)
                logger.info("Buying %s at %s for %s %s, block %d", base_token.symbol, price, quote_amount, quote_token.symbol, block_number)

                # Buy base token
                swap_func = swap_with_slippage_protection(
                    uniswap_v2_deployment=uniswap_v2,
                    recipient_address=trader,
                    base_token=base_token.contract,
                    quote_token=quote_token.contract,
                    amount_in=quote_token.convert_to_raw(quote_amount),
                    max_slippage=9999,  # 99%
                )

                stats["min_price"] = min(stats["min_price"], price)
                stats["max_price"] = max(stats["max_price"], price)
                stats["buys"] = stats["buys"] + 1

            swap_func.transact(
                {
                    "from": trader,
                    "gas": 350_000,  # estimate max 350k gas per swap
                })

        current_timestamp = eth_tester.get_block_by_number('pending')['timestamp']
        next_timestamp = current_timestamp + block_time
        eth_tester.time_travel(next_timestamp)
        eth_tester.mine_block()

    return stats
