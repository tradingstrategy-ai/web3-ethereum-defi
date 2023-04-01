"""ChainLink price feed functions"""
from typing import Iterable

from eth_defi.enzyme.deployment import EnzymeDeployment
from eth_defi.event_reader.filter import Filter
from eth_defi.event_reader.reader import Web3EventReader


class ChainlinkPriceFeed:
    """ChainLink price feedin Enzyme.

    See `ChainlinkPriceFeedMixin.sol`
    """

    def wrap(self, event: dict) -> "ChainlinkPriceFeed":
        pass


def fetch_price_feeds(
    deployment: EnzymeDeployment,
    start_block: int,
    end_block: int,
    read_events: Web3EventReader,
) -> Iterable[ChainlinkPriceFeed]:
    """Iterate configured price feeds

    - Uses eth_getLogs ABI

    - Read both deposits and withdrawals in one go

    - Serial read

    - Slow over long block ranges

    - See `ComptrollerLib.sol`
    """

    web3 = deployment.web3

    filter = Filter.create_filter(
        deployment.contracts.value_interpreter.address,
        [deployment.contracts.value_interpreter.events.PrimitiveAdded],
    )

    for solidity_event in read_events(
        web3,
        start_block,
        end_block,
        filter=filter,
    ):
        yield ChainlinkPriceFeed()
