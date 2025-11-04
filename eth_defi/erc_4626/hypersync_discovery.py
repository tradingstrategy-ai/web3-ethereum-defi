"""Find ERC-4626 vaults onchain using HyperSync.

- Use HyperSync's index to quickly get ERC-4626 identification events from the chain
- We do not use raw JSON-RPC, because Etheruem JSON-RPC is badly designed piece of crap for reading data
- Use tons of heurestics to figure out what's going on with vaults
- This is because ERC-4626, like many other ERC standards, are very poorly designed, lacking proper identification events and interface introspection

"""

import asyncio
import logging
import time

from eth_typing import HexAddress, HexStr
from web3 import Web3

from tqdm_loggable.auto import tqdm

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.chain import get_chain_name
from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch, VaultDiscoveryBase, LeadScanReport, get_vault_discovery_events
from eth_defi.event_reader.web3factory import Web3Factory

try:
    import hypersync
    from hypersync import BlockField, LogField
except ImportError as e:
    raise ImportError("Install the library with optional HyperSync dependency to use this module") from e


logger = logging.getLogger(__name__)


class HypersyncVaultDiscover(VaultDiscoveryBase):
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
        recv_timeout: float = 90.0,
    ):
        """Create vault discover.

        :param web3:
            Current process web3 connection

        :param web3factory:
            Used to initialise connection in created worker threads/processes

        :param client:
            HyperSync client used to scan lead event data

        :parma recv_timeout:
            Hypersync core reading loop timeout.

        :param max_workers:
            How many worker processes use in multicall probing
        """
        super().__init__(max_workers=max_workers)
        self.web3 = web3
        self.web3factory = web3factory
        self.client = client
        self.recv_timeout = recv_timeout

    def get_topic_signatures(self) -> list[HexStr]:
        """Contracts must have at least one event of both these signatures

        - Find contracts emitting these events
        - Later prod these contracts to see which of them are proper vaults
        - We are likely having a real ERC-4262 contract if both events match,
          ``Deposit`` evnet might have few similar contracts
        """
        return [get_topic_signature_from_event(e) for e in get_vault_discovery_events(self.web3)]

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

    def fetch_leads(self, start_block: int, end_block: int, display_progress=True) -> LeadScanReport:
        # Don't leak async colored interface, as it is an implementation detail
        async def _hypersync_asyncio_wrapper():
            report = await self.scan_potential_vaults(start_block, end_block, display_progress)
            return report

        return asyncio.run(_hypersync_asyncio_wrapper())

    async def scan_potential_vaults(
        self,
        start_block: int,
        end_block: int,
        display_progress=True,
    ) -> LeadScanReport:
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
            chain_name = get_chain_name(self.web3.eth.chain_id)
            progress_bar = tqdm(
                total=end_block - start_block,
                desc=f"HypersyncVaultDiscover: scanning vault leads on {chain_name}",
            )
        else:
            progress_bar = None

        last_block = start_block
        timestamp = None

        logger.info(f"Streaming HyperSync")

        last_synced = None

        report = LeadScanReport(backend=self)
        report.old_leads = len(self.existing_leads)

        leads: dict[HexAddress, PotentialVaultMatch] = self.existing_leads.copy()
        matches = 0
        seen = set()

        while True:
            try:
                res = await asyncio.wait_for(receiver.recv(), timeout=self.recv_timeout)
            except asyncio.TimeoutError as e:
                # TODO: Not sure if we can recover from a timeout like this
                retry_sleep = 10
                logger.error("HyperSync receiver timed out, sleeping %f", retry_sleep)
                raise

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
                        timestamp = native_datetime_utc_fromtimestamp(int(block.timestamp, 16))
                        lead = PotentialVaultMatch(
                            chain=chain,
                            address=log.address.lower(),
                            first_seen_at_block=log.block_number,
                            first_seen_at=timestamp,
                        )
                        leads[log.address] = lead
                        report.new_leads += 1

                    # Hardcoded for now
                    deposit_kind = log.topics[0] == "0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7"
                    if deposit_kind:
                        lead.deposit_count += 1
                        report.deposits += 1
                    else:
                        lead.withdrawal_count += 1
                        report.withdrawals += 1

                    if log.address not in seen:
                        if lead.is_candidate():
                            # Return leads early, even if we still accumulate deposit and withdraw matches for them
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

        report.leads = leads
        return report
