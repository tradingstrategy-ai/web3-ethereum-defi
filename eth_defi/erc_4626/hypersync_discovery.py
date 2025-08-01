"""Find ERC-4626 vaults onchain using HyperSync.

- Use HyperSync's index to quickly get ERC-4626 identification events from the chain
- We do not use raw JSON-RPC, because Etheruem JSON-RPC is badly designed piece of crap for reading data
- Use tons of heurestics to figure out what's going on with vaults
- This is because ERC-4626, like many other ERC standards, are very poorly designed, lacking proper identification events and interface introspection

"""

import asyncio
import logging
import dataclasses

import datetime
from typing import AsyncIterable, Iterable

from eth_typing import HexAddress, HexStr
from web3 import Web3

from tqdm_loggable.auto import tqdm

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.erc_4626.classification import probe_vaults
from eth_defi.erc_4626.core import get_erc_4626_contract, ERC4626Feature, ERC4262VaultDetection
from eth_defi.event_reader.web3factory import Web3Factory

try:
    import hypersync
except ImportError as e:
    raise ImportError("Install the library with optional HyperSync dependency to use this module") from e

from hypersync import BlockField, LogField


logger = logging.getLogger(__name__)


@dataclasses.dataclass(slots=True, frozen=False)
class PotentialVaultMatch:
    """Categorise contracts that emit ERC-4626 like events."""

    chain: int
    address: HexAddress
    first_seen_at_block: int
    first_seen_at: datetime.datetime
    first_log_clue: hypersync.Log
    deposit_count: int = 0
    withdrawal_count: int = 0

    def is_candidate(self) -> bool:
        return self.deposit_count > 0 and self.withdrawal_count > 0


class HypersyncVaultDiscover:
    """Autoscan the chain for 4626 vaults.

    - First build map of potential contracts using :py:meth:`scan_potential_vaults`
    - Then probe given contracts and determine their ERC-4626 vault properties

    See :ref:`scan-erc_4626_vaults` for usage.
    """

    def __init__(
        self,
        web3: Web3,
        web3factory: Web3Factory,
        client: hypersync.HypersyncClient,
        max_workers: int = 8,
    ):
        """Create vault discover.

        :param web3:
            Current process web3 connection

        :param web3factory:
            Used to initialise connection in created worker threads/processes

        :param client:
            HyperSync client used to scan lead event data

        :param max_workers:
            How many worker processes use in multicall probing
        """
        self.web3 = web3
        self.web3factory = web3factory
        self.client = client
        self.max_workers = max_workers

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

        chain = self.web3.eth.chain_id

        logger.info("Building HyperSync query")
        query = self.build_query(start_block, end_block)

        logger.info(f"Starting HyperSync stream {start_block:,} to {end_block:,}, chain {chain}, query is {query}")
        # start the stream
        receiver = await self.client.stream(query, hypersync.StreamConfig())

        if display_progress:
            progress_bar = tqdm(
                total=end_block - start_block,
                desc=f"Scanning potential vault leads on chain {self.web3.eth.chain_id}",
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
            try:
                res = await asyncio.wait_for(receiver.recv(), timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning("HyperSync receiver timed out")
                break  # or handle as appropriate

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
                            chain=chain,
                            address=log.address.lower(),
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
                    progress_bar.set_postfix(
                        {
                            "At": timestamp,
                            "Matches": f"{matches:,}",
                        }
                    )

        logger.info(f"HyperSync sees {last_synced} as the last block")

        if progress_bar is not None:
            progress_bar.close()

    def scan_vaults(
        self,
        start_block: int,
        end_block: int,
        display_progress=True,
    ) -> Iterable[ERC4262VaultDetection]:
        """Scan vaults.

        - Detect vault leads by events using :py:meth:`scan_potential_vaults`
        - Then perform multicall probing for each vault smart contract to detect protocol
        """

        chain = self.web3.eth.chain_id

        # Don't leak async colored interface, as it is an implementation detail
        async def _hypersync_asyncio_wrapper():
            leads = {}
            async for x in self.scan_potential_vaults(start_block, end_block, display_progress):
                leads[x.address] = x
            return leads

        leads: dict[HexAddress, PotentialVaultMatch]
        leads = asyncio.run(_hypersync_asyncio_wrapper())

        logger.info("Found %d leads", len(leads))
        addresses = list(leads.keys())
        good_vaults = broken_vaults = 0

        if display_progress:
            progress_bar_desc = f"Identifying vaults, using {self.max_workers} workers"
        else:
            progress_bar_desc = None

        for feature_probe in probe_vaults(
            chain,
            self.web3factory,
            addresses,
            block_identifier=end_block,
            max_workers=self.max_workers,
            progress_bar_desc=progress_bar_desc,
        ):
            lead = leads[feature_probe.address]

            yield ERC4262VaultDetection(
                chain=chain,
                address=feature_probe.address,
                features=feature_probe.features,
                first_seen_at_block=lead.first_seen_at_block,
                first_seen_at=lead.first_seen_at,
                updated_at=datetime.datetime.utcnow(),
                deposit_count=lead.deposit_count,
                redeem_count=lead.withdrawal_count,
            )

            if ERC4626Feature.broken in feature_probe.features:
                broken_vaults += 1
            else:
                good_vaults += 1

        logger.info(
            "Found %d good ERC-4626 vaults, %d broken vaults",
            good_vaults,
            broken_vaults,
        )
