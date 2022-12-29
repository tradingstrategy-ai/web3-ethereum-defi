"""Uniswap v3 price calculations."""
from decimal import Decimal

from eth_typing import HexAddress
from eth_defi.uniswap_v3.deployment import UniswapV3Deployment
from eth_defi.uniswap_v3.utils import encode_path
from eth_defi.uniswap_v3.pool import fetch_pool_details, PoolDetails
from eth_defi.token import fetch_erc20_details

from web3 import Web3


from typing import Optional

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
        assert len(path) >= 2
        assert len(fees) == len(path) - 1
        assert slippage >= 0

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
        assert len(path) >= 2
        assert len(fees) == len(path) - 1
        assert slippage >= 0

        encoded_path = encode_path(path, fees, exact_output=True)
        amount_in = self.deployment.quoter.functions.quoteExactOutput(encoded_path, amount_out).call()

        return int(amount_in * (10_000 + slippage) // 10_000)

def get_pool_fee(web3: Web3, pool_address: HexAddress):
    """Helper function to get the swap fee of a Uniswap V3 like pool"""
    pool_details = fetch_pool_details(web3, pool_address)
    return pool_details.raw_fee

def get_path_and_fees_quote_first(
    web3: Web3,
    base_token: HexAddress,
    quote_token: HexAddress,
    *,
    intermediate_token: Optional[HexAddress] = None,
    base_token_fee: Optional[int] = None,
    quote_token_fee: Optional[int] = None,
    intermediate_token_fee: Optional[int] = None
):
    """Helper function"""
    # If trading fees are not provided, we must fetch since
    # pool fees are dynamic in uniswap v3
    if base_token_fee is None:
        base_token_fee = get_pool_fee(web3, base_token)
    if quote_token_fee is None:
        quote_token_fee = get_pool_fee(web3, quote_token)
    if intermediate_token is not None and intermediate_token_fee is None:
        intermediate_token_fee = get_pool_fee(web3, intermediate_token)
    
    # get path and fees lists
    if intermediate_token:
        path = [quote_token, intermediate_token, base_token]
        fees = [quote_token_fee, intermediate_token_fee, base_token_fee]
    else:
        path = [quote_token, base_token]
        fees = [quote_token_fee, base_token_fee]

    return path, fees

def get_path_and_fees_base_first(
    web3: Web3,
    base_token: HexAddress,
    quote_token: HexAddress,
    *,
    intermediate_token: Optional[HexAddress] = None,
    base_token_fee: Optional[int] = None,
    quote_token_fee: Optional[int] = None,
    intermediate_token_fee: Optional[int] = None
):
    """Helper function"""
    # If trading fees are not provided, we must fetch since
    # pool fees are dynamic in uniswap v3
    if base_token_fee is None:
        base_token_fee = get_pool_fee(web3, base_token)
    if quote_token_fee is None:
        quote_token_fee = get_pool_fee(web3, quote_token)
    if intermediate_token is not None and intermediate_token_fee is None:
        intermediate_token_fee = get_pool_fee(web3, intermediate_token)
    
    # get path and fees lists
    if intermediate_token:
        path = [base_token, intermediate_token, quote_token]
        fees = [base_token_fee, intermediate_token_fee, quote_token_fee]
    else:
        path = [base_token, quote_token]
        fees = [base_token_fee, quote_token_fee]

    return path, fees

def estimate_buy_quantity(
    uniswap: UniswapV3Deployment,
    base_token: HexAddress,
    quote_token: HexAddress,
    amount_in: int,
    *,
    web3: Optional[Web3] = None,
    base_token_fee: Optional[int]=None,
    quote_token_fee: Optional[int]=None,
    slippage: Optional[float]=0,
    intermediate_token: Optional[HexAddress] = None,
    intermediate_token_fee: Optional[int]=None,
) -> int:
    """Estimate how many tokens we are going to receive when doing a buy.

    Trading fees do not have to be provided. If not provided, the fees will be
    fetched onchain. 

    Good for doing a price impact calculations.

    Calls the on-chain contract to get the current liquidity and estimates the
    the price based on it.

    :param amount_in: How much of the quote token we have to use
    :param uniswap: Uniswap v3 deployment
    :param base_token: Base token of the trading pair
    :param quote_token: Quote token of the trading pair
    :param fee: Trading fee express in bps, default = 30 bps (0.3%)
    :param slippage: Slippage express in bps
    :return: Expected base token to receive
    """

    path, fees = get_path_and_fees_quote_first(
            uniswap.web3,
            base_token,
            quote_token,
            intermediate_token=intermediate_token,
            base_token_fee=base_token_fee,
            quote_token_fee=quote_token_fee,
            intermediate_token_fee=intermediate_token_fee
        )
        
    price_helper = UniswapV3PriceHelper(uniswap)
    return price_helper.get_amount_out(amount_in, path, fees, slippage=slippage)

def estimate_buy_price(
    uniswap: UniswapV3Deployment,
    base_token: HexAddress,
    quote_token: HexAddress,
    amount_out: int,
    *,
    base_token_fee: Optional[int]=None,
    quote_token_fee: Optional[int]=None,
    slippage: Optional[float]=0,
    intermediate_token: Optional[HexAddress] = None,
    intermediate_token_fee: Optional[int]=None,
)-> int:
    """Estimate how much we are going to need to pay when doing buy.

    Calls the on-chain contract to get the current liquidity and estimates the
    the price based on it.

    Trading fees do not have to be provided. If not provided, the fees will be
    fetched onchain. 

    :param uniswap: Uniswap v3 deployment
    :param base_token: Base token of the trading pair
    :param quote_token: Quote token of the trading pair
    :param amount_out: How much of the base token we want to buy
    :param fee: Trading fee express in bps, default = 30 bps (0.3%)
    :param slippage: Slippage express in bps
    :return: Expected base token to receive
    """

    path, fees = get_path_and_fees_quote_first(
            uniswap.web3,
            base_token,
            quote_token,
            intermediate_token=intermediate_token,
            base_token_fee=base_token_fee,
            quote_token_fee=quote_token_fee,
            intermediate_token_fee=intermediate_token_fee
        )
        
    price_helper = UniswapV3PriceHelper(uniswap)
    return price_helper.get_amount_in(amount_out, path, fees, slippage=slippage)

def estimate_sell_price(
    uniswap: UniswapV3Deployment,
    base_token: HexAddress,
    quote_token: HexAddress,
    amount_in: int,
    *,
    base_token_fee: Optional[int]=None,
    quote_token_fee: Optional[int]=None,
    slippage: Optional[float]=0,
    intermediate_token: Optional[HexAddress] = None,
    intermediate_token_fee: Optional[int]=None,
) -> int:
    """Estimate how much we are going to get paid when doing a sell.

    Calls the on-chain contract to get the current liquidity and estimates the
    the price based on it.

    Trading fees do not have to be provided. If not provided, the fees will be
    fetched onchain. 

    TODO example for uni v3, see uni v2 equivalent

    :param uniswap: Uniswap v3 deployment
    :param base_token: Base token of the trading pair
    :param quote_token: Quote token of the trading pair
    :param amount_out: How much of the base token we want to buy
    :param fee: Trading fee express in bps, default = 30 bps (0.3%)
    :param slippage: Slippage express in bps
    :return: Expected base token to receive
    """
    
    path, fees = get_path_and_fees_base_first(
                uniswap.web3,
                base_token,
                quote_token,
                intermediate_token=intermediate_token,
                base_token_fee=base_token_fee,
                quote_token_fee=quote_token_fee,
                intermediate_token_fee=intermediate_token_fee
            )
        
    price_helper = UniswapV3PriceHelper(uniswap)
    return price_helper.get_amount_out(amount_in, path, fees, slippage=slippage)

def estimate_buy_price_decimals(
    uniswap: UniswapV3Deployment,
    base_token: HexAddress,
    quote_token: HexAddress,
    amount_out: int,
    *,
    base_token_fee: Optional[int]=None,
    quote_token_fee: Optional[int]=None,
    slippage: Optional[float]=0,
    intermediate_token: Optional[HexAddress] = None,
    intermediate_token_fee: Optional[int]=None,
)-> Decimal:
    """Estimate how much we are going to need to pay when doing buy.

    Much like :py:func:`estimate_buy_price` with the differences of
    - Tokens are passed as address instead of contract instance
    - We use base token quantity units instead of cash
    - We use decimals instead of raw token amounts

    Example:
    TODO update example to uniswap v3

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
    quote = fetch_erc20_details(web3, quote_token, raise_on_error=False)
    quantity_raw = quote.convert_to_raw(amount_out)
    price_helper = UniswapV3PriceHelper(uniswap)

    path, fees = get_path_and_fees_quote_first(
                uniswap.web3,
                base_token,
                quote_token,
                intermediate_token=intermediate_token,
                base_token_fee=base_token_fee,
                quote_token_fee=quote_token_fee,
                intermediate_token_fee=intermediate_token_fee
            )

    in_raw = price_helper.get_amount_in(quantity_raw, path, fees, slippage=slippage)
    return quote.convert_to_decimals(in_raw)

def estimate_sell_price_decimals(
    uniswap: UniswapV3Deployment,
    base_token: HexAddress,
    quote_token: HexAddress,
    amount_in: int,
    *,
    base_token_fee: Optional[int]=None,
    quote_token_fee: Optional[int]=None,
    slippage: Optional[float]=0,
    intermediate_token: Optional[HexAddress] = None,
    intermediate_token_fee: Optional[int]=None,
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
    base = fetch_erc20_details(web3, base_token, raise_on_error=False)
    quote = fetch_erc20_details(web3, quote_token, raise_on_error=False)
    quantity_raw = base.convert_to_raw(amount_in)
    price_helper = UniswapV3PriceHelper(uniswap)

    path, fees = get_path_and_fees_base_first(
                uniswap.web3,
                base_token,
                quote_token,
                intermediate_token=intermediate_token,
                base_token_fee=base_token_fee,
                quote_token_fee=quote_token_fee,
                intermediate_token_fee=intermediate_token_fee
            )

    out_raw = price_helper.get_amount_out(quantity_raw, path, fees, slippage=slippage)
    return quote.convert_to_decimals(out_raw)

def estimate_buy_received_amount_raw(
    uniswap: UniswapV3Deployment,
    base_token: HexAddress,
    quote_token: HexAddress,
    amount_out_raw: Decimal,
    *,
    base_token_fee: Optional[int]=None,
    quote_token_fee: Optional[int]=None,
    slippage: Optional[float]=0,
    intermediate_token: Optional[HexAddress] = None,
    intermediate_token_fee: Optional[int]=None,
) -> int:
    """Estimate how much we receive for a certain cash amount.

    TODO update to Uniswap v3, see uni v2 equivalent

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
    price_helper = UniswapV3PriceHelper(uniswap)

    path, fees = get_path_and_fees_quote_first(
                uniswap.web3,
                base_token,
                quote_token,
                intermediate_token=intermediate_token,
                base_token_fee=base_token_fee,
                quote_token_fee=quote_token_fee,
                intermediate_token_fee=intermediate_token_fee
            )

    # We will receive equal number of amounts as there are items in the path
    return price_helper.get_amount_out(amount_out_raw, path, fees, slippage=slippage)

def estimate_sell_received_amount_raw(
    uniswap: UniswapV3Deployment,
    base_token: HexAddress,
    quote_token: HexAddress,
    amount_in_raw: Decimal,
    *,
    base_token_fee: Optional[int]=None,
    quote_token_fee: Optional[int]=None,
    slippage: Optional[float]=0,
    intermediate_token: Optional[HexAddress] = None,
    intermediate_token_fee: Optional[int]=None,
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
    price_helper = UniswapV3PriceHelper(uniswap)

    path, fees = get_path_and_fees_base_first(
                uniswap.web3,
                base_token,
                quote_token,
                intermediate_token=intermediate_token,
                base_token_fee=base_token_fee,
                quote_token_fee=quote_token_fee,
                intermediate_token_fee=intermediate_token_fee
            )

    # We will receive equal number of amounts as there are items in the path
    return price_helper.get_amount_out(amount_in_raw, path, fees, slippage=slippage)
