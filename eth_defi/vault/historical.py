"""Read historical state of vaults.

- Use multicall to get data points for multiple vaults once
- Include
    - Share price
    - TVL
    - Fees

See :py:class:`VaultHistoricalReadMulticaller` for usage.

Monad does not provide archive-complete historical state. Its price scans probe
the configured provider and begin at the oldest block where the scanner's
Multicall contract can execute.
"""

import datetime
import logging
import os
import tempfile

try:
    import fcntl
except ImportError:
    fcntl = None
from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Callable, Literal, TypedDict

from eth_typing import HexAddress
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi import hypersync
from eth_defi.chain import EVM_BLOCK_TIMES, get_chain_name
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.vault import DENOMINATION_UNAVAILABLE_EXCHANGE_RATE, UNKNOWN_EXCHANGE_RATE, ERC4626HistoricalReader, VaultReaderState
from eth_defi.erc_4626.warmup import warmup_vault_reader
from eth_defi.event_reader.multicall_batcher import BatchCallState, EncodedCall, EncodedCallResult, get_multicall_contract, read_multicall_historical, read_multicall_historical_stateful
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.middleware import ProbablyNodeHasNoBlock
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.rpcdb import RPCRequestStats
from eth_defi.token import TokenDetails, TokenDiskCache, fetch_erc20_details
from eth_defi.utils import chunked
from eth_defi.vault.base import MAX_VAULT_PRICE_ROW_AGE, VAULT_PRICE_REFRESH_INTERVAL, VaultBase, VaultHistoricalRead, VaultHistoricalReader, VaultSpec, is_meaningful_usd_tvl, verify_parquet_file
from eth_defi.vault.risk import BROKEN_VAULT_CONTRACTS
from eth_defi.vault.rpc_scan_state import save_reader_publication_journal
from eth_defi.version_info import stamp_parquet_schema_metadata

logger = logging.getLogger(__name__)


#: Monad mainnet chain ID.
MONAD_CHAIN_ID = 143

#: Canonical explanation of Monad's provider-dependent historical state window.
MONAD_HISTORICAL_DATA_DOCUMENTATION_URL = "https://docs.monad.xyz/developer-essentials/historical-data"


#: List of contracts we cannot scan.
#: These will bomb out with out of gas.
#: See Mantle issues.
DEFAULT_BLACK_LIST = [
    # TODO
]


class ParquetScanResult(TypedDict):
    """Result of generating historical prices Parquet file.

    Freshness is measured using real source ``timestamp`` values, never the
    Parquet ``written_at`` time. A successful live scan targets an unchanged
    row before the maximum 14-day age for a vault whose last supported USD TVL
    estimate entered at $1,500 (remaining eligible until below $1,000). When
    no new TVL can be read, the last observed value is used for monitoring;
    its present-day TVL is unknown. Source outages are reported as overdue
    rather than represented by fabricated rows.
    """

    existing: bool
    chain_id: int
    rows_written: int
    rows_deleted: int
    existing_row_count: int
    output_fname: Path
    file_size: int
    chunks_done: int
    start_block: int
    end_block: int

    #: Newly emitted rows keyed by lower-case vault address.
    rows_written_by_vault: dict[str, int]

    #: Newly emitted rows with a non-null share price keyed by vault address.
    price_rows_written_by_vault: dict[str, int]

    #: Unchanged real observations retained solely for the early deadline.
    freshness_rows_written: int

    #: Vaults verified above the $1,500 entry or $1,000 exit TVL limits.
    freshness_eligible_vaults: int

    #: Addresses with a source row older than 14 days at the live scan horizon.
    overdue_vaults: dict[str, str]

    #: Observed addresses whose denomination lacks a supported USD conversion;
    #: these are sampled but excluded from USD-qualified overdue counts.
    unknown_conversion_vaults: list[str]

    #: Token resolution failed; retain prior coverage without certifying USD.
    denomination_unavailable_vaults: list[str]

    #: A failed audit does not invalidate durable rows or returned progress.
    audit_error: str | None

    #: Actual provider boundary observation, present only after state eviction.
    historical_state_window: dict | None

    reader_states: dict[VaultSpec, dict] | None


def pformat_scan_result(self) -> str:
    """Format a stateful or stateless Parquet scan result.

    :param self:
        Result returned by :func:`scan_historical_prices_to_parquet`.
    :return:
        Multi-line operator summary.
    """

    reader_state_count = len(self["reader_states"] or {})
    return f"ParquetScanResult(chain_id={self['chain_id']}, \nstart_block={self['start_block']:,}, \nend_block={self['end_block']:,}, \nrows_written={self['rows_written']:,}, \nrows_deleted={self['rows_deleted']:,}, \nexisting_row_count={self['existing_row_count']:,}, \nreader_state_count={reader_state_count}, \noutput_fname={self['output_fname']}, \nfile_size={self['file_size']:,} bytes, \nchunks_done={self['chunks_done']:,})"


class VaultReadNotSupported(Exception):
    """Vault cannot be read due to misconfiguration somewhere."""


def fetch_monad_historical_state_start_block(
    web3: Web3,
    start_block: int,
    end_block: int,
    report_irrecoverable_gap: bool = True,
) -> int:
    """Find the earliest block whose Monad state the connected provider can read.

    Monad retains all historical transactional data, but its RPC providers retain
    historical state only while their state tries fit on disk. The window is
    provider-specific and may move forward over time. Probe the same Multicall3
    contract used by the historical price reader and binary-search the boundary
    between unavailable and available state before creating any output file.
    See `Monad historical data documentation <https://docs.monad.xyz/developer-essentials/historical-data>`_.

    :param web3:
        Monad JSON-RPC connection to probe.

    :param start_block:
        Earliest block otherwise requested by the caller.

    :param end_block:
        Latest block otherwise requested by the caller. It must be readable,
        because an unavailable end block indicates a provider failure rather
        than ordinary historical-state eviction.

    :param report_irrecoverable_gap:
        Whether this range represents required price history. Disable for a
        bounded capability measurement that does not imply lost observations.

    :return:
        The earliest readable block within ``start_block`` and ``end_block``.

    :raises RuntimeError:
        If the provider cannot read state at ``end_block``.
    """
    assert web3.eth.chain_id == MONAD_CHAIN_ID, f"Expected Monad chain {MONAD_CHAIN_ID}, got {web3.eth.chain_id}"
    assert start_block >= 0, f"Invalid start block: {start_block}"
    assert end_block >= start_block, f"End block {end_block} is before start block {start_block}"

    multicall = get_multicall_contract(web3)

    def fetch_state_availability(block_number: int) -> bool:
        """Probe the execution path used by historical vault batches.

        Block headers and events survive Monad state eviction, so checking
        their existence cannot establish whether a historical eth_call works.
        Multicall's returned block also guards against an upstream silently
        serving current state for a requested historical block.

        :param block_number: Requested historical execution block.
        :return: Whether Multicall executes at that exact block.
        """
        try:
            received_block_number = multicall.functions.getBlockNumber().call(block_identifier=block_number)
        except (BadFunctionCallOutput, ContractLogicError, ProbablyNodeHasNoBlock):
            return False
        return received_block_number == block_number

    if fetch_state_availability(start_block):
        return start_block

    if not fetch_state_availability(end_block):
        raise RuntimeError(f"Monad provider cannot read state at requested end block {end_block:,}. Check the RPC provider and {MONAD_HISTORICAL_DATA_DOCUMENTATION_URL}.")

    unavailable_block = start_block
    available_block = end_block
    while available_block - unavailable_block > 1:
        candidate_block = (unavailable_block + available_block) // 2
        if fetch_state_availability(candidate_block):
            available_block = candidate_block
        else:
            unavailable_block = candidate_block

    web3._vault_historical_state_window = {"checked_at": native_datetime_utc_now().isoformat(), "readable_start_block": available_block, "head_block": end_block, "retention_seconds": (end_block - available_block) * EVM_BLOCK_TIMES[143], "irrecoverable_gap_start": start_block, "irrecoverable_gap_end": available_block}
    if report_irrecoverable_gap:
        logger.warning("Monad irrecoverable state gap: [%d, %d); previously committed rows are preserved", start_block, available_block)
    logger.warning(
        "Monad historical state before block %d is unavailable from the configured RPC provider; clipping price scan start from %d. See %s",
        available_block,
        start_block,
        MONAD_HISTORICAL_DATA_DOCUMENTATION_URL,
    )
    return available_block


class VaultHistoricalReadMulticaller:
    """Read historical data from multiple vaults using Multicall and JSON-RPC polling.

    Archive-capable nodes provide the historical state needed on most EVM
    chains. Monad instead has a provider-specific recent state window; callers
    must use :func:`fetch_monad_historical_state_start_block` before starting a
    scan.
    """

    def __init__(
        self,
        web3factory: Web3Factory,
        supported_quote_tokens: set[TokenDetails] | None,
        max_workers: int = 8,
        token_cache: TokenDiskCache | None = None,
        require_multicall_result: bool = False,
        write_all_samples: bool = False,
        enforce_live_freshness: bool = False,
        last_retained_at: dict[str, datetime.datetime] | None = None,
        hypersync_client: "hypersync.HypersyncClient | None" = None,
        timestamp_cache_file: Path = DEFAULT_TIMESTAMP_CACHE_FOLDER,
        rpc_request_stats: RPCRequestStats | None = None,
    ) -> None:
        """Configure the multicall reader and its live-row retention state.

        The reader keeps source timestamps in memory for one scan. The Parquet
        writer seeds them from committed rows before the scan begins.

        :param web3factory:
            Factory for worker JSON-RPC connections.
        :param supported_quote_tokens:
            Optional supported denomination-token set.
        :param max_workers:
            Maximum concurrent historical read workers.
        :param token_cache:
            Token metadata cache; a default is created when omitted.
        :param require_multicall_result:
            Require a successful result from every requested multicall.
        :param write_all_samples:
            Retain every real sample, including unchanged samples.
        :param enforce_live_freshness:
            Retain an unchanged eligible EVM row after seven days.
        :param last_retained_at:
            Last committed source timestamp by lower-case vault address.
        :param hypersync_client:
            Client for cached historical block timestamps.
        :param timestamp_cache_file:
            Shared per-chain timestamp cache location.
        :param rpc_request_stats:
            Optional JSON-RPC usage accumulator.
        :return:
            ``None``.
        """

        if supported_quote_tokens is not None:
            for a in supported_quote_tokens:
                assert isinstance(a, TokenDetails)

        self.supported_quote_tokens = supported_quote_tokens
        self.web3factory = web3factory
        self.max_workers = max_workers
        self.hypersync_client = hypersync_client
        self.timestamp_cache_file = timestamp_cache_file
        self.rpc_request_stats = rpc_request_stats

        if token_cache is None:
            token_cache = TokenDiskCache()

        self.token_cache = token_cache
        self.require_multicall_result = require_multicall_result
        self.write_all_samples = write_all_samples
        self.enforce_live_freshness = enforce_live_freshness
        self.last_retained_at = last_retained_at or {}
        self.freshness_rows_written = 0
        self.latest_observed_at: dict[str, datetime.datetime] = {}
        self.latest_usd_tvl: dict[str, Decimal] = {}
        self.prior_max_assets: dict[str, Decimal] = {}
        self.unknown_conversion_vaults: set[str] = set()
        self.denomination_unavailable_vaults: set[str] = set()

        self.readers: dict[HexAddress, VaultHistoricalReader] = {}

    def validate_vaults(
        self,
        vaults: list[VaultBase],
    ):
        """Check that we can read these vaults.

        - Validate that we know how to read vaults

        :raise VaultReadNotSupported:
            In the case we cannot read some of the vaults
        """
        for vault in vaults:
            denomination_token = vault.denomination_token
            if self.supported_quote_tokens is not None:
                if denomination_token not in self.supported_quote_tokens:
                    raise VaultReadNotSupported(f"Vault {vault} has denomination token {denomination_token} which is not supported denomination token set: {self.supported_quote_tokens}")

    def _prepare_reader(self, vault: VaultBase, stateful=False) -> VaultHistoricalReader:
        """Create a historical reader and validate its state contract.

        Stateful multicall filtering requires every call to have a
        :class:`~eth_defi.event_reader.multicall_batcher.BatchCallState`.
        Validate this at construction so a new protocol reader cannot fail
        later in token preparation or inside a joblib worker with an obscure
        missing-attribute error.

        :param vault:
            Vault adapter creating the protocol-specific reader.
        :param stateful:
            Whether persistent adaptive scan state is required.
        :return:
            Validated protocol-specific historical reader.
        :raise TypeError:
            If a stateful reader does not initialise ``reader_state``.
        """

        reader = vault.get_historical_reader(stateful=stateful)
        vault_id = f"{vault.__class__.__name__} at {vault.address}"
        if not isinstance(reader, VaultHistoricalReader):
            raise TypeError(f"{vault_id} returned invalid historical reader {reader!r}")
        if stateful and not reader.uses_contextual_history and not isinstance(reader.reader_state, BatchCallState):
            raise TypeError(f"{reader.__class__.__name__} did not initialise a BatchCallState reader_state for a stateful scan of {vault_id}")
        return reader

    def _prepare_denomination_token(self, reader: VaultHistoricalReader) -> HexAddress | None:
        """Prepare an optional denomination token address in a worker.

        Synthetic accounting units, such as Asseto's collateral-less USD
        products, intentionally return ``None`` because there is no ERC-20
        token metadata to prepare.

        :param reader:
            Historical vault reader being prepared.
        :return:
            ERC-20 denomination token address, or ``None`` for a synthetic
            accounting unit.
        """

        state = reader.reader_state
        if state:
            if state.denomination_token_address is not None:
                return state.denomination_token_address

        address = reader.vault.fetch_denomination_token_address()

        # Save for the next run as this is slow to fetch
        if state:
            state.denomination_token_address = address

        return address

    def _prepare_share_token(self, reader: ERC4626HistoricalReader) -> HexAddress:
        """Run in subprocess"""

        state = reader.reader_state
        if state:
            if state.share_token_address is not None:
                return state.share_token_address

        address = reader.vault.fetch_share_token_address()

        # Save for the next run as this is slow to fetch
        if state:
            state.share_token_address = address

        return address

    def _run_warmup(
        self,
        readers: dict[HexAddress, VaultHistoricalReader],
        block_number: int,
    ) -> None:
        """Run warmup checks on all vault readers.

        Tests each vault's supported calls to detect which ones revert.
        Results are stored in reader_state.call_status and used to skip
        broken calls during the actual scan.

        See README-reader-states.md for documentation.

        :param readers:
            Dict of vault_address -> VaultHistoricalReader

        :param block_number:
            Block number to use for testing
        """
        checked_count = 0
        broken_count = 0

        for reader in readers.values():
            vault_results = warmup_vault_reader(reader, block_number)
            if vault_results:
                checked_count += len(vault_results)
                broken_count += sum(1 for _, reverts in vault_results.values() if reverts)

        if checked_count > 0:
            logger.info("Warmup complete: checked %d calls across %d vaults, %d broken", checked_count, len(readers), broken_count)

    def _prepare_multicalls(self, reader: VaultHistoricalReader, stateful=False) -> Iterable[tuple[EncodedCall, BatchCallState]]:
        """Run in subprocess"""
        for call in reader.construct_multicalls():
            yield call, reader.reader_state

    def prepare_readers(
        self,
        vaults: list[VaultBase],
        stateful=False,
        saved_states: dict[VaultSpec, dict] | None = None,
    ) -> dict[HexAddress, VaultHistoricalReader]:
        """Create readrs for vaults."""
        logger.info(
            "Preparing readers for %d vaults, using %d threads, stateful is %s",
            len(vaults),
            self.max_workers,
            stateful,
        )

        assert len(vaults) > 0

        chain_id = vaults[0].chain_id

        # Each vault reader creation causes ~5 RPC call as it initialises the token information.
        # We do parallel to cut down the time here.
        logger.info("Preparing readers %d vaults", len(vaults))
        results = Parallel(n_jobs=self.max_workers, backend="threading")(delayed(self._prepare_reader)(v, stateful) for v in vaults)
        readers = {r.address: r for r in results}

        # Hydrate states from the previous run
        loaded_state_count = 0
        cached_denomination_tokens = 0
        cached_share_tokens = 0
        if saved_states:
            for reader in readers.values():
                if reader.reader_state is None:
                    continue
                spec = reader.vault.get_spec()
                existing_state = saved_states.get(spec)
                if existing_state:
                    reader.reader_state.load(existing_state)
                    loaded_state_count += 1

                    if existing_state.get("denomination_token_address") is not None:
                        # Ensure we have denomination token address loaded
                        cached_denomination_tokens += 1

                    if existing_state.get("share_token_address") is not None:
                        # Ensure we have share token address loaded
                        cached_share_tokens += 1

        logger.info(
            "Prepared %d readers, loaded %d states, had %d cached denomination tokens, %s cached share tokens",
            len(readers),
            loaded_state_count,
            cached_denomination_tokens,
            cached_share_tokens,
        )

        # Warm up token disk cache for denomination tokens.
        # We need to load this up before because we need to calculate share price for amount 1 in denomination token (USDC)
        logger.info("Preparing denomination/share tokens for %d vaults", len(vaults))
        token_load_max_workers = self.max_workers
        token_addresses = Parallel(n_jobs=token_load_max_workers, backend="threading")(delayed(self._prepare_denomination_token)(r) for r in readers.values())
        denomination_token_addresses = [a for a in token_addresses if a is not None]
        token_addresses = Parallel(n_jobs=token_load_max_workers, backend="threading")(delayed(self._prepare_share_token)(r) for r in readers.values())
        share_token_addresses = [a for a in token_addresses if a is not None]

        addresses = denomination_token_addresses + share_token_addresses

        logger.info(
            "Warmin up token cache for %d tokens, cache is %s",
            len(addresses),
            self.token_cache,
        )
        self.token_cache.load_token_details_with_multicall(
            chain_id=chain_id,
            web3factory=self.web3factory,
            addresses=addresses,
        )

        # Because of JSON-RPC eth_call asset() call in fetch_denomination_token()
        # slowing down everything, we need to populate these
        populated_tokens = 0
        if saved_states:
            for reader in readers.values():
                if reader.reader_state is None:
                    continue
                denomination_token_address = reader.reader_state.denomination_token_address
                if denomination_token_address is not None:
                    vault = reader.vault
                    vault.__dict__["denomination_token"] = fetch_erc20_details(
                        vault.web3,
                        token_address=denomination_token_address,
                        chain_id=vault.chain_id,
                        cache=self.token_cache,
                    )
                    populated_tokens += 1

                share_token_address = reader.reader_state.share_token_address
                if share_token_address is not None:
                    vault = reader.vault
                    vault.__dict__["share_token"] = fetch_erc20_details(
                        vault.web3,
                        token_address=share_token_address,
                        chain_id=vault.chain_id,
                        cache=self.token_cache,
                    )
                    populated_tokens += 1

        logger.info("Populated cache warmed up denomination tokens for %d vaults", populated_tokens)

        return readers

    def generate_vault_historical_calls(
        self,
        readers: dict[HexAddress, VaultHistoricalReader],
        display_progress: bool = True,
    ) -> Iterable[tuple[EncodedCall, BatchCallState]]:
        """Generate multicalls for each vault to read its state at any block."""
        # Each vault reader creation causes ~5 RPC call as it initialises the token information.
        # We do parallel to cut down the time here.
        logger.info("Preparing historical multicalls for %d readers using %d workers", len(readers), self.max_workers)

        if display_progress:
            progress_bar = tqdm(
                total=len(readers),
                unit=" readers",
                desc=f"Preparing historical multicalls for {len(readers)} readers using {self.max_workers} workers",
            )
        else:
            progress_bar = None

        results = [self._prepare_multicalls(r) for r in readers.values()]
        # results = Parallel(n_jobs=self.max_workers, backend="threading")(delayed(self._prepare_multicalls)(r) for r in readers.values())

        for r in results:
            if progress_bar is not None:
                progress_bar.update(1)
            yield from r

        if progress_bar is not None:
            progress_bar.close()

    def read_historical(
        self,
        vaults: list[VaultBase],
        start_block: int,
        end_block: int,
        step: int,
        reader_func: Callable = read_multicall_historical,
        saved_states: dict[VaultReaderState, dict] | None = None,
    ) -> Iterable[VaultHistoricalRead]:
        """Create an iterable that extracts vault records from RPC.

        Ordinary scans keep their first successful observation per vault even
        when its values are unchanged. Live scans additionally retain a real
        unchanged observation before its previous source row can become 14
        days old. Verified USD TVL enters monitoring at $1,500 and leaves
        below $1,000; unknown conversions are sampled but not certified.

        :param vaults:
            Vaults with known first-seen blocks on one chain.
        :param start_block:
            Inclusive first block to read.
        :param end_block:
            Exclusive end block.
        :param step:
            Approximate interval between sampled blocks.
        :param reader_func:
            Stateless or stateful multicall reader.
        :param saved_states:
            Optional adaptive reader states from a previous scan.
        :return:
            Real price observations retained by the sparse and freshness rules.
        """

        # Debug debug
        # vaults = [v for v in vaults if v.vault_address.lower() == "0x00c8a649c9837523ebb406ceb17a6378ab5c74cf"]

        # TODO: Clean up as an arg
        stateful = reader_func != read_multicall_historical

        logger.info("Preparing readers for %d vaults, stateful is %s", len(vaults), stateful)

        readers = self.prepare_readers(
            vaults,
            stateful=stateful,
            saved_states=saved_states,
        )

        # Expose for testing purposes
        self.readers = readers

        # Run warmup to detect broken calls before generating calls
        # TODO: Warmup system disabled for now - individual readers handle broken calls
        # if stateful:
        #     self._run_warmup(readers, end_block)

        # for address, reader in readers.items():
        #     state: VaultReaderState = reader.reader_state
        #     logger.debug(
        #         "Prepared reader for vault %s: state:\n%s",
        #         address,
        #         state.pformat() if state else "-",
        #     )

        static_readers = {address: reader for address, reader in readers.items() if not reader.uses_contextual_history}
        contextual_readers = {address: reader for address, reader in readers.items() if reader.uses_contextual_history}

        # Dealing with legacy shit here
        calls = {c: state for c, state in self.generate_vault_historical_calls(static_readers)}

        if not stateful:
            # Discard any state mapping
            calls = list(calls.keys())
        else:
            for reader in static_readers.values():
                assert reader.reader_state, f"Stateful reading: Reader did not set up state: {reader}"

        logger.info(
            f"Starting historical read loop, total calls {len(calls)} per block, {start_block:,} - {end_block:,} blocks, step is {step}",
        )

        if len(vaults) == 0:
            return

        chain_id = vaults[0].chain_id

        active_vault_set = set()
        last_block_at = last_block_num = None

        def _progress_bar_suffix():
            return {"Active vaults": len(active_vault_set), "Last block at": last_block_at.strftime("%Y-%m-%d") if last_block_at else "-", "Block": f"{last_block_num:,}" if last_block_num else "-"}

        chain_name = get_chain_name(chain_id)

        total_results = 0
        total_combined_results = 0

        # Cache the last result per vault to detect changes
        last_results: dict[HexAddress, VaultHistoricalRead] = {}
        retained_at = self.last_retained_at

        def freshness_due(reader: VaultHistoricalReader, current: VaultHistoricalRead) -> bool:
            """Check the early deadline using genuine source observation times.

            :param reader:
                Reader that produced the observation.
            :param current:
                Current row with source timestamp, price and denomination TVL.
            :return:
                Whether this unchanged row must be retained for freshness.
            """
            if not self.enforce_live_freshness or current.share_price is None or current.total_assets is None:
                return False
            address = reader.address.lower()
            self.latest_observed_at[address] = max(self.latest_observed_at.get(address, current.timestamp), current.timestamp)
            state = reader.reader_state
            rate = state.exchange_rate if isinstance(state, VaultReaderState) else VaultReaderState(reader.vault).exchange_rate
            if rate in (UNKNOWN_EXCHANGE_RATE, DENOMINATION_UNAVAILABLE_EXCHANGE_RATE):
                (self.denomination_unavailable_vaults if rate == DENOMINATION_UNAVAILABLE_EXCHANGE_RATE else self.unknown_conversion_vaults).add(address)
                retain_for_freshness = True
            else:
                current_usd = current.total_assets * rate
                self.latest_usd_tvl[address] = current_usd
                highest_usd = state.max_tvl if isinstance(state, VaultReaderState) else self.prior_max_assets.get(address, Decimal(0)) * rate
                retain_for_freshness = is_meaningful_usd_tvl(current_usd, highest_usd)
            if not retain_for_freshness:
                return False
            previous = retained_at.get(address)
            if previous is None:
                return True
            return current.timestamp - previous >= VAULT_PRICE_REFRESH_INTERVAL

        def is_unchanged(reader: VaultHistoricalReader, current: VaultHistoricalRead, previous: VaultHistoricalRead | None) -> bool:
            """Apply the product's existing sparse-value comparison.

            Share-price-equivalent readers ignore other changing fields;
            ordinary readers compare the full economic observation.

            :param reader:
                Reader that supplied the current observation.
            :param current:
                Current source observation.
            :param previous:
                Last observation retained during this scan, if any.
            :return:
                Whether the current observation is economically unchanged.
            """

            if reader.uses_share_price_equivalence:
                return current.is_share_price_almost_equal(previous, reader.share_price_change_threshold)
            return current.is_almost_equal(previous)

        skipped_results = 0
        error_count = 0

        static_results = (
            reader_func(
                chain_id=chain_id,
                web3factory=self.web3factory,
                calls=calls,
                start_block=start_block,
                end_block=end_block,
                step=step,
                display_progress=f"Reading {chain_name} historical with {self.max_workers} workers, blocks {start_block:,} - {end_block:,}",
                max_workers=self.max_workers,
                progress_suffix=_progress_bar_suffix,
                require_multicall_result=self.require_multicall_result,
                hypersync_client=self.hypersync_client,
                timestamp_cache_file=self.timestamp_cache_file,
                rpc_request_stats=self.rpc_request_stats,
            )
            if static_readers
            else ()
        )
        for combined_result in static_results:
            total_combined_results += 1

            active_vault_set.clear()
            vault_data: dict[HexAddress, list[EncodedCallResult]] = defaultdict(list)

            # Transform single multicall call results to calls batched by vault-results
            block_number = combined_result.block_number
            assert all(c.block_identifier == block_number for c in combined_result.results), "Sanity check we do not mis-assign block numbers"
            timestamp = combined_result.timestamp
            logger.debug(
                "Got %d call results for block %s",
                len(combined_result.results),
                block_number,
            )
            for call_result in combined_result.results:
                vault: HexAddress = call_result.call.extra_data["vault"]
                vault_data[vault].append(call_result)
                active_vault_set.add(vault)
                total_results += 1

            last_block_num = combined_result.block_number
            last_block_at = combined_result.timestamp
            for vault_address, results in vault_data.items():
                reader = readers[vault_address]
                state = reader.reader_state

                last_result: VaultHistoricalRead = last_results.get(vault_address)
                current_result: VaultHistoricalRead = reader.process_result(
                    block_number,
                    timestamp,
                    results,
                )

                # Stateless scans invoke every selected block. Keep their
                # exported frequency marker compatible with the historical
                # reader-state contract without allocating a dummy state.
                current_result.vault_poll_frequency = state.vault_poll_frequency if state else "first_read"

                if current_result.errors:
                    error_count += 1
                    if state:
                        state.rpc_error_count += 1
                        state.last_rpc_error = str(current_result.errors)

                due = freshness_due(reader, current_result)
                unchanged = is_unchanged(reader, current_result, last_result)
                if unchanged and not self.write_all_samples and not due:
                    # Only yield a new row if the vault state has changed,
                    # to not to unnecessary bloat the dataset
                    skipped_results += 1
                    if state:
                        state.write_filtered += 1
                else:
                    last_results[vault_address] = current_result
                    if current_result.share_price is not None:
                        retained_at[vault_address.lower()] = current_result.timestamp
                    if due and unchanged:
                        self.freshness_rows_written += 1
                    if state:
                        state.write_done += 1
                    yield current_result

        for reader in contextual_readers.values():
            reader_start_block = max(start_block, reader.first_block or start_block)
            if reader_start_block >= end_block:
                continue
            last_result = last_results.get(reader.address)
            for current_result in reader.fetch_contextual_historical_reads(reader_start_block, end_block, step):
                current_result.vault_poll_frequency = "contextual"
                if current_result.errors:
                    error_count += 1
                due = freshness_due(reader, current_result)
                unchanged = is_unchanged(reader, current_result, last_result)
                if unchanged and not due:
                    skipped_results += 1
                    continue
                last_result = current_result
                last_results[reader.address] = current_result
                if current_result.share_price is not None:
                    retained_at[reader.address.lower()] = current_result.timestamp
                if due and unchanged:
                    self.freshness_rows_written += 1
                total_results += 1
                yield current_result

        logger.info("Processed total %d results, total %d combined results, for %d vaults, skipped %d new rows, error count %d", total_results, total_combined_results, len(vaults), skipped_results, error_count)

    def export_reader_states(self) -> dict[VaultSpec, dict]:
        """Serialise reader progress without writing any pipeline file.

        The Parquet writer uses this snapshot for its publication journal and
        returned scan result. The all-chain scheduler owns the later atomic
        reader-pickle write; separating those responsibilities lets it recover
        progress after prices publish but before the legacy pickle is updated.

        :return:
            Legacy-shaped state dictionaries keyed by :py:class:`VaultSpec`.
        """

        return {r.vault.get_spec(): r.reader_state.save() for r in self.readers.values() if r.reader_state}

    #: Compatibility alias; exporting state does not itself persist a file.
    save_reader_state = export_reader_states


def _proper_fsync(fd: int) -> None:
    """Fsync with ``F_FULLFSYNC`` on macOS for full durability.

    Mirrors :py:func:`atomicwrites._proper_fsync`.
    """
    if fcntl is not None and hasattr(fcntl, "F_FULLFSYNC"):
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
    else:
        os.fsync(fd)


def scan_historical_prices_to_parquet(
    output_fname: Path,
    web3: Web3,
    web3factory: Web3Factory,
    vaults: list[VaultBase],
    token_cache: TokenDiskCache,
    start_block=None,
    end_block=None,
    step=None,
    chunk_size=1024,
    compression="zstd",
    max_workers=8,
    require_multicall_result=False,
    write_all_samples: bool = False,
    enforce_live_freshness: bool = False,
    expected_live_vaults: set[str] | None = None,
    frequency: Literal["1d", "1h"] = "1d",
    reader_states: dict[VaultSpec, dict] | None = None,
    hypersync_client=None,
    timestamp_cache_file=DEFAULT_TIMESTAMP_CACHE_FOLDER,
    vault_addresses: set[str] | None = None,
    rpc_request_stats: RPCRequestStats | None = None,
    reader_state_journal_path: Path | None = None,
) -> ParquetScanResult:
    """Scan vault prices and atomically update the shared raw Parquet file.

    Rows from multiple chains share one file. A stateful live scan retains a
    genuine unchanged eligible observation after seven days and audits the
    latest source timestamp against a 14-day limit. Historical backfills use
    sparse value-change sampling. Monad scans start at the provider's readable
    historical-state boundary and preserve earlier committed rows.

    :param reader_state_journal_path:
        Optional durable progress receipt for recovery after price publication
        but before the caller saves its legacy reader-state pickle.

    :param output_fname:
        Path to a destination Parquet file.

        If the file exists, replace rows in the requested block range for the
        selected vault addresses, or for the full chain when omitted.

    :param web3:
        Web3 connection

    :param web3factory:
        Factory for worker JSON-RPC connections.

    :param vaults:
        Vaults of which historical price we scan.

        All vaults must have their ``first_seen_at_block`` attribute set to
        increase scan performance.

    :param start_block:
        First block to scan.

        Leave empty to autodetect. On Monad this is only a lower bound: the
        scan starts at the oldest block whose state the configured provider can
        read, because Monad has no arbitrary-depth historical state.

    :param end_block:
        Exclusive end block to scan.

        Leave empty to autodetect.

    :param step:
        Approximate number of blocks between observations. When omitted,
        derive it from ``frequency`` and the chain's block time.

        Sampling is block-based, not timestamp-based. Equal timestamps from
        separate blocks are valid input and output because the scanner only
        requires one-second time accuracy, not synthetic higher-resolution
        timestamps.

    :param chunk_size:
        How many rows to write to the Parquet file in one buffer.

    :param compression:
        Parquet compression codec.

    :param max_workers:
        Maximum concurrent multicall workers.

    :param token_cache:
        Shared token metadata cache used by the reader.

    :param require_multicall_result:
        Require successful multicall responses.

    :param hypersync_client:
        Speed up the discovery of timestamps

    :param timestamp_cache_file:
        Cache for historical block timestamps.

    :param frequency:
        One-hour or one-day base sampling grid.

    :param reader_states:
        Persisted adaptive read states for a live scan, or ``None`` for a
        historical backfill.

    :param vault_addresses:
        If set, only delete and rewrite parquet rows for these vault addresses.

        Addresses are normalised to lowercase. An empty selection or one
        containing blacklisted contracts or addresses without a supplied vault
        raises :py:exc:`ValueError` before writing: excluded or missing readers
        cannot supply replacement observations. When ``None``, chain rows at
        or after the chosen start block and before the end block are replaced
        (default behaviour).

    :param write_all_samples:
        Write every sampled block even when a vault's values are unchanged.
        Dedicated issuer-NAV feeds use this to retain their daily freshness
        timestamp rather than collapsing an unchanged price history.

    :param enforce_live_freshness:
        Retain a genuine unchanged EVM observation once its preceding source
        row is seven days old. Routine live scans enable this.

    :param expected_live_vaults:
        Vaults already verified as meaningful by a current-state probe. If
        their historical reader yields no sample, report an overdue result.

    :param rpc_request_stats:
        Optional phase accumulator for physical JSON-RPC request accounting.

    :return:
        Scan report.
    """

    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    stateful = reader_states is not None

    assert isinstance(output_fname, Path)
    if start_block is not None:
        assert type(start_block) == int

    if end_block is not None:
        assert type(end_block) == int

    chain_id = web3.eth.chain_id
    if vault_addresses is not None:
        if not vault_addresses:
            raise ValueError("vault_addresses cannot be empty because that would broaden deletion to the whole chain")
        vault_addresses = {address.lower() for address in vault_addresses}
        # Direct callers such as scan-prices.py bypass the all-chain selector.
        # Reject an excluded repair target before dropping its reader, otherwise
        # the bounded replacement below could delete history without new rows.
        blacklisted_addresses = vault_addresses & BROKEN_VAULT_CONTRACTS
        if blacklisted_addresses:
            raise ValueError(f"Selected vaults are blacklisted; refusing bounded deletion: {sorted(blacklisted_addresses)}")
        # scan-prices.py can drop candidates during its activity/adapter filter
        # while retaining their requested addresses. Validate that deletion has
        # a corresponding supplied vault, independently of that caller's policy.
        missing_addresses = vault_addresses - {vault.vault_address.lower() for vault in vaults}
        if missing_addresses:
            raise ValueError(f"Selected vaults have no supplied reader; refusing bounded deletion: {sorted(missing_addresses)}")

    logger.info(
        "Vault scan on %s: %s - %s, stateful is %s",
        chain_id,
        start_block,
        end_block,
        stateful,
    )

    cleaned_vaults = []
    for v in vaults:
        if v.vault_address.lower() in BROKEN_VAULT_CONTRACTS:
            logger.warning(f"Skipping blacklisted vault {v.vault_address} on chain {v.chain_id}")
            continue
        cleaned_vaults.append(v)

    vaults = cleaned_vaults

    assert all(v.first_seen_at_block for v in vaults), "You need to set vault.first_seen_at_block hint in order to run this reader"
    assert all(v.chain_id == chain_id for v in vaults), "All vaults must be on the same chain"

    if vaults:
        first_detect_block = min(v.first_seen_at_block for v in vaults)
    else:
        first_detect_block = 0

    logger.info(f"First vault lead detection at block {first_detect_block:,} on chain {chain_id} ({get_chain_name(chain_id)})")
    if start_block is None:
        if stateful:
            # A vault requiring an older bootstrap must use its own
            # address-scoped scan. Restored states cannot safely replay time
            # before their individual ``last_call_at`` values.
            last_scanned_block = max(((state["last_block"] or 0) for spec, state in reader_states.items() if spec.chain_id == chain_id), default=0)
            # The saved block was already processed. Replaying it with the
            # restored last_call_at can skip its read while the overlapping
            # Parquet replacement deletes its retained price row.
            start_block = last_scanned_block + 1 if last_scanned_block else first_detect_block
            logger.info("Chain %s: determined start block %s from %s vault read states", chain_id, f"{start_block:,}", len(reader_states))
        else:
            # Clean start, find the first block of any vault on this chain.
            # Detected during probing.
            logger.info("No previous state, using first vault detection block as start block")
            start_block = first_detect_block

    if end_block is None:
        end_block = get_almost_latest_block_number(web3)

    if chain_id == MONAD_CHAIN_ID:
        start_block = fetch_monad_historical_state_start_block(
            web3,
            start_block=start_block,
            end_block=end_block,
        )

    reader = VaultHistoricalReadMulticaller(
        web3factory,
        supported_quote_tokens=None,
        max_workers=max_workers,
        token_cache=token_cache,
        require_multicall_result=require_multicall_result,
        write_all_samples=write_all_samples,
        enforce_live_freshness=enforce_live_freshness,
        hypersync_client=hypersync_client,
        timestamp_cache_file=timestamp_cache_file,
        rpc_request_stats=rpc_request_stats,
    )

    reader_func = read_multicall_historical_stateful if stateful else read_multicall_historical
    match frequency:
        case "1d":
            step_duration = datetime.timedelta(hours=24)
        case "1h":
            # TODO: This is a dynamic frequency.
            step_duration = datetime.timedelta(hours=1)
        case _:
            raise ValueError(f"Unsupported frequency: {frequency}")

    # Note this is an approx,
    # manual tuning will be needed
    if step is None:
        block_time = EVM_BLOCK_TIMES.get(chain_id)
        assert block_time is not None, f"Block time not configured for chain: {chain_id}"
        step = step_duration // datetime.timedelta(seconds=block_time)
    else:
        block_time = None

    logger.info(
        "Reading %d vaults on chain %d, start block %d, end block %d, step %d blocks, step duration %s",
        len(vaults),
        chain_id,
        start_block,
        end_block,
        step,
        step_duration,
    )

    # Create iterator that will drop in vault historical read entries block by block
    entries_iter = reader.read_historical(
        vaults=cleaned_vaults,
        start_block=start_block,
        end_block=end_block,
        step=step,
        reader_func=reader_func,
        saved_states=reader_states,
    )

    rows_written_by_vault: dict[str, int] = defaultdict(int)
    price_rows_written_by_vault: dict[str, int] = defaultdict(int)

    # Convert VaultHistoricalRead objects to exportable dicts for Parquet
    def converter(entries_iter: Iterable[VaultHistoricalRead]) -> Iterable[dict]:
        for entry in entries_iter:
            vault_address = entry.vault.vault_address.lower()
            rows_written_by_vault[vault_address] += 1
            if entry.share_price is not None:
                price_rows_written_by_vault[vault_address] += 1
            yield entry.export()

    converted_iter = converter(entries_iter)

    # Always use the current canonical schema so new columns are not silently dropped
    canonical_schema = VaultHistoricalRead.to_pyarrow_schema()

    # Resolve the source horizon before replacing the file so an RPC failure
    # cannot leave a successful write with no freshness audit result.
    freshness_horizon = None
    if enforce_live_freshness:
        horizon_block = web3.eth.get_block(max(0, end_block - 1))
        freshness_horizon = datetime.datetime.fromtimestamp(horizon_block["timestamp"], tz=datetime.UTC).replace(tzinfo=None)

    if output_fname.exists():
        logger.info("Reading existing Parquet file %s", output_fname)
        existing_table = pq.read_table(output_fname)
        existing_table = VaultHistoricalRead.migrate_parquet_schema(existing_table)
    else:
        logger.info("Creating Parquet from the scratch %s", output_fname)
        existing_table = None

    source_table = existing_table

    if enforce_live_freshness and existing_table is not None:
        selected_addresses = pa.array([vault.address.lower() for vault in vaults])
        previous_mask = pc.and_(
            pc.equal(existing_table["chain"], chain_id),
            pc.less(existing_table["block_number"], start_block),
        )
        previous_mask = pc.and_(previous_mask, pc.is_in(existing_table["address"], value_set=selected_addresses))
        previous_mask = pc.and_(previous_mask, pc.is_finite(existing_table["share_price"]))
        prior = existing_table.select(["address", "timestamp"]).filter(previous_mask)
        if len(prior):
            grouped = prior.group_by("address").aggregate([("timestamp", "max")])
            reader.last_retained_at = dict(zip(grouped["address"].to_pylist(), grouped["timestamp_max"].to_pylist()))
        assets_mask = pc.and_(pc.equal(existing_table["chain"], chain_id), pc.is_in(existing_table["address"], value_set=selected_addresses))
        assets_mask = pc.and_(assets_mask, pc.is_finite(existing_table["total_assets"]))
        assets = existing_table.select(["address", "total_assets"]).filter(assets_mask)
        if len(assets):
            grouped = assets.group_by("address").aggregate([("total_assets", "max")])
            reader.prior_max_assets = {address: Decimal(str(value)) for address, value in zip(grouped["address"].to_pylist(), grouped["total_assets_max"].to_pylist())}

    if existing_table is not None:
        logger.info(
            "Detected existing file %s with %d rows",
            output_fname,
            len(existing_table),
        )
        # Clear existing entries for this chain
        # When vault_addresses is set, only delete rows for those specific vaults
        # to preserve other vaults' data
        mask = pc.and_(
            pc.equal(existing_table["chain"], chain_id),
            pc.greater_equal(existing_table["block_number"], start_block),
        )
        mask = pc.and_(mask, pc.less(existing_table["block_number"], end_block))
        if vault_addresses is not None:
            address_mask = pc.is_in(existing_table["address"], pa.array(list(vault_addresses)))
            mask = pc.and_(mask, address_mask)
        all_row_count = len(existing_table)
        rows_deleted = pc.sum(mask).as_py() or 0
        existing_table = existing_table.filter(pc.invert(mask))
        existing_row_count = existing_table.num_rows
        logger.info(
            "Removed existing %d rows out of %d rows for chain %d from the vault time-series data, existing table has %d rows",
            rows_deleted,
            all_row_count,
            chain_id,
            existing_row_count,
        )
        existing = True
    else:
        logger.info("No existing table, no removed rows")
        existing_table = None
        rows_deleted = 0
        existing = False
        existing_row_count = 0

    # Build a unified writer schema: canonical columns + any extra columns
    # from native protocol merges (e.g. account_pnl, leader_fraction).
    # This preserves native protocol data when the EVM scanner rewrites the file.
    if existing_table is not None:
        canonical_names = set(canonical_schema.names)
        extra_fields = [f for f in existing_table.schema if f.name not in canonical_names]
        writer_schema = canonical_schema
        for field in extra_fields:
            writer_schema = writer_schema.append(field)
    else:
        writer_schema = canonical_schema

    # Store the current scanner build on the file schema. This is done after
    # combining native-only fields so every rewrite refreshes the provenance.
    writer_schema = stamp_parquet_schema_metadata(writer_schema)

    if existing_table is not None:
        existing_table = existing_table.replace_schema_metadata(writer_schema.metadata)

    # Perform atomic update of the prices Parquet file.
    #
    # The temp file lives next to the final Parquet file so ``os.replace()`` is
    # atomic. Because ``delete=False`` is required for PyArrow and replacement,
    # we must explicitly remove the file on every failure path.
    writer = None
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=output_fname.parent,
        suffix=".parquet",
        delete=False,
    ) as tmp:
        temp_fname = tmp.name

    try:
        # Initialize ParquetWriter with the unified schema
        writer = pq.ParquetWriter(temp_fname, writer_schema, compression=compression)
        if existing_table is not None:
            writer.write_table(existing_table)

        rows_written = 0

        logger.info(
            "Starting vault historical price export to %s, we have %d vaults, range %d - %d, block step is %d, block time is %s seconds, token cache is %s",
            output_fname,
            len(vaults),
            start_block,
            end_block,
            step,
            block_time,
            token_cache.filename,
        )

        assert end_block >= start_block, f"End block {end_block} must be greater than or equal to start block {start_block}"

        chunks_done = 0
        written_at = native_datetime_utc_now()
        for chunk in chunked(converted_iter, chunk_size):
            logger.debug(f"Processing Parquet chunk {chunks_done:,}, rows written so far {rows_written:,}")
            table = pa.Table.from_pylist(chunk, schema=canonical_schema)
            # Stamp all rows in this batch with the same write timestamp
            table = table.set_column(
                table.schema.get_field_index("written_at"),
                "written_at",
                pa.array([written_at] * len(table), type=pa.timestamp("ms")),
            )
            # Pad with null columns for any extra native protocol fields
            # so new EVM rows match the unified writer schema
            for field in writer_schema:
                if field.name not in table.schema.names:
                    table = table.append_column(field, pa.nulls(len(table), type=field.type))
            writer.write_table(table)
            rows_written += len(chunk)
            chunks_done += 1

        # Close the writer to finalise the file, then flush to disk
        # and atomically replace the target.  We also sync the parent
        # directory so the new filename is durable after power loss.
        writer.close()
        writer = None
        fd = os.open(temp_fname, os.O_RDONLY)
        try:
            _proper_fsync(fd)
        finally:
            os.close(fd)
        # Verify temp file before replacing the target so the
        # previous good file is preserved if verification fails.
        verify_parquet_file(
            temp_fname,
            expected_rows=existing_row_count + rows_written,
            expected_schema=writer_schema,
        )
        # Audit verified observations before publication while keeping diagnostics
        # separate from the price data's validity. An audit implementation error
        # is returned explicitly to the scheduler, but need not discard a valid
        # completed scan or force its expensive historical reads to run again.
        overdue_vaults: dict[str, str] = {}
        freshness_eligible_vaults = 0
        audit_error = None
        try:
            if enforce_live_freshness:
                assert freshness_horizon is not None
                horizon = freshness_horizon
                expected_addresses = {address.lower() for address in (expected_live_vaults or ())}
                contextual_addresses = {address.lower() for address, historical_reader in reader.readers.items() if historical_reader.uses_contextual_history}
                contextual_history = None
                if contextual_addresses and source_table is not None:
                    contextual_mask = pc.and_(
                        pc.equal(source_table["chain"], chain_id),
                        pc.is_in(source_table["address"], value_set=pa.array(sorted(contextual_addresses))),
                    )
                    contextual_mask = pc.and_(contextual_mask, pc.is_finite(source_table["share_price"]))
                    contextual_mask = pc.and_(contextual_mask, pc.is_finite(source_table["total_assets"]))
                    contextual_history = source_table.select(["address", "block_number", "timestamp", "total_assets"]).filter(contextual_mask).to_pandas()
                    if not contextual_history.empty:
                        contextual_history = contextual_history.sort_values(["address", "timestamp", "block_number"], kind="stable")
                for address, historical_reader in reader.readers.items():
                    state = historical_reader.reader_state
                    if isinstance(state, VaultReaderState) and state.exchange_rate == DENOMINATION_UNAVAILABLE_EXCHANGE_RATE:
                        reader.denomination_unavailable_vaults.add(address.lower())
                    qualified = address.lower() in expected_addresses or (isinstance(state, VaultReaderState) and state.freshness_qualified)
                    if not qualified and historical_reader.uses_contextual_history:
                        rate = state.exchange_rate if isinstance(state, VaultReaderState) else VaultReaderState(historical_reader.vault).exchange_rate
                        if rate in (UNKNOWN_EXCHANGE_RATE, DENOMINATION_UNAVAILABLE_EXCHANGE_RATE):
                            (reader.denomination_unavailable_vaults if rate == DENOMINATION_UNAVAILABLE_EXCHANGE_RATE else reader.unknown_conversion_vaults).add(address.lower())
                        else:
                            prior_assets = contextual_history.loc[contextual_history["address"] == address.lower(), "total_assets"] if contextual_history is not None else None
                            highest_usd = reader.prior_max_assets.get(address.lower(), Decimal(0)) * rate
                            latest_usd = reader.latest_usd_tvl.get(address.lower())
                            if latest_usd is None and prior_assets is not None and len(prior_assets):
                                # Retain last-known eligibility when the source has no new observation.
                                latest_usd = Decimal(str(prior_assets.iloc[-1])) * rate
                            qualified = is_meaningful_usd_tvl(latest_usd, highest_usd)
                    if not qualified:
                        continue
                    freshness_eligible_vaults += 1
                    retained = reader.last_retained_at.get(address.lower())
                    if retained is None or horizon - retained > MAX_VAULT_PRICE_ROW_AGE:
                        reason = "no_valid_source_observation" if address.lower() not in reader.latest_observed_at else "source_observation_not_retained"
                        overdue_vaults[address.lower()] = "denomination_unavailable" if address.lower() in reader.denomination_unavailable_vaults else reason
                missing_readers = expected_addresses - {address.lower() for address in reader.readers}
                freshness_eligible_vaults += len(missing_readers)
                for address in missing_readers:
                    retained = reader.last_retained_at.get(address)
                    if retained is None or horizon - retained > MAX_VAULT_PRICE_ROW_AGE:
                        overdue_vaults[address] = "reader_unavailable"
                    else:
                        logger.warning("Vault %s on chain %d has no historical reader in this scan; last price row is %s", address, chain_id, retained)
                logger.info(
                    "Vault price freshness on chain %d: eligible=%d, heartbeat rows=%d, overdue=%d, unknown USD conversion=%d",
                    chain_id,
                    freshness_eligible_vaults,
                    reader.freshness_rows_written,
                    len(overdue_vaults),
                    len(reader.unknown_conversion_vaults),
                )
                for address, reason in overdue_vaults.items():
                    logger.warning("Vault %s on chain %d has no source price row within 14 days of scan horizon %s: %s", address, chain_id, horizon, reason)

        except (AttributeError, ValueError, TypeError, ArithmeticError) as error:
            audit_error = f"{type(error).__name__}: {error}"
            logger.error("Freshness audit failed on chain %d: %s", chain_id, audit_error, exc_info=True)
        if stateful:
            # Serialise progress before the price rename so a serialisation
            # failure leaves the old output intact. The journal and returned
            # result use the same legacy state snapshot for normal persistence
            # and crash recovery; neither advances cursors beyond published data.
            new_states = reader.export_reader_states()
            logger.info("Total %d updates reader states available", len(new_states))
            if any(not historical_reader.uses_contextual_history for historical_reader in reader.readers.values()):
                assert len(new_states) > 0, f"Reader states are empty, this is a bug, chain_id: {chain_id}, vaults: {vaults}"
            reader_states = reader_states or {}
            reader_states.update(new_states)
        else:
            logger.info("Not a stateful scan, do not update states")

        if stateful and reader_state_journal_path is not None:
            # Atomic replacement cannot publish prices and the reader pickle
            # together. Prepare a receipt bound to this verified temporary
            # inode first; after rename the scheduler can recover it on restart.
            # A crash before rename leaves a non-matching receipt, not progress
            # that would incorrectly skip still-unpublished historical values.
            save_reader_publication_journal(reader_state_journal_path, Path(temp_fname), output_fname, reader_states)
        os.replace(temp_fname, output_fname)
        dir_fd = os.open(str(output_fname.parent), os.O_RDONLY)
        try:
            _proper_fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        if writer is not None:
            try:
                writer.close()
            except Exception as e:
                logger.warning("Could not close failed Parquet writer for %s: %s", temp_fname, e)
        if os.path.exists(temp_fname):
            os.unlink(temp_fname)
        raise

    return ParquetScanResult(
        rows_written=rows_written,
        rows_deleted=rows_deleted,
        output_fname=output_fname,
        chain_id=chain_id,
        file_size=output_fname.stat().st_size,
        existing=existing,
        existing_row_count=existing_row_count,
        chunks_done=chunks_done,
        reader_states=reader_states,
        start_block=start_block,
        end_block=end_block,
        rows_written_by_vault=dict(rows_written_by_vault),
        price_rows_written_by_vault=dict(price_rows_written_by_vault),
        freshness_rows_written=reader.freshness_rows_written,
        freshness_eligible_vaults=freshness_eligible_vaults,
        overdue_vaults=overdue_vaults,
        unknown_conversion_vaults=sorted(reader.unknown_conversion_vaults),
        denomination_unavailable_vaults=sorted(reader.denomination_unavailable_vaults),
        audit_error=audit_error,
        historical_state_window=getattr(web3, "_vault_historical_state_window", None) if chain_id == 143 else None,
    )
