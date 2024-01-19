"""Read token price using Chainlink."""

from typing import Tuple

from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.chainlink.round_data import ChainLinkLatestRoundData


def get_native_token_price_with_chainlink(
    web3: Web3,
) -> Tuple[str, ChainLinkLatestRoundData]:
    """Get the latest price of a native token on any chain.

    `Find feeds here <https://docs.chain.link/data-feeds/price-feeds/addresses?network=ethereum&page=1>`__.

    Example for ETH:

    .. code-block:: python

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

    aggregator = get_deployed_contract(
        web3,
        "ChainlinkAggregatorV2V3Interface.json",
        aggregator_address,
    )
    data = aggregator.functions.latestRoundData().call()
    description = aggregator.functions.description().call()
    base, quote = description.split("/")
    return base.strip(), ChainLinkLatestRoundData(aggregator, *data)
