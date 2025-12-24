"""Find ERC-4626 vaults onchain using JSON-RPC.

- Slow fallback method using only standard JSON-RPC calls when Hypersync server is not available

"""

import logging

from concurrent.futures.thread import ThreadPoolExecutor
from pprint import pformat

from eth_typing import HexAddress
from web3 import Web3

from tqdm_loggable.auto import tqdm

from eth_defi.erc_4626.discovery_base import get_vault_discovery_events, LeadScanReport
from eth_defi.erc_4626.discovery_base import VaultDiscoveryBase, PotentialVaultMatch
from eth_defi.event_reader.reader import read_events_concurrent
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.event_reader.web3worker import create_thread_pool_executor
from eth_defi.provider.log_block_range import get_logs_max_block_range
from eth_defi.timestamp import get_block_timestamp
from eth_defi.utils import addr


logger = logging.getLogger(__name__)


class JSONRPCVaultDiscover(VaultDiscoveryBase):
    """Autoscan the chain for 4626 vaults.

    - First build map of potential contracts using :py:meth:`scan_potential_vaults`
    - Then probe given contracts and determine their ERC-4626 vault properties

    See :ref:`scan-erc_4626_vaults` for usage.
    """

    def __init__(
        self,
        web3: Web3,
        web3factory: Web3Factory,
        max_workers: int = 8,
        max_getlogs_range: int | None = None,
    ):
        """Create vault discover.

        :param web3:
            Current process web3 connection

        :param web3factory:
            Used to initialise connection in created worker threads/processes

        :param max_workers:
            How many worker processes use in multicall probing
        """

        super().__init__(max_workers=max_workers)

        self.web3 = web3
        self.web3factory = web3factory
        self.max_getlogs_range = max_getlogs_range

    def build_query(self, executor: ThreadPoolExecutor, start_block: int, end_block: int) -> dict:
        """Create a read_events_concurrent arguments to discover new vaults.

        See :py:func:`eth_defi.event_reader.reader.read_events_concurrent`
        """

        return {
            "executor": executor,
            "start_block": start_block,
            "end_block": end_block,
            "events": get_vault_discovery_events(self.web3),
            "chunk_size": self.max_getlogs_range or get_logs_max_block_range(self.web3),  # Allow command line override for crappy supported chains like TAC
            "extract_timestamps": None,  # We only need timestamps for first event per vault
        }

    def fetch_leads(
        self,
        start_block: int,
        end_block: int,
        display_progress=True,
    ) -> LeadScanReport:
        """Identify smart contracts emitting 4626 like events.

        - Scan all event matches using RPC
        """
        assert end_block > start_block

        logger.info(
            "Starting JSONRPCVaultDiscover.fetch_leads() on chain %d from block %d to %d, progress is %s",
            self.web3.eth.chain_id,
            start_block,
            end_block,
            display_progress,
        )

        chain = self.web3.eth.chain_id

        executor = create_thread_pool_executor(
            self.web3factory,
            context=None,
            max_workers=self.max_workers,
        )

        query = self.build_query(executor, start_block, end_block)

        logger.info("Building eth_getLogs JSON-RPC query using read_events_concurrent(): %s", pformat(query))

        if display_progress:
            progress_bar = tqdm(
                total=end_block - start_block,
                desc=f"JSONRPCVaultDiscover: Scanning leads on chain {self.web3.eth.chain_id}",
            )
        else:
            progress_bar = None

        last_block = start_block
        timestamp = None

        matches = 0
        seen = set()

        report = LeadScanReport(backend=self)
        report.old_leads = len(self.existing_leads)

        leads: dict[HexAddress, PotentialVaultMatch] = self.existing_leads.copy()

        for event in read_events_concurrent(**query):
            current_block = event["blockNumber"]
            address = addr(event["address"].lower())
            lead = leads.get(address)

            if not lead:
                # Fresh match
                block_number = current_block
                timestamp = get_block_timestamp(self.web3, block_number)
                lead = PotentialVaultMatch(
                    chain=chain,
                    address=address,
                    first_seen_at_block=block_number,
                    first_seen_at=timestamp,
                )
                leads[address] = lead
                report.new_leads += 1

            # Hardcoded for now
            assert event["topics"][0].startswith("0x")
            deposit_kind = event["topics"][0] == "0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7"
            if deposit_kind:
                lead.deposit_count += 1
                report.deposits += 1
            else:
                lead.withdrawal_count += 1
                report.withdrawals += 1

            if address not in seen:
                if lead.is_candidate():
                    # Return leads early, even if we still accumulate deposit and withdraw matches for them
                    matches += 1
                    seen.add(address)
                    logger.debug("Found lead %s", address)

            if progress_bar is not None:
                progress_bar.update(current_block - last_block)
                last_block = current_block

                # Add extra data to the progress bar
                if timestamp is not None:
                    progress_bar.set_postfix(
                        {
                            "At": timestamp,
                            "Block": f"{last_block:,}",
                            "Matches": f"{matches:,}",
                        }
                    )

        if progress_bar is not None:
            progress_bar.close()

        report.leads = leads

        return report
