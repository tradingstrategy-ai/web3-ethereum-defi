"""Find ERC-4626 vaults onchain using HyperSync.

- Use HyperSync's index to quickly get ERC-4626 identification events from the chain
"""
import dataclasses
from typing import Iterable

from eth_typing import HexAddress, HexStr
from web3 import Web3

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.erc_4626.core import get_erc_4626_contract

try:
    import hypersync
except ImportError as e:
    raise ImportError("Install the library with optional HyperSync dependency to use this module") from e

from hypersync import BlockField, TransactionField, LogField


@dataclasses.dataclass(slots=True, frozen=True)
class PotentialVaultMatch:
    block_number: int
    address: HexAddress


class HypersyncVaultDiscover:
    """Autoscan the chain for 4626 vaults."""

    def __init__(self, web3: Web3, client: hypersync.HypersyncClient):
        self.web3 = web3
        self.client = client

    def get_topic_signatures(self) -> list[HexStr]:
        """Contracts must have at least one event of both these signatures

        IERC4626.events.Deposit,
        """
        IERC4626 = get_erc_4626_contract(self.web3)
        return [
            get_topic_signature_from_event(IERC4626.events.Deposit),
            get_topic_signature_from_event(IERC4626.events.Withdraw),
        ]

    def build_query(self) -> hypersync.Query:
        # The query to run
        query = hypersync.Query(
            # start from block 0 and go to the end of the chain (we don't specify a toBlock).
            from_block=0,
            # The logs we want. We will also automatically get transactions and blocks relating to these logs (the query implicitly joins them).
            logs=[
                hypersync.LogSelection(
                    # We want All ERC20 transfers so no address filter and only a filter for the first topic
                    topics=[[x] for x in self.get_topic_signatures()]
                )
            ],
            # Select the fields we are interested in, notice topics are selected as topic0,1,2,3
            field_selection=hypersync.FieldSelection(
                block=[BlockField.NUMBER, BlockField.TIMESTAMP, BlockField.HASH],
                log=[
                    LogField.LOG_INDEX,
                    LogField.TRANSACTION_INDEX,
                    LogField.TRANSACTION_HASH,
                ],
                transaction=[
                    TransactionField.BLOCK_NUMBER,
                    TransactionField.TRANSACTION_INDEX,
                    TransactionField.HASH,
                ],
            ),
        )
        return query

   def scan_potential_vaults(self, start_block: int, end_block: int) -> Iterable[PotentialVaultMatch]:

       assert end_block > start_block

       query = self.build_query()

       # start the stream
       receiver = await client.stream(query, hypersync.StreamConfig())

