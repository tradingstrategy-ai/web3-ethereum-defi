"""Read Securitize fund NAV from RedStone on-chain push feeds.

RedStone publishes reviewed Securitize fundamental-value feeds through
Chainlink-compatible contracts. Reading ``latestRoundData()`` at an archive
block returns the value that was available at that block, so the vault history
scanner can backfill NAV without relying on RedStone's 30-day REST retention.

Feed catalogue: https://app.redstone.finance/app/feeds/
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.securitize.description import ACRED_ETHEREUM, BCAP_ETHEREUM, HLSCOPE_ETHEREUM, MI4_MANTLE, STAC_ETHEREUM, VBILL_ETHEREUM


class RedstoneFeedError(RuntimeError):
    """Raised when a RedStone NAV feed returns an unusable value."""


@dataclass(slots=True, frozen=True)
class RedstoneSecuritizeFeed:
    """One reviewed Securitize NAV push feed.

    RedStone push feeds expose the Chainlink aggregator interface. The
    ``first_block`` boundary is the first block where ``latestRoundData()``
    returned a positive observation, which can be later than contract
    deployment.
    """

    #: EVM chain hosting both the Securitize token and feed.
    chain_id: int

    #: Lower-case Securitize token address.
    token: HexAddress

    #: RedStone fundamental feed identifier.
    feed_id: str

    #: Chainlink-compatible RedStone push-feed contract.
    oracle_address: HexAddress

    #: First block with a valid feed observation.
    first_block: int

    #: Number of decimals used by the oracle answer.
    decimals: int = 8


@dataclass(slots=True, frozen=True)
class RedstonePricePoint:
    """One RedStone on-chain fundamental NAV observation."""

    #: Naive UTC time at which RedStone published the value.
    timestamp: datetime.datetime

    #: Fund NAV per share in USD.
    share_price: Decimal


#: Reviewed Securitize products with RedStone fundamental NAV push feeds.
REDSTONE_SECURITIZE_FEEDS: dict[tuple[int, HexAddress], RedstoneSecuritizeFeed] = {
    (ACRED_ETHEREUM.chain_id, ACRED_ETHEREUM.token): RedstoneSecuritizeFeed(
        ACRED_ETHEREUM.chain_id,
        ACRED_ETHEREUM.token,
        "ACRED_FUNDAMENTAL",
        HexAddress("0xd6bcbbc87bfb6c8964ddc73dc3eae6d08865d51c"),
        21_888_488,
    ),
    (HLSCOPE_ETHEREUM.chain_id, HLSCOPE_ETHEREUM.token): RedstoneSecuritizeFeed(
        HLSCOPE_ETHEREUM.chain_id,
        HLSCOPE_ETHEREUM.token,
        "HLScope_FUNDAMENTAL",
        HexAddress("0x1f14a50ba904a28cf6088e71b6a15561074398d7"),
        21_888_488,
    ),
    (STAC_ETHEREUM.chain_id, STAC_ETHEREUM.token): RedstoneSecuritizeFeed(
        STAC_ETHEREUM.chain_id,
        STAC_ETHEREUM.token,
        "STAC_FUNDAMENTAL",
        HexAddress("0xedc6287d3d41b322af600317628d7e226dd3add4"),
        23_734_437,
    ),
    (VBILL_ETHEREUM.chain_id, VBILL_ETHEREUM.token): RedstoneSecuritizeFeed(
        VBILL_ETHEREUM.chain_id,
        VBILL_ETHEREUM.token,
        "VBILL_ETHEREUM_FUNDAMENTAL",
        HexAddress("0xa569e68b5d110f2a255482c2997dfdbe1b2ab912"),
        22_537_814,
    ),
    (BCAP_ETHEREUM.chain_id, BCAP_ETHEREUM.token): RedstoneSecuritizeFeed(
        BCAP_ETHEREUM.chain_id,
        BCAP_ETHEREUM.token,
        "BCAP_FUNDAMENTAL",
        HexAddress("0x46f1b5f29a2dc1a730508a1b41a8b5b93e316eb2"),
        25_494_164,
    ),
    (MI4_MANTLE.chain_id, MI4_MANTLE.token): RedstoneSecuritizeFeed(
        MI4_MANTLE.chain_id,
        MI4_MANTLE.token,
        "MI4_MANTLE_FUNDAMENTAL",
        HexAddress("0x24c8964338deb5204b096039147b8e8c3aea42cc"),
        86_247_628,
    ),
}


def fetch_redstone_feed_contract(web3: Web3, feed: RedstoneSecuritizeFeed) -> Contract:
    """Create a contract proxy for a reviewed RedStone push feed.

    :param web3:
        Connection to ``feed.chain_id``.
    :param feed:
        Reviewed Securitize feed configuration.
    :return:
        Chainlink-compatible feed contract.
    :raises ValueError:
        If the connection is for a different chain.
    """

    if web3.eth.chain_id != feed.chain_id:
        raise ValueError(f"RedStone feed {feed.feed_id} is on chain {feed.chain_id}, not {web3.eth.chain_id}")
    return get_deployed_contract(web3, "ChainlinkAggregatorV2V3Interface.json", feed.oracle_address)


def fetch_redstone_price_at(web3: Web3, feed: RedstoneSecuritizeFeed, block_identifier: BlockIdentifier = "latest") -> RedstonePricePoint:
    """Fetch the RedStone NAV observation available at an archive block.

    The push-feed contract stores its current answer. Archive-node state makes
    the same call point-in-time correct for both initial backfills and normal
    incremental scans.

    :param web3:
        Archive-capable connection to the feed chain.
    :param feed:
        Reviewed Securitize feed configuration.
    :param block_identifier:
        Historical block number or ``latest``.
    :return:
        Positive USD NAV/share and its publication timestamp.
    :raises RedstoneFeedError:
        If the feed has not published a valid observation at the block.
    """

    if isinstance(block_identifier, int) and block_identifier < feed.first_block:
        raise RedstoneFeedError(f"RedStone {feed.feed_id} has no observation before block {feed.first_block}")

    _round_id, answer, _started_at, updated_at, _answered_in_round = fetch_redstone_feed_contract(web3, feed).functions.latestRoundData().call(block_identifier=block_identifier)
    if answer <= 0 or updated_at <= 0:
        raise RedstoneFeedError(f"RedStone {feed.feed_id} returned an invalid observation at block {block_identifier}")
    return RedstonePricePoint(
        timestamp=datetime.datetime.fromtimestamp(updated_at, tz=datetime.UTC).replace(tzinfo=None),
        share_price=Decimal(answer) / Decimal(10**feed.decimals),
    )
