"""Uniswap v2 price impact and fee calculations.

`Mostly lifted from Uniswap-v2-py MIT licensed by Asynctomatic <https://github.com/nosofa/uniswap-v2-py>`_.
"""

from decimal import Decimal
from typing import List

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_hentai.token import fetch_erc20_details
from eth_hentai.uniswap_v2.deployment import UniswapV2Deployment
from eth_hentai.uniswap_v2.utils import pair_for, sort_tokens


def get_amount_in(amount_out, reserve_in, reserve_out):
    """
    Returns the minimum input asset amount required to buy the given
    output asset amount (accounting for fees) given reserves.
    :param amount_out: Amount of output asset.
    :param reserve_in: Reserve of input asset in the pair contract.
    :param reserve_out: Reserve of input asset in the pair contract.
    :return: Required amount of input asset.
    """
    assert amount_out > 0
    assert reserve_in > 0 and reserve_out > 0
    numerator = reserve_in*amount_out*1000
    denominator = (reserve_out - amount_out)*997
    return int(numerator/denominator + 1)


def get_amount_out(amount_in: int, reserve_in: int, reserve_out: int):
    """Given an input asset amount, returns the maximum output amount of the other asset (accounting for fees) given reserves.

    :param amount_in: Amount of input asset.
    :param reserve_in: Reserve of input asset in the pair contract.
    :param reserve_out: Reserve of input asset in the pair contract.
    :return: Maximum amount of output asset.
    """
    assert amount_in > 0
    assert reserve_in > 0 and reserve_out > 0
    amount_in_with_fee = amount_in*997  # 30 bps fee baked in
    numerator = amount_in_with_fee*reserve_out
    denominator = reserve_in*1000 + amount_in_with_fee
    return int(numerator/denominator)


class UniswapV2FeeCalculator:
    """A helper class to estimate Uniswap fees."""

    def __init__(self, uniswap_v2: UniswapV2Deployment):
        self.deployment = uniswap_v2

    # Liften from uniswap-v2-py by Asynctomatic
    def get_reserves(self, token_a: HexAddress, token_b: HexAddress):
        """Gets the reserves of token_0 and token_1 used to price trades
        and distribute liquidity as well as the timestamp of the last block
        during which an interaction occurred for the pair.

        :param pair: Address of the pair.
        :return:
            - reserve_0 - Amount of token_0 in the contract.
            - reserve_1 - Amount of token_1 in the contract.
            - liquidity - Unix timestamp of the block containing the last pair interaction.
        """
        assert token_a.startswith("0x")
        assert token_b.startswith("0x")
        (token0, token1) = sort_tokens(token_a, token_b)
        pair_contract = self.deployment.PairContract(
            address=Web3.toChecksumAddress(
                pair_for(self.deployment.factory.address, token_a, token_b, self.deployment.init_code_hash)),
            )
        reserve = pair_contract.functions.getReserves().call()
        return reserve if token0 == token_a else [reserve[1], reserve[0], reserve[2]]

    def get_amounts_out(self, amount_in: int, path: List[HexAddress]) -> List[int]:
        """Get how much token we are going to receive.

        :param amount_in:
        :param path: List of token addresses how to route the trade
        :return:
        """
        assert len(path) >= 2
        amounts = [amount_in]
        current_amount = amount_in
        for p0, p1 in zip(path, path[1:]):
            r = self.get_reserves(p0, p1)
            current_amount = get_amount_out(
                current_amount, r[0], r[1]
            )
            amounts.append(current_amount)
        return amounts

    def get_amounts_in(self, amount_out, path):
        assert len(path) >= 2
        amounts = [amount_out]
        current_amount = amount_out
        for p0, p1 in reversed(list(zip(path, path[1:]))):
            r = self.get_reserves(p0, p1)
            current_amount = get_amount_in(
                current_amount, r[0], r[1]
            )
            amounts.insert(0, current_amount)
        return amounts


def estimate_buy_quantity(uniswap: UniswapV2Deployment, base_token: Contract, quote_token: Contract, quantity: int) -> int:
    """Estimate how many tokens we are going to receive when doing a buy..

    Calls the on-chain contract to get the current liquidity and estimates the
    the price based on it.s

    Example:

    .. code-block:: python

            # Estimate how much ETH we will receive for 500 USDC.
            # In this case the pool ETH price is $1700 so this should be below ~1/4 of ETH
            amount_eth = estimate_received_quantity(
                uniswap_v2,
                weth,
                usdc,
                500*10**18,
            )
            assert amount_eth / 1e18 == pytest.approx(0.28488156127668085)

    :param web3: Web3 instance
    :param quantity: How much of the base token we want to buy
    :param uniswap: Uniswap v2 deployment
    :param base_token: Base token of the trading pair
    :param quote_token: Quote token of the trading pair
    :return: Expected base token to receive
    """
    fee_helper = UniswapV2FeeCalculator(uniswap)
    path = [quote_token.address, base_token.address]
    amounts = fee_helper.get_amounts_out(quantity, path)
    return amounts[-1]


def estimate_sell_price(uniswap: UniswapV2Deployment, base_token: Contract, quote_token: Contract, quantity: int) -> int:
    """Estimate how much we are going to get paid when doing a sell.

    Calls the on-chain contract to get the current liquidity and estimates the
    the price based on it.

    .. note ::

        The price of an asset depends on how much you are selling it. More you sell,
        more there will be price impact.

    To get a price of an asset, ask for quantity 1 of it:

    .. code-block:: python

        # Create the trading pair and add initial liquidity for price 1700 USDC/ETH
        deploy_trading_pair(
            web3,
            deployer,
            uniswap_v2,
            weth,
            usdc,
            1_000 * 10**18,  # 1000 ETH liquidity
            1_700_000 * 10**6,  # 1.7M USDC liquidity
        )

        # Estimate the price of selling 1 ETH
        usdc_per_eth = estimate_price(
            uniswap_v2,
            weth,
            usdc,
            1 * 10**18,  # 1 ETH
        )
        price_as_usd = usdc_per_eth / 1e6
        assert price_as_usd == pytest.approx(1693.2118677678354)

    :param quantity: How much of the base token we want to sell
    :param uniswap: Uniswap v2 deployment
    :param base_token: Base token of the trading pair
    :param quote_token: Quote token of the trading pair
    :return: Expected quote token amount to receive
    """
    fee_helper = UniswapV2FeeCalculator(uniswap)
    path = [base_token.address, quote_token.address]
    amounts = fee_helper.get_amounts_out(quantity, path)
    return amounts[-1]


def estimate_buy_price_decimals(uniswap: UniswapV2Deployment, base_token_address: HexAddress, quote_token_address: HexAddress, quantity: Decimal) -> Decimal:
    """Estimate how much we are going to need to pay when doing buy.

    Much like :py:func:`estimate_buy_quantity` with the differences of
    - Tokens are passed as address instead of contract instance
    - We use base token quantity units instead of cash
    - We use decimals instead of raw token amounts

    Example:

    .. code-block:: python

        # Create the trading pair and add initial liquidity
        deploy_trading_pair(
            web3,
            deployer,
            uniswap_v2,
            weth,
            usdc,
            1_000 * 10**18,  # 1000 ETH liquidity
            1_700_000 * 10**18,  # 1.7M USDC liquidity
        )

        # Estimate the price of buying 1 ETH
        usdc_per_eth = estimate_buy_price_decimals(
            uniswap_v2,
            weth.address,
            usdc.address,
            Decimal(1.0),
        )
        assert usdc_per_eth == pytest.approx(Decimal(1706.82216820632059904))

    :param quantity: How much of the base token we want to buy
    :param uniswap: Uniswap v2 deployment
    :param base_token: Base token of the trading pair
    :param quote_token: Quote token of the trading pair
    :return: Expected quote token amount to receive
    :raise TokenDetailError: If we have an issue with ERC-20 contracts
    """
    web3 = uniswap.web3
    base = fetch_erc20_details(web3, base_token_address)
    quote = fetch_erc20_details(web3, quote_token_address)
    quantity_raw = base.convert_to_raw(quantity)
    fee_helper = UniswapV2FeeCalculator(uniswap)
    path = [quote_token_address, base_token_address]
    amounts = fee_helper.get_amounts_in(quantity_raw, path)
    in_raw = amounts[0]
    return quote.convert_to_decimals(in_raw)


def estimate_sell_price_decimals(uniswap: UniswapV2Deployment, base_token_address: HexAddress, quote_token_address: HexAddress, quantity: Decimal) -> Decimal:
    """Estimate how much we are going to get paid when doing a sell.

    Much like :py:func:`estimate_sell_price` but in/out is expressed as python Decimal units.
    Furthermore, no ERC-20 token contract needed ABI, but it is loaded by the function.

    :param quantity: How much of the base token we want to sell
    :param uniswap: Uniswap v2 deployment
    :param base_token: Base token of the trading pair
    :param quote_token: Quote token of the trading pair
    :return: Expected quote token amount to receive
    :raise TokenDetailError: If we have an issue with ERC-20 contracts
    """

    web3 = uniswap.web3
    base = fetch_erc20_details(web3, base_token_address)
    quote = fetch_erc20_details(web3, quote_token_address)
    quantity_raw = base.convert_to_raw(quantity)

    fee_helper = UniswapV2FeeCalculator(uniswap)
    path = [base_token_address, quote_token_address]
    amounts = fee_helper.get_amounts_out(quantity_raw, path)

    out_raw = amounts[-1]
    return quote.convert_to_decimals(out_raw)
