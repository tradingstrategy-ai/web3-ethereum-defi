"""Find ERC-4626 vaults onchain using HyperSync.

- Use HyperSync's index to quickly get ERC-4626 identification events from the chain
- We do not use raw JSON-RPC, because Etheruem JSON-RPC is badly designed piece of crap for reading data
- Use tons of heurestics to figure out what's going on with vaults
- This is because ERC-4626, like many other ERC standards, are very poorly designed, lacking proper identification events and interface introspection

"""

import asyncio
import logging

from eth_abi.exceptions import DecodingError
from eth_typing import HexAddress, HexStr
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.chain import get_chain_name
from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.erc_4626.discovery_base import HardcodedVaultLeadSources, LeadScanReport, PotentialVaultMatch, VaultDiscoveryBase, add_mellow_factory_candidate_lead, get_vault_discovery_events, get_vault_event_topic_map, is_configuration_event, is_deposit_event
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.hypersync.hypersync_timestamp import HypersyncFlaky, get_hypersync_block_height_with_retries, is_hypersync_next_block_range_error, is_hypersync_rate_limit_error, is_hypersync_retryable_runtime_error
from eth_defi.mellow.discovery import create_mellow_factory_candidate, fetch_mellow_created_event_topic, fetch_mellow_factories_for_chain, is_mellow_factory_log

try:
    import hypersync
    from hypersync import BlockField, LogField
except ImportError as e:
    raise ImportError("Install the library with optional HyperSync dependency to use this module") from e

from eth_defi.hypersync.session import open_hypersync_stream

logger = logging.getLogger(__name__)

VAULT_LEAD_HEIGHT_CHECK_ATTEMPTS = 3
VAULT_LEAD_HEIGHT_CHECK_RETRY_SLEEP = 30


class HypersyncCrappedOut(Exception):
    pass


def _raise_recoverable_hypersync_error(e: RuntimeError, reason: str) -> None:
    """Convert known recoverable Hypersync runtime errors to discovery errors."""
    if not is_hypersync_retryable_runtime_error(e):
        return
    if is_hypersync_rate_limit_error(e):
        raise HypersyncCrappedOut(f"Hypersync rate limited [{reason}]: {e}") from e
    if is_hypersync_next_block_range_error(e):
        raise HypersyncCrappedOut(f"Hypersync stream pagination failed [{reason}]: {e}") from e


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
        """Get topic signatures that can seed vault leads.

        - Find contracts emitting these events
        - Later prod these contracts to see which of them are proper vaults
        - A deposit event is enough to create a lead. Some large vaults have
          not emitted withdrawal events yet because funds are still locked or
          the vault is in a pre-deposit phase.
        - Also includes protocol-specific vault flow events
        """
        return [get_topic_signature_from_event(e) for e in get_vault_discovery_events(self.web3)]

    def build_query(self, start_block: int, end_block: int) -> hypersync.Query:
        """Create HyperSync query that extracts all potential lead events from the chain.

        See example here: https://github.com/enviodev/hypersync-client-python/blob/main/examples/all-erc20-transfers.py
        """

        # [['0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7'], ['0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db']]
        log_selections = [hypersync.LogSelection(topics=[[sig]]) for sig in self.get_topic_signatures()]
        mellow_factories = fetch_mellow_factories_for_chain(self.web3.eth.chain_id)
        if mellow_factories:
            log_selections.append(
                hypersync.LogSelection(
                    address=mellow_factories,
                    topics=[[fetch_mellow_created_event_topic()]],
                )
            )

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
                    LogField.LOG_INDEX,
                    LogField.ADDRESS,
                    LogField.TRANSACTION_HASH,
                    LogField.TOPIC0,
                    LogField.TOPIC1,
                    LogField.TOPIC2,
                    LogField.TOPIC3,
                    LogField.DATA,
                ],
            ),
        )
        return query

    def clip_end_block_to_available_height(self, start_block: int, end_block: int) -> int:
        """Clip scan end block to heights available from RPC and Hypersync.

        Hypersync may lag the chain head by a few blocks. Asking it to stream
        over that head gap can fail with an ``inner receiver`` pagination error.
        We also cap by RPC height so follow-up vault probing does not request a
        future block from the JSON-RPC provider.

        :param start_block:
            Inclusive scan start block.

        :param end_block:
            Requested inclusive scan end block.

        :return:
            End block that both Hypersync and RPC should be able to serve.
        """
        try:
            rpc_height = self.web3.eth.block_number
            hypersync_height = get_hypersync_block_height_with_retries(
                self.client,
                attempts=VAULT_LEAD_HEIGHT_CHECK_ATTEMPTS,
                retry_sleep=VAULT_LEAD_HEIGHT_CHECK_RETRY_SLEEP,
                reason="vault-lead-discovery",
            )
        except HypersyncFlaky as e:
            raise HypersyncCrappedOut(f"Hypersync failed during vault lead height check: {e}") from e

        clipped_end_block = min(end_block, rpc_height, hypersync_height)
        if clipped_end_block < end_block:
            logger.warning(
                "Clipping vault lead discovery end block from %s to %s (RPC height %s, Hypersync height %s)",
                f"{end_block:,}",
                f"{clipped_end_block:,}",
                f"{rpc_height:,}",
                f"{hypersync_height:,}",
            )

        return clipped_end_block

    def process_log(
        self,
        report: LeadScanReport,
        leads: dict[HexAddress, PotentialVaultMatch],
        topic_map: dict[str, object],
        chain: int,
        log: hypersync.Log,
        block_timestamp,
        seen: set[HexAddress],
    ) -> None:
        """Process one Hypersync log into the shared lead map.

        Both ERC-4626-like event leads and Mellow factory leads are written to
        ``leads`` as ``PotentialVaultMatch`` objects. Mellow keeps decoded
        factory metadata on the lead for the later detection construction step.

        :param report:
            Mutable scan report.

        :param leads:
            Mutable lead map keyed by lower-case vault address.

        :param topic_map:
            ERC-4626-style event topic classification map.

        :param chain:
            EVM chain id.

        :param log:
            Hypersync log.

        :param block_timestamp:
            Naive UTC timestamp for the log block.

        :param seen:
            Addresses already counted as matched candidates.

        :return:
            None.
        """

        if is_mellow_factory_log(chain, log.address, log.topics[0]):
            try:
                candidate = create_mellow_factory_candidate(
                    self.web3,
                    chain,
                    log,
                    block_timestamp,
                )
            except (DecodingError, ValueError) as e:
                logger.warning(
                    "Could not decode Mellow factory Created log at %s:%s tx %s: %s",
                    log.block_number,
                    getattr(log, "log_index", None),
                    log.transaction_hash,
                    e,
                )
                return

            add_mellow_factory_candidate_lead(report, leads, candidate)
            return

        address_key = HexAddress(log.address.lower())
        lead = leads.get(address_key)
        first_seen_timestamp = None

        if not lead:
            first_seen_timestamp = block_timestamp
            lead = PotentialVaultMatch(
                chain=chain,
                address=address_key,
                first_seen_at_block=log.block_number,
                first_seen_at=first_seen_timestamp,
            )
            leads[address_key] = lead
            report.new_leads += 1

        event_kind = topic_map.get(log.topics[0])
        if event_kind is not None and is_deposit_event(event_kind):
            lead.deposit_count += 1
            report.deposits += 1
        elif event_kind is not None and is_configuration_event(event_kind):
            lead.configuration_count = getattr(lead, "configuration_count", 0) + 1
        else:
            lead.withdrawal_count += 1
            report.withdrawals += 1

        if address_key not in seen and lead.is_candidate():
            seen.add(address_key)

    def fetch_log_timestamp(self, block_lookup: dict, log: hypersync.Log):
        """Resolve a Hypersync log timestamp from the batch block lookup."""

        block = block_lookup[log.block_number]
        return native_datetime_utc_fromtimestamp(int(block.timestamp, 16) if isinstance(block.timestamp, str) else block.timestamp)

    def scan_vaults(
        self,
        start_block: int,
        end_block: int,
        display_progress=True,
        hardcoded_lead_sources: HardcodedVaultLeadSources | None = None,
    ) -> LeadScanReport:
        """Scan vaults using a Hypersync-safe head block."""
        end_block = self.clip_end_block_to_available_height(start_block, end_block)

        if end_block <= start_block:
            logger.info(
                "No new Hypersync vault lead blocks to scan: start block %s, available end block %s",
                f"{start_block:,}",
                f"{end_block:,}",
            )
            return LeadScanReport(
                backend=self,
                leads=self.existing_leads.copy(),
                old_leads=len(self.existing_leads),
                start_block=start_block,
                end_block=end_block,
            )

        return super().scan_vaults(
            start_block,
            end_block,
            display_progress=display_progress,
            hardcoded_lead_sources=hardcoded_lead_sources,
        )

    def fetch_leads(self, start_block: int, end_block: int, display_progress=True, attempts=3, retry_sleep=30) -> LeadScanReport:
        """
        Synchronous wrapper around async lead scanning.

        :param display_progress:
            Show progress bar.

        :param attempts:
            Deal with HyperSync flakiness by retrying the scan this many times.

        :param retry_sleep:
            How long to sleep between retries.
        """

        assert attempts > 0, "attempts must be at least 1"

        # Don't leak async colored interface, as it is an implementation detail
        async def _hypersync_asyncio_wrapper() -> LeadScanReport:
            last_exception = None
            for attempt in range(attempts):
                try:
                    report = await self.scan_potential_vaults(start_block, end_block, display_progress)
                    return report
                except HypersyncCrappedOut as e:
                    last_exception = e
                    logger.error(f"HyperSync scan attempt {attempt + 1} of {attempts} failed: {e}")
                    if attempt + 1 >= attempts:
                        logger.error("All HyperSync scan attempts failed, giving up")
                        raise
                    else:
                        logger.info(f"Retrying HyperSync scan after {retry_sleep} seconds backoff")
                        await asyncio.sleep(retry_sleep)

            # Should never reach here, but raise if we somehow do
            raise last_exception or RuntimeError("HyperSync scan failed with no exception recorded")

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

        # Build topic map for classifying events (ERC-4626 and BrinkVault)
        topic_map = get_vault_event_topic_map(self.web3)

        logger.info("Building HyperSync query")
        query = self.build_query(start_block, end_block)

        logger.info(
            "Hypersync stream open: chain %d, blocks %d-%d (%d blocks) [vault-lead-discovery]",
            chain,
            start_block,
            end_block,
            end_block - start_block,
        )
        # start the stream
        try:
            receiver = await open_hypersync_stream(self.client, query)
        except RuntimeError as e:
            _raise_recoverable_hypersync_error(e, "vault-lead-discovery")
            raise

        if display_progress:
            chain_name = get_chain_name(self.web3.eth.chain_id)
            progress_bar = tqdm(
                total=end_block - start_block,
                desc=f"HypersyncVaultDiscover: scanning vault leads on {chain_name}",
            )
        else:
            progress_bar = None

        last_block = start_block

        logger.info("Streaming HyperSync")

        last_synced = None

        report = LeadScanReport(backend=self)
        report.old_leads = len(self.existing_leads)
        report.leads = self.existing_leads.copy()
        seen = set()

        while True:
            try:
                res = await asyncio.wait_for(receiver.recv(), timeout=self.recv_timeout)
            except asyncio.TimeoutError as e:
                logger.error("HyperSync receiver timed out [vault-lead-discovery]")
                raise HypersyncCrappedOut(f"Cannot recover from HyperSync stream timeout after {self.recv_timeout} seconds [vault-lead-discovery]") from e
            except RuntimeError as e:
                _raise_recoverable_hypersync_error(e, "vault-lead-discovery")
                raise

            # exit if the stream finished
            if res is None:
                break

            current_block = res.next_block

            if res.data.logs:
                block_lookup = {b.number: b for b in res.data.blocks}
                log: hypersync.Log
                for log in res.data.logs:
                    self.process_log(
                        report,
                        report.leads,
                        topic_map,
                        chain,
                        log,
                        self.fetch_log_timestamp(block_lookup, log),
                        seen,
                    )

            last_synced = res.archive_height

            if progress_bar is not None:
                progress_bar.update(current_block - last_block)
                last_block = current_block

                # Add extra data to the progress bar
                progress_bar.set_postfix(
                    {
                        "Matches": f"{len(seen):,}",
                    }
                )

        logger.info(f"HyperSync sees {last_synced} as the last block")

        if progress_bar is not None:
            progress_bar.close()

        return report
