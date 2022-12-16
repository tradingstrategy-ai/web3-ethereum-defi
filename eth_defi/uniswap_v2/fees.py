"""Uniswap v2 price impact and fee calculations.

`Mostly lifted from Uniswap-v2-py MIT licensed by Asynctomatic <https://github.com/nosofa/uniswap-v2-py>`_.
"""

from decimal import Decimal
from typing import Optional

from eth_typing import HexAddress
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput

from eth_defi.token import fetch_erc20_details
from eth_defi.uniswap_v2.deployment import INIT_CODE_HASH_MISSING, UniswapV2Deployment


class BadReserves(Exception):
    pass


class UniswapV2FeeCalculator:
    """A helper class to estimate Uniswap fees."""

    def __init__(self, uniswap_v2: UniswapV2Deployment):
        self.deployment = uniswap_v2

    # Liften from uniswap-v2-py by Asynctomatic
    def get_reserves(self, token_a: HexAddress, token_b: HexAddress) -> tuple[int]:
        """Gets the reserves of token_0 and token_1 used to price trades
        and distribute liquidity as well as the timestamp of the last block
        during which an interaction occurred for the pair.

        :param pair: Address of the pair.
        :return:
            - reserve_0 - Amount of token_0 in the contract.
            - reserve_1 - Amount of token_1 in the contract.
            - liquidity - Unix timestamp of the block containing the last pair interaction.
        """

        assert self.deployment.init_code_hash is not None, "Init hash not set"
        assert self.deployment.init_code_hash != INIT_CODE_HASH_MISSING, "You need to set init hash to use get_reserves()"

        assert token_a.startswith("0x")
        assert token_b.startswith("0x")

        # (token0, token1) = sort_tokens(token_a, token_b)
        pair_address, token0, token1 = self.deployment.pair_for(token_a, token_b)
        pair_contract = self.deployment.PairContract(pair_address)
        try:
            reserve = pair_contract.functions.getReserves().call()
        except BadFunctionCallOutput as e:
            raise BadReserves(f"Could not get reserves, bad pair contract {pair_address}, init hash {self.deployment.init_code_hash}, token_a {token_a}, token_b {token_b}?") from e
        return reserve if token0 == token_a else [reserve[1], reserve[0], reserve[2]]

    def get_amount_out(
        self,
        amount_in: int,
        path: list[HexAddress],
        *,
        fee: int = 30,
        slippage: float = 0,
    ) -> int:
        """Get how much token we are going to receive.

        :param amount_in: Amount of input asset.
        :param path: List of token addresses how to route the trade
        :param fee: Trading fee express in bps, default = 30 bps (0.3%)
        :param slippage: Slippage express in bps
        :return:
        """
        assert len(path) >= 2
        assert slippage >= 0
        amounts = [amount_in]
        current_amount = amount_in

        pairs = list(zip(path, path[1:]))
        for p0, p1 in pairs:
            r = self.get_reserves(p0, p1)
            current_amount = self.get_amount_out_from_reserves(current_amount, r[0], r[1], fee=fee)
            amounts.append(current_amount)

        amount_out = amounts[-1]
        return int(amount_out * 10_000 // (10_000 + slippage))

    def get_amount_in(
        self,
        amount_out: int,
        path: list[HexAddress],
        *,
        fee: int = 30,
        slippage: float = 0,
    ) -> int:
        """Get how much token we are going to spend.

        :param amount_out: Amount of output asset.
        :param path: List of token addresses how to route the trade
        :param fee: Trading fee express in bps, default = 30 bps (0.3%)
        :param slippage: Slippage express in bps
        :return:
        """
        assert len(path) >= 2
        assert slippage >= 0
        amounts = [amount_out]
        current_amount = amount_out

        pairs = reversed(list(zip(path, path[1:])))
        for p0, p1 in pairs:
            r = self.get_reserves(p0, p1)
            current_amount = self.get_amount_in_from_reserves(current_amount, r[0], r[1], fee=fee)
            amounts.insert(0, current_amount)

        amount_in = amounts[0]
        return int(amount_in * (10_000 + slippage) // 10_000)

    @staticmethod
    def get_amount_in_from_reserves(
        amount_out: int,
        reserve_in: int,
        reserve_out: int,
        *,
        fee: int = 30,
    ) -> int:
        """Returns the minimum input asset amount required to buy the given
        output asset amount (accounting for fees) given reserves.

        :param amount_out: Amount of output asset.
        :param reserve_in: Reserve of input asset in the pair contract.
        :param reserve_out: Reserve of output asset in the pair contract.
        :param fee: Trading fee express in bps, default = 30 bps (0.3%)
        :return: Required amount of input asset.
        """
        assert amount_out > 0
        assert reserve_in > 0 and reserve_out > 0
        numerator = reserve_in * amount_out * 10_000
        denominator = (reserve_out - amount_out) * (10_000 - fee)
        return numerator // denominator + 1

    @staticmethod
    def get_amount_out_from_reserves(
        amount_in: int,
        reserve_in: int,
        reserve_out: int,
        *,
        fee: int = 30,
    ) -> int:
        """Given an input asset amount, returns the maximum output amount of
        the other asset (accounting for fees) given reserves.

        :param amount_in: Amount of input asset.
        :param reserve_in: Reserve of input asset in the pair contract.
        :param reserve_out: Reserve of output asset in the pair contract.
        :param fee: Trading fee express in bps, default = 30 bps (0.3%)
        :return: Maximum amount of output asset.
        """
        assert amount_in > 0
        assert reserve_in > 0 and reserve_out > 0
        amount_in_with_fee = amount_in * (10_000 - fee)
        numerator = amount_in_with_fee * reserve_out
        denominator = reserve_in * 10_000 + amount_in_with_fee
        return numerator // denominator


def estimate_buy_quantity(
    uniswap: UniswapV2Deployment,
    base_token: Contract,
    quote_token: Contract,
    quantity: int,
    *,
    fee: int = 30,
    slippage: float = 0,
) -> int:
    """Estimate how many tokens we are going to receive when doing a buy.

    Good for doing a price impact calculations.

    Calls the on-chain contract to get the current liquidity and estimates the
    the price based on it.

    Example:

    .. code-block:: python

        # Estimate how much ETH we will receive for 500 USDC.
        # In this case the pool ETH price is $1700 so this should be below ~1/4 of ETH
        amount_eth = estimate_buy_quantity(
            uniswap_v2,
            weth,
            usdc,
            500*10**18,
        )
        assert amount_eth / 1e18 == pytest.approx(0.28488156127668085)

    :param quantity: How much of the quote token we have to use
    :param uniswap: Uniswap v2 deployment
    :param base_token: Base token of the trading pair
    :param quote_token: Quote token of the trading pair
    :param fee: Trading fee express in bps, default = 30 bps (0.3%)
    :param slippage: Slippage express in bps
    :return: Expected base token to receive
    """
    fee_helper = UniswapV2FeeCalculator(uniswap)
    path = [quote_token.address, base_token.address]
    return fee_helper.get_amount_out(quantity, path, fee=fee, slippage=slippage)


def estimate_buy_price(
    uniswap: UniswapV2Deployment,
    base_token: Contract,
    quote_token: Contract,
    quantity: int,
    *,
    fee: int = 30,
    slippage: float = 0,
    intermediate_token: Optional[Contract] = None,
) -> int:
    """Estimate how much we are going to need to pay when doing buy.

    Calls the on-chain contract to get the current liquidity and estimates the
    the price based on it.

    Example:

    .. code-block:: python

        # Estimate how much ETH we will receive for 500 USDC.
        # In this case the pool ETH price is $1700 so this should be below ~1/4 of ETH
        amount_eth = estimate_buy_price(
            uniswap_v2,
            weth,
            usdc,
            1*10**18,
        )
        assert amount_eth / 1e18 == pytest.approx(0.28488156127668085)

    :param uniswap: Uniswap v2 deployment
    :param base_token: Base token of the trading pair
    :param quote_token: Quote token of the trading pair
    :param quantity: How much of the base token we want to buy
    :param fee: Trading fee express in bps, default = 30 bps (0.3%)
    :param slippage: Slippage express in bps
    :return: Expected base token to receive
    """
    fee_helper = UniswapV2FeeCalculator(uniswap)
    if intermediate_token:
        path = [quote_token.address, intermediate_token.address, base_token.address]
    else:
        path = [quote_token.address, base_token.address]
    return fee_helper.get_amount_in(quantity, path, fee=fee, slippage=slippage)


def estimate_sell_price(
    uniswap: UniswapV2Deployment,
    base_token: Contract,
    quote_token: Contract,
    quantity: int,
    *,
    fee: int = 30,
    slippage: float = 0,
    intermediate_token: Optional[Contract] = None,
) -> int:
    """Estimate how much we are going to get paid when doing a sell.

    Calls the on-chain contract to get the current liquidity and estimates the
    the price based on it.

    .. note ::

        The price of an asset depends on how much you are selling it. More you sell,
        more there will be price impact.

    To get a price of an asset, ask for quantity 1 of it:

    .. code-block:: python

        # Create the trading pair and add iint(10_000 * amounts[-1] // (10_000 - slippage))nitial liquidity for price 1700 USDC/ETH
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
            weth,user_1
            usdc,
            1 * 10**18,  # 1 ETH
        )
        price_as_usd = usdc_per_eth / 1e6
        assert price_as_usd == pytest.approx(1693.2118677678354)

    :param quantity: How much of the base token we want to sell
    :param uniswap: Uniswap v2 deployment
    :param base_token: Base token of the trading pair
    :param quote_token: Quote token of the trading pair
    :param fee: Trading fee express in bps, default = 30 bps (0.3%)
    :param slippage: Slippage express in bps
    :return: Expected quote token amount to receive
    """
    fee_helper = UniswapV2FeeCalculator(uniswap)
    if intermediate_token:
        path = [base_token.address, intermediate_token.address, quote_token.address]
    else:
        path = [base_token.address, quote_token.address]
    return fee_helper.get_amount_out(quantity, path, fee=fee, slippage=slippage)


def estimate_buy_price_decimals(
    uniswap: UniswapV2Deployment,
    base_token_address: HexAddress,
    quote_token_address: HexAddress,
    quantity: Decimal,
    *,
    fee: int = 30,
    slippage: float = 0,
    intermediate_token_address: Optional[HexAddress] = None,
) -> Decimal:
    """Estimate how much we are going to need to pay when doing buy.

    Much like :py:func:`estimate_buy_price` with the differences of
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
    :param fee: Trading fee express in bps, default = 30 bps (0.3%)
    :param slippage: Slippage express in bps
    :return: Expected quote token amount to receive
    :raise TokenDetailError: If we have an issue with ERC-20 contracts
    """
    web3 = uniswap.web3
    quote = fetch_erc20_details(web3, quote_token_address, raise_on_error=False)
    quantity_raw = quote.convert_to_raw(quantity)
    fee_helper = UniswapV2FeeCalculator(uniswap)

    if intermediate_token_address:
        path = [quote_token_address, intermediate_token_address, base_token_address]
    else:
        path = [quote_token_address, base_token_address]

    in_raw = fee_helper.get_amount_in(quantity_raw, path, fee=fee, slippage=slippage)
    return quote.convert_to_decimals(in_raw)


def estimate_sell_price_decimals(
    uniswap: UniswapV2Deployment,
    base_token_address: HexAddress,
    quote_token_address: HexAddress,
    quantity: Decimal,
    *,
    fee: int = 30,
    slippage: float = 0,
    intermediate_token_address: Optional[HexAddress] = None,
) -> Decimal:
    """Estimate how much we are going to get paid when doing a sell.

    Much like :py:func:`estimate_sell_price` but in/out is expressed as python Decimal units.
    Furthermore, no ERC-20 token contract needed ABI, but it is loaded by the function.

    :param quantity:
        How much of the base token we want to sell,
        in token units (will be decimal autoconverted).

    :param uniswap:
        Uniswap v2 deployment

    :param base_token:
        Base token of the trading pair

    :param quote_token:
        Quote token of the trading pair

    :param fee:
        Trading fee express in bps, default = 30 bps (0.3%)

    :param slippage:
        Slippage express in bps

    :return:
        Expected quote token amount to receive in quota tokens (decimal converted).

    :raise TokenDetailError: If we have an issue with ERC-20 contracts
    """

    web3 = uniswap.web3
    base = fetch_erc20_details(web3, base_token_address, raise_on_error=False)
    quote = fetch_erc20_details(web3, quote_token_address, raise_on_error=False)
    quantity_raw = base.convert_to_raw(quantity)

    fee_helper = UniswapV2FeeCalculator(uniswap)
    if intermediate_token_address:
        path = [base_token_address, intermediate_token_address, quote_token_address]
    else:
        path = [base_token_address, quote_token_address]

    out_raw = fee_helper.get_amount_out(quantity_raw, path, fee=fee, slippage=slippage)
    return quote.convert_to_decimals(out_raw)


def estimate_buy_received_amount_raw(
    uniswap: UniswapV2Deployment,
    base_token_address: HexAddress,
    quote_token_address: HexAddress,
    quantity_raw: Decimal,
    *,
    fee: int = 30,
    slippage: float = 0,
    intermediate_token_address: Optional[HexAddress] = None,
) -> int:
    """Estimate how much we receive for a certain cash amount.

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

        # Estimate the price of buying 1650 USDC worth of ETH
        eth_received = estimate_buy_received_amount_raw(
            uniswap_v2,
            weth.address,
            usdc.address,
            1650 * 10**18,
        )

        assert eth_received / (10**18) == pytest.approx(0.9667409780905836)

        # Calculate price of ETH as $ for our purchase
        price = (1650*10**18) / eth_received
        assert price == pytest.approx(Decimal(1706.7653460381143))

    :param quantity: How much of the base token we want to buy
    :param uniswap: Uniswap v2 deployment
    :param base_token: Base token of the trading pair
    :param quote_token: Quote token of the trading pair
    :param fee: Trading fee express in bps, default = 30 bps (0.3%)
    :param slippage: Slippage express in bps
    :return: Expected quote token amount to receive
    :raise TokenDetailError: If we have an issue with ERC-20 contracts
    """
    fee_helper = UniswapV2FeeCalculator(uniswap)

    if intermediate_token_address:
        path = [quote_token_address, intermediate_token_address, base_token_address]
    else:
        path = [quote_token_address, base_token_address]

    # We will receive equal number of amounts as there are items in the path
    return fee_helper.get_amount_out(quantity_raw, path, fee=fee, slippage=slippage)


def estimate_sell_received_amount_raw(
    uniswap: UniswapV2Deployment,
    base_token_address: HexAddress,
    quote_token_address: HexAddress,
    quantity_raw: Decimal,
    *,
    fee: int = 30,
    slippage: float = 0,
    intermediate_token_address: Optional[HexAddress] = None,
) -> int:
    """Estimate how much cash we receive for a certain quantity of tokens sold.

    Example:

    .. code-block:: python

        deploy_trading_pair(
            web3,
            deployer,
            uniswap_v2,
            weth,
            usdc,
            1_000 * 10**18,  # 1000 ETH liquidity
            1_700_000 * 10**18,  # 1.7M USDC liquidity
        )

        # Sell 50 ETH
        usdc_received = estimate_sell_received_amount_raw(
            uniswap_v2,
            weth.address,
            usdc.address,
            50 * 10**18,
        )

        usdc_received_decimals = usdc_received / 10**18
        assert usdc_received_decimals == pytest.approx(80721.05538886508)

        # Calculate price of ETH as $ for our purchase
        price = usdc_received / (50*10**18)
        assert price == pytest.approx(Decimal(1614.4211077773016))

    :param quantity: How much of the base token we want to buy
    :param uniswap: Uniswap v2 deployment
    :param base_token: Base token of the trading pair
    :param quote_token: Quote token of the trading pair
    :param fee: Trading fee express in bps, default = 30 bps (0.3%)
    :param slippage: Slippage express in bps
    :return: Expected quote token amount to receive
    :raise TokenDetailError: If we have an issue with ERC-20 contracts
    """
    fee_helper = UniswapV2FeeCalculator(uniswap)

    if intermediate_token_address:
        path = (base_token_address, intermediate_token_address, quote_token_address)
    else:
        path = (base_token_address, quote_token_address)

    return fee_helper.get_amount_out(quantity_raw, path, fee=fee, slippage=slippage)
