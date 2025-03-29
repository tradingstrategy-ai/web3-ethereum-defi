"""Find ERC-4626 vaults onchain using HyperSync.

- Use HyperSync's index to quickly get ERC-4626 identification events from the chain
- We do not use raw JSON-RPC, because Etheruem JSON-RPC is badly designed piece of crap for reading data
- Use tons of heurestics to figure out what's going on with vaults
- This is because ERC-4626, like many other ERC standards, are very poorly designed, lacking proper identification events and interface introspection

"""
import logging
import dataclasses

import datetime
from typing import AsyncIterable

from eth_typing import HexAddress, HexStr
from web3 import Web3

from tqdm_loggable.auto import tqdm

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.erc_4626.core import get_erc_4626_contract, ERC4626Feature

try:
    import hypersync
except ImportError as e:
    raise ImportError("Install the library with optional HyperSync dependency to use this module") from e

from hypersync import BlockField, TransactionField, LogField


logger = logging.getLogger(__name__)


@dataclasses.dataclass(slots=True, frozen=False)
class PotentialVaultMatch:
    """Categorise contracts that emit ERC-4626 like events."""
    address: HexAddress
    first_seen_at_block: int
    first_seen_at: datetime.datetime
    first_log_clue: hypersync.Log
    deposit_count: int = 0
    withdrawal_count: int = 0

    def is_candidate(self) -> bool:
        return self.deposit_count > 0 and self.withdrawal_count > 0


@dataclasses.dataclass(slots=True, frozen=True)
class ERC4262Vault:
    """A ERC-4626 detection."""
    address: HexAddress
    first_seen_at_block: int
    first_seen_at: datetime.datetime
    features: set[ERC4626Feature]


class HypersyncVaultDiscover:
    """Autoscan the chain for 4626 vaults.

    - First build map of potential contracts using :py:meth:`scan_potential_vaults`
    - Then probe given contracts and determine their ERC-4626 vault properties
    """

    def __init__(self, web3: Web3, client: hypersync.HypersyncClient):
        self.web3 = web3
        self.client = client

    def get_topic_signatures(self) -> list[HexStr]:
        """Contracts must have at least one event of both these signatures

        - Find contracts emitting these events
        - Later prod these contracts to see which of them are proper vaults
        - We are likely having a real ERC-4262 contract if both events match,
          ``Deposit`` evnet might have few similar contracts
        """

        # event Deposit(
        #     address indexed sender,
        #     address indexed owner,
        #     uint256 assets,
        #     uint256 shares
        #
        # )

        # event Withdraw(
        #     address indexed sender,
        #     address indexed receiver,
        #     address indexed owner,
        #     uint256 assets,
        #     uint256 shares
        # )

        IERC4626 = get_erc_4626_contract(self.web3)
        return [
            # Example tx https://basescan.org/tx/0x7d5e4b42e6e5f2c819683f3b3d4d883c7a6ee9f2d5abf56ac8b742528a5d9c80#eventlog
            get_topic_signature_from_event(IERC4626.events.Deposit),
            get_topic_signature_from_event(IERC4626.events.Withdraw),
        ]

    def build_query(self, start_block: int, end_block: int) -> hypersync.Query:
        """Create HyperSync query that extracts all potential lead events from the chain.

        See example here: https://github.com/enviodev/hypersync-client-python/blob/main/examples/all-erc20-transfers.py
        """

        # [['0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7'], ['0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db']]
        log_selections = [hypersync.LogSelection(topics=[[sig]]) for sig in self.get_topic_signatures()]

        # The query to run
        query = hypersync.Query(
            # start from block 0 and go to the end of the chain (we don't specify a toBlock).
            from_block=start_block,
            to_block=end_block,
            # The logs we want. We will also automatically get transactions and blocks relating to these logs (the query implicitly joins them).
            logs=log_selections,
            # Select the fields we are interested in, notice topics are selected as topic0,1,2,3
            field_selection=hypersync.FieldSelection(
                block=[
                    BlockField.NUMBER,
                    BlockField.TIMESTAMP,
                ],
                log=[
                    LogField.BLOCK_NUMBER,
                    LogField.ADDRESS,
                    LogField.TRANSACTION_HASH,
                    LogField.TOPIC0,
                ],
            ),
        )
        return query

    async def scan_potential_vaults(
        self,
        start_block: int,
        end_block: int,
        display_progress=True,
    ) -> AsyncIterable[PotentialVaultMatch]:
        """Identify smart contracts emitting 4626 like events.

        - Scan all event matches using HyperSync

        - See stream() example here: https://github.com/enviodev/hypersync-client-python/blob/main/examples/all-erc20-transfers.py
        """
        assert end_block > start_block

        logger.info("Building HyperSync query")
        query = self.build_query(start_block, end_block)

        logger.info(f"Starting HyperSync stream {start_block:,} to {end_block:,}, query is {query}")
        # start the stream
        receiver = await self.client.stream(query, hypersync.StreamConfig())

        if display_progress:
            progress_bar = tqdm(
                total=end_block - start_block,
                desc=f"Scanning potential vaults on chain {self.web3.eth.chain_id}",
            )
        else:
            progress_bar = None

        last_block = start_block
        timestamp = None

        logger.info(f"Streaming HyperSync")

        last_synced = None

        leads: dict[HexAddress, PotentialVaultMatch] = {}
        matches = 0
        seen = set()

        while True:
            res = await receiver.recv()
            # exit if the stream finished
            if res is None:
                break

            current_block = res.next_block

            if res.data.logs:
                block_lookup = {b.number: b for b in res.data.blocks}
                log: hypersync.Log
                for log in res.data.logs:

                    lead = leads.get(log.address)

                    if not lead:
                        # Fresh match
                        block = block_lookup[log.block_number]
                        timestamp = datetime.datetime.utcfromtimestamp(int(block.timestamp, 16))
                        lead = PotentialVaultMatch(
                            address=log.address,
                            first_seen_at_block=log.block_number,
                            first_seen_at=timestamp,
                            first_log_clue=log,
                        )
                        leads[log.address] = lead

                    # Hardcoded for now
                    deposit_kind = log.topics[0] == "0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7"
                    if deposit_kind:
                        lead.deposit_count += 1
                    else:
                        lead.withdrawal_count += 1

                    if log.address not in seen:
                        if lead.is_candidate():
                            # Return leads early, even if we still accumulate deposit and withdraw matches for them
                            yield lead
                            matches += 1
                            seen.add(log.address)

            last_synced = res.archive_height

            if progress_bar is not None:
                progress_bar.update(current_block - last_block)
                last_block = current_block

                # Add extra data to the progress bar
                if timestamp is not None:
                    progress_bar.set_postfix({
                        "At": timestamp,
                        "Matches": f"{matches:,}",
                    })

        logger.info(f"HyperSync sees {last_synced} as the last block")

        if progress_bar is not None:
            progress_bar.close()

    async def scan_vaults(
        self,
        start_block: int,
        end_block: int,
        display_progress=True,
    ):
        leads = [x async for x in self.scan_potential_vaults(start_block, end_block, display_progress)]
        logger.info("Found %d leads", len(leads))
