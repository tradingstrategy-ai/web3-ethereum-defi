"""Read token price using Chainlink.

See tutorials

- :ref:`chainlink-native-token`

"""

from typing import Tuple
from eth_typing import HexAddress

from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.chainlink.round_data import ChainLinkLatestRoundData


def get_native_token_price_with_chainlink(
    web3: Web3,
) -> Tuple[str, ChainLinkLatestRoundData]:
    """Get the latest price of a native token on any chain in USD.

    `Find feeds here <https://docs.chain.link/data-feeds/price-feeds/addresses?network=ethereum&page=1>`__.

    Example for ETH:

    .. code-block:: python

            import os

            from eth_defi.chainlink.token_price import get_native_token_price_with_chainlink
            from eth_defi.provider.multi_provider import create_multi_provider_web3

            json_rpc_url = os.environ["JSON_RPC_URL"]

            web3 = create_multi_provider_web3(json_rpc_url)

            token_name, last_round = get_native_token_price_with_chainlink(web3)

            price = last_round.price
            print(f"The chain native token price of is {price} {token_name} / USD")

    :return:
        USD exchange rate of the chain native token.

        Returned as native token symbol, latest ChainLink round data.

    :raise NotImplementedError:
        Chainlink configuration not yet added for this chain.

    """

    match web3.eth.chain_id:
        case 1:
            aggregator_address = "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"
        case _:
            raise NotImplementedError(f"Unsupported chain: {web3.eth.chain_id}. Please add aggregator mapping.")

    base, quote, price = get_token_price_with_chainlink(web3, aggregator_address)
    return base, price


def get_token_price_with_chainlink(
    web3: Web3,
    aggregator_address: HexAddress,
) -> Tuple[str, str, ChainLinkLatestRoundData]:
    """Get the latest price of any token on a chain based on its Chainlink feed.

    `Find feeds here <https://docs.chain.link/data-feeds/price-feeds/addresses?network=ethereum&page=1>`__.

    Example for BNB price on Ethereum mainnet:

    .. code-block:: python

        import os

        from eth_defi.chainlink.token_price import get_token_price_with_chainlink
        from eth_defi.provider.multi_provider import create_multi_provider_web3

        json_rpc_url = os.environ["JSON_RPC_URL"]

        web3 = create_multi_provider_web3(json_rpc_url)

        base_token_symbol, quote_token_symbol, last_round = get_token_price_with_chainlink(web3, "0x14e613AC84a31f709eadbdF89C6CC390fDc9540A")

        price = last_round.price
        print(f"The chain native token price of is {price} {base_token_symbol} / {quote_token_symbol}")

    This will output:

    .. code-block:: text

        The chain native token price of is 312.94308698 BNB / USD

    :param aggregator_address:
        The Chainlink aggregator contract address

    :return:
        USD exchange rate of the chain native token.

        Returned as tuple (base token symbol, quote token symbol, price)

    """
    aggregator = get_deployed_contract(
        web3,
        "ChainlinkAggregatorV2V3Interface.json",
        aggregator_address,
    )
    data = aggregator.functions.latestRoundData().call()
    description = aggregator.functions.description().call()
    base, quote = description.split("/")
    return base.strip(), quote.strip(), ChainLinkLatestRoundData(aggregator, *data)
