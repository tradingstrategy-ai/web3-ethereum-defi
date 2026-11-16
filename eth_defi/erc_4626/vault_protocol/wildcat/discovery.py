"""Wildcat market discovery through the ``WildcatArchController`` registry.

Wildcat markets are not ERC-4626 vaults. Every market is registered in the
chain's ``WildcatArchController``, which emits
``MarketAdded(address indexed controller, address market)``. The registry event
is sufficient identification, so a decoded market becomes a direct lead and
skips the generic ERC-4626 feature probe, as Enzyme Blue deployments do.

Only version 2 markets are supported. Their controller is the ``HooksFactory``;
registry events from version 1 market controllers are skipped.

See the `WildcatArchController source <https://github.com/wildcat-finance/v2-protocol/blob/main/src/WildcatArchController.sol>`__.
"""

import dataclasses
import datetime
from typing import Any

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.wildcat.constants import WILDCAT_DEPLOYMENTS, WILDCAT_MARKET_ADDED_EVENT_SIGNATURE, WildcatDeployment
from eth_defi.vault.flow_events import decode_hypersync_int

#: ``MarketAdded`` has topic0 and the indexed controller topic.
MARKET_ADDED_TOPIC_COUNT = 2


@dataclasses.dataclass(slots=True, frozen=True)
class WildcatMarketCandidate:
    """A version 2 Wildcat market decoded from ``MarketAdded``."""

    #: EVM chain id.
    chain: int
    #: Lowercased market address. The market is also the lender's ERC-20 token.
    address: HexAddress
    #: Block of the registry event.
    created_block: int
    #: Naive UTC timestamp of the registry event block.
    created_at: datetime.datetime


def fetch_wildcat_market_added_event_topic() -> str:
    """Return the ``WildcatArchController.MarketAdded`` topic0.

    :return:
        Hex-encoded keccak hash of the event signature.
    """

    return Web3.to_hex(Web3.keccak(text=WILDCAT_MARKET_ADDED_EVENT_SIGNATURE))


def fetch_wildcat_deployment(chain_id: int) -> WildcatDeployment | None:
    """Return the reviewed Wildcat deployment for a chain.

    :param chain_id:
        EVM chain id.
    :return:
        Deployment constants, or ``None`` when Wildcat is not supported on the chain.
    """

    return WILDCAT_DEPLOYMENTS.get(chain_id)


def is_wildcat_registry_log(chain_id: int, address: HexAddress | str, topic0: str | None) -> bool:
    """Check whether a log is a ``MarketAdded`` event of the reviewed registry.

    :param chain_id:
        EVM chain id of the log.
    :param address:
        Log emitter address.
    :param topic0:
        Log topic0.
    :return:
        ``True`` for registry ``MarketAdded`` events.
    """

    deployment = fetch_wildcat_deployment(chain_id)
    return deployment is not None and topic0 == fetch_wildcat_market_added_event_topic() and address.lower() == deployment.arch_controller.lower()


def create_wildcat_market_candidate(chain_id: int, log: Any, timestamp: datetime.datetime) -> WildcatMarketCandidate | None:
    """Decode a version 2 market from a registry ``MarketAdded`` log.

    ``market`` is not indexed, so it is decoded from the log data. The indexed
    controller identifies the market version: ``HooksFactory`` registers
    version 2 markets, while per-borrower controllers registered version 1
    markets.

    :param chain_id:
        EVM chain id of the log.
    :param log:
        Hypersync log of a registry ``MarketAdded`` event, with hex string
        ``topics`` and ``data``.
    :param timestamp:
        Naive UTC timestamp of the log block.
    :return:
        Market candidate, or ``None`` for a version 1 market.
    :raise ValueError:
        If the log does not have the expected layout.
    """

    deployment = fetch_wildcat_deployment(chain_id)
    assert deployment is not None, f"Wildcat is not configured for chain {chain_id}"
    topics = list(log.topics or [])
    if len(topics) < MARKET_ADDED_TOPIC_COUNT:
        message = "Wildcat MarketAdded event omitted its controller topic"
        raise ValueError(message)
    controller = f"0x{topics[1][-40:]}"
    if controller.lower() != deployment.hooks_factory.lower():
        return None
    (market,) = Web3().codec.decode(["address"], bytes.fromhex(log.data.removeprefix("0x")))
    return WildcatMarketCandidate(
        chain=chain_id,
        address=HexAddress(market.lower()),
        created_block=decode_hypersync_int(log.block_number),
        created_at=timestamp,
    )
