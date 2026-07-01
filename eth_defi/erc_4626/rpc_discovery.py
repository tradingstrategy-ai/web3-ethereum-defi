"""Find ERC-4626 vaults onchain using JSON-RPC.

- Slow fallback method using only standard JSON-RPC calls when Hypersync server is not available

"""

import logging
from concurrent.futures.thread import ThreadPoolExecutor
from pprint import pformat

from eth_abi.exceptions import DecodingError
from eth_typing import HexAddress
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.abi import get_contract
from eth_defi.erc_4626.discovery_base import (
    LeadScanReport,
    PotentialVaultMatch,
    VaultDiscoveryBase,
    add_mellow_factory_candidate_lead,
    get_vault_discovery_events,
    get_vault_event_topic_map,
    is_deposit_event,
)
from eth_defi.event_reader.filter import Filter
from eth_defi.event_reader.reader import read_events_concurrent
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.event_reader.web3worker import create_thread_pool_executor
from eth_defi.mellow.abi import FACTORY_ABI_FILENAME
from eth_defi.mellow.discovery import create_mellow_factory_candidate, fetch_mellow_factories_for_chain
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

        Includes both standard ERC-4626 events and BrinkVault events.

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

    def build_mellow_factory_query(self, executor: ThreadPoolExecutor, start_block: int, end_block: int) -> dict | None:
        """Create a JSON-RPC event-reader query for Mellow factory leads.

        Mellow Core Vault discovery is factory-led. The JSON-RPC fallback mirrors
        the Hypersync path by scanning only configured factory addresses and
        storing decoded candidates as normal ``PotentialVaultMatch`` leads for
        the shared ``probe_vaults()`` pass.

        :param executor:
            Thread pool executor for ``read_events_concurrent()``.

        :param start_block:
            Inclusive start block.

        :param end_block:
            Inclusive end block.

        :return:
            Query keyword arguments, or ``None`` when the chain has no
            configured Mellow Core factory.
        """

        mellow_factories = fetch_mellow_factories_for_chain(self.web3.eth.chain_id)
        if not mellow_factories:
            return None

        mellow_factory = get_contract(self.web3, FACTORY_ABI_FILENAME)
        return {
            "executor": executor,
            "start_block": start_block,
            "end_block": end_block,
            "filter": Filter.create_filter(mellow_factories, [mellow_factory.events.Created]),
            "chunk_size": self.max_getlogs_range or get_logs_max_block_range(self.web3),
            "extract_timestamps": None,
        }

    def scan_mellow_factory_leads(
        self,
        report: LeadScanReport,
        chain: int,
        leads: dict[HexAddress, PotentialVaultMatch],
        mellow_query: dict | None,
    ) -> None:
        """Populate Mellow factory leads using the JSON-RPC fallback.

        :param report:
            Mutable scan report whose counters are updated.

        :param chain:
            EVM chain id.

        :param leads:
            Mutable shared lead map to receive Mellow factory leads.

        :param mellow_query:
            Query keyword arguments from :py:meth:`build_mellow_factory_query`,
            or ``None`` if the chain has no configured factory.
        """

        if mellow_query is None:
            return

        logger.info("Building Mellow factory eth_getLogs JSON-RPC query using read_events_concurrent(): %s", pformat(mellow_query))
        block_timestamps = {}
        for event in read_events_concurrent(**mellow_query):
            block_number = event["blockNumber"]
            timestamp = block_timestamps.get(block_number)
            if timestamp is None:
                timestamp = get_block_timestamp(self.web3, block_number)
                block_timestamps[block_number] = timestamp

            try:
                candidate = create_mellow_factory_candidate(
                    self.web3,
                    chain,
                    event,
                    timestamp,
                )
            except (DecodingError, ValueError) as e:
                logger.warning(
                    "Could not decode Mellow factory Created log at %s:%s tx %s: %s",
                    event.get("blockNumber"),
                    event.get("logIndex"),
                    event.get("transactionHash"),
                    e,
                )
                continue

            add_mellow_factory_candidate_lead(report, leads, candidate)

    def fetch_leads(
        self,
        start_block: int,
        end_block: int,
        display_progress=True,  # noqa: FBT002
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

        # Build topic map for classifying events (ERC-4626 and BrinkVault)
        topic_map = get_vault_event_topic_map(self.web3)

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

            # Classify event using topic map (supports ERC-4626 and BrinkVault)
            assert event["topics"][0].startswith("0x")
            event_kind = topic_map.get(event["topics"][0])
            if event_kind is not None and is_deposit_event(event_kind):
                lead.deposit_count += 1
                report.deposits += 1
            else:
                lead.withdrawal_count += 1
                report.withdrawals += 1

            if address not in seen:
                if lead.is_candidate():
                    # Return leads early, even if we still accumulate deposit and withdraw matches for them
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
                            "Matches": f"{len(seen):,}",
                        }
                    )

        if progress_bar is not None:
            progress_bar.close()

        report.leads = leads

        # ``read_events_concurrent()`` owns the executor lifecycle. Use a fresh
        # pool for the Mellow factory pass so the preceding ERC-4626/BrinkVault
        # event scan cannot leave us with a shut down executor.
        if fetch_mellow_factories_for_chain(chain):
            self.scan_mellow_factory_leads(
                report,
                chain,
                leads,
                self.build_mellow_factory_query(
                    create_thread_pool_executor(
                        self.web3factory,
                        context=None,
                        max_workers=self.max_workers,
                    ),
                    start_block,
                    end_block,
                ),
            )

        return report
