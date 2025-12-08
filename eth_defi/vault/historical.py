"""Read historical state of vaults.

- Use multicall to get data points for multiple vaults once
- Include
    - Share price
    - TVL
    - Fees

See :py:class:`VaultHistoricalReadMulticaller` for usage.
"""

import logging
import os
import tempfile
from collections import defaultdict, Counter
import datetime
from pathlib import Path

from typing import Iterable, TypedDict, Callable, Literal

from eth_typing import HexAddress
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi import hypersync
from eth_defi.chain import EVM_BLOCK_TIMES, get_chain_name
from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.multicall_batcher import EncodedCall, read_multicall_historical, EncodedCallResult, read_multicall_historical_stateful, BatchCallState
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.token import TokenDetails, TokenDiskCache, fetch_erc20_details
from eth_defi.utils import chunked
from eth_defi.vault.base import VaultBase, VaultHistoricalReader, VaultHistoricalRead, VaultSpec
from eth_defi.vault.risk import BROKEN_VAULT_CONTRACTS

logger = logging.getLogger(__name__)


#: List of contracts we cannot scan.
#: These will bomb out with out of gas.
#: See Mantle issues.
DEFAULT_BLACK_LIST = [
    # TODO
]


class _DummyObject:
    def __getattr__(self, name):
        return self

    def __call__(self, *args, **kwargs):
        return self

    def __iadd__(self, other):
        return 0


class ParquetScanResult(TypedDict):
    """Result of generating historical prices Parquet file."""

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

    reader_states: dict[VaultSpec, dict] | None


def pformat_scan_result(self) -> str:
    """Format the result as a string."""
    return f"ParquetScanResult(chain_id={self['chain_id']}, \nstart_block={self['start_block']:,}, \nend_block={self['end_block']:,}, \nrows_written={self['rows_written']:,}, \nrows_deleted={self['rows_deleted']:,}, \nexisting_row_count={self['existing_row_count']:,}, \nreader_state_count={len(self['reader_states'])}, \noutput_fname={self['output_fname']}, \nfile_size={self['file_size']:,} bytes, \nchunks_done={self['chunks_done']:,})"


class VaultReadNotSupported(Exception):
    """Vault cannot be read due to misconfiguration somewhere."""


class VaultHistoricalReadMulticaller:
    """Read historical data from multiple vaults using multicall and archive node polling."""

    def __init__(
        self,
        web3factory: Web3Factory,
        supported_quote_tokens=set[TokenDetails] | None,
        max_workers=8,
        token_cache=None,
        require_multicall_result=False,
        hypersync_client: "hypersync.HypersyncClient | None" = None,
        timestamp_cache_file: Path = DEFAULT_TIMESTAMP_CACHE_FOLDER,
    ):
        """
        :param supported_quote_tokens:
            Allows us to validate vaults against list of supported tokens
        """

        if supported_quote_tokens is not None:
            for a in supported_quote_tokens:
                assert isinstance(a, TokenDetails)

        self.supported_quote_tokens = supported_quote_tokens
        self.web3factory = web3factory
        self.max_workers = max_workers
        self.hypersync_client = hypersync_client
        self.timestamp_cache_file = timestamp_cache_file

        if token_cache is None:
            token_cache = TokenDiskCache()

        self.token_cache = token_cache
        self.require_multicall_result = require_multicall_result

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
        """Run in subprocess"""
        return vault.get_historical_reader(stateful=stateful)

    def _prepare_denomination_token(self, reader: "eth_defi.erc_4626.vault.ERC4626HistoricalReader") -> HexAddress:
        """Run in subprocess"""

        state = reader.reader_state
        if state:
            if state.denomination_token_address is not None:
                return state.denomination_token_address

        address = reader.vault.fetch_denomination_token_address()

        # Save for the next run as this is slow to fetch
        if state:
            state.denomination_token_address = address

        return address

    def _prepare_share_token(self, reader: "eth_defi.erc_4626.vault.ERC4626HistoricalReader") -> HexAddress:
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

    def _prepare_multicalls(self, reader: VaultHistoricalReader, stateful=False) -> Iterable[tuple[EncodedCall, BatchCallState]]:
        """Run in subprocess"""
        for call in reader.construct_multicalls():
            yield call, reader.reader_state

    def prepare_readers(
        self,
        vaults: list[VaultBase],
        stateful=False,
        saved_states: dict[VaultReaderState, dict] | None = None,
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
        """Create an iterable that extracts vault record from RPC.

        :param start_block:
            The first block to read from.

            Set to None to get from the saved state what we have not yet read.

        :param reader_func:
            Either ``read_multicall_historical`` or ``read_multicall_historical_stateful``

        :return:
            Unordered results
        """

        # Debug debug
        # vaults = [v for v in vaults if v.vault_address.lower() == "0x00c8a649c9837523ebb406ceb17a6378ab5c74cf"]

        # TODO: Clean up as an arg
        stateful = reader_func != read_multicall_historical

        logger.info(f"Preparing readers for %d vaults, stateful is %s", len(vaults), stateful)

        readers = self.prepare_readers(
            vaults,
            stateful=stateful,
            saved_states=saved_states,
        )

        # Expose for testing purposes
        self.readers = readers

        # for address, reader in readers.items():
        #     state: VaultReaderState = reader.reader_state
        #     logger.debug(
        #         "Prepared reader for vault %s: state:\n%s",
        #         address,
        #         state.pformat() if state else "-",
        #     )

        # Dealing with legacy shit here
        calls = {c: state for c, state in self.generate_vault_historical_calls(readers)}

        if not stateful:
            # Discard any state mapping
            calls = list(calls.keys())
        else:
            for reader in readers.values():
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

        skipped_results = 0
        error_count = 0

        for combined_result in reader_func(
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
        ):
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
            dummy_counter = _DummyObject()

            for vault_address, results in vault_data.items():
                reader = readers[vault_address]
                state = reader.reader_state or dummy_counter

                last_result: VaultHistoricalRead = last_results.get(vault_address)
                current_result: VaultHistoricalRead = reader.process_result(
                    block_number,
                    timestamp,
                    results,
                )

                current_result.vault_poll_frequency = state.vault_poll_frequency

                if current_result.errors:
                    error_count += 1
                    state.rpc_error_count += 1
                    state.last_rpc_error = str(current_result.errors)

                if current_result.is_almost_equal(last_result):
                    # Only yield a new row if the vault state has changed,
                    # to not to unnecessary bloat the dataset
                    skipped_results += 1
                    state.write_filtered += 1
                else:
                    last_results[vault_address] = current_result
                    state.write_done += 1
                    yield current_result

        logger.info("Processed total %d results, total %d combined results, for %d vaults, skipped %d new rows, error count %d", total_results, total_combined_results, len(vaults), skipped_results, error_count)

    def save_reader_state(self) -> dict[VaultSpec, dict]:
        """Save the state of all readers.

        :return:
            Dictionary keyed by the vault spce
        """

        # TODO: Fix class inheritance, etc.
        return {r.vault.get_spec(): r.reader_state.save() for r in self.readers.values() if r.reader_state}


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
    frequency: Literal["1d", "1h"] = "1d",
    reader_states: dict[VaultSpec, dict] | None = None,
    hypersync_client=None,
    timestamp_cache_file=DEFAULT_TIMESTAMP_CACHE_FOLDER,
) -> ParquetScanResult:
    """Scan all historical vault share prices of vaults and save them in to Parquet file.

    - Write historical prices to a Parquet file
    - Multiprocess-boosted
    - The same Parquet file can contain data from multiple chains

    :param output_fname:
        Path to a destination Parquet file.

        If the file exists, all entries for the current chain are deleted and rewritten.

    :param web3:
        Web3 connection

    :param web3factory:
        Creation of connections in subprocess

    :param vaults:
        Vaults of which historical price we scan.

        All vaults must have their ``first_seen_at_block`` attribute set to
        increase scan performance.

    :param start_block:
        First block to scan.

        Leave empty to autodetect

    :param end_block:
        Last block to scan.

        Leave empty to autodetect.

    :param step_duration:
        What is the historical step size (1 day).

        Will be automatically attmpeted to map  to a block time.

    :param step:
        What is the step is in number of blocks.

    :param chunk_size:
        How many rows to write to the Parquet file in one buffer.

    :param max_workers:
        Number of subprocesses to use for multicall

    :param hypersync_client:
        Speed up the discovery of timestamps

    :return:
        Scan report.
    """

    import pyarrow as pa
    import pyarrow.parquet as pq
    import pyarrow.compute as pc

    stateful = reader_states is not None

    assert isinstance(output_fname, Path)
    if start_block is not None:
        assert type(start_block) == int

    if end_block is not None:
        assert type(end_block) == int

    chain_id = web3.eth.chain_id

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

    assert all(v.first_seen_at_block for v in vaults), f"You need to set vault.first_seen_at_block hint in order to run this reader"
    assert all(v.chain_id == chain_id for v in vaults), f"All vaults must be on the same chain"

    if vaults:
        first_detect_block = min(v.first_seen_at_block for v in vaults)
    else:
        first_detect_block = 0

    logger.info(f"First vault lead detection at block {first_detect_block:,} on chain {chain_id} ({get_chain_name(chain_id)})")
    if start_block is None:
        if stateful:
            # If we have reader states, use the earliest block from there
            start_block = max(((state["last_block"] or 0) for spec, state in reader_states.items() if spec.chain_id == chain_id), default=first_detect_block)
            logger.info(f"Chain {chain_id}: determined start block to be {start_block:,} from {len(reader_states)} vault read states")
        else:
            # Clean start, find the first block of any vault on this chain.
            # Detected during probing.
            logger.info("No previous state, using first vault detection block as start block")
            start_block = first_detect_block

    if end_block is None:
        end_block = get_almost_latest_block_number(web3)

    reader = VaultHistoricalReadMulticaller(
        web3factory,
        supported_quote_tokens=None,
        max_workers=max_workers,
        token_cache=token_cache,
        require_multicall_result=require_multicall_result,
        hypersync_client=hypersync_client,
        timestamp_cache_file=timestamp_cache_file,
    )

    # TODO: Do not use - all is dynamic frequency with stateful reading now
    reader_func = read_multicall_historical_stateful
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

    # Convert VaultHistoricalRead objects to exportable dicts for Parquet
    def converter(entries_iter: Iterable[VaultHistoricalRead]) -> Iterable[dict]:
        for entry in entries_iter:
            yield entry.export()

    converted_iter = converter(entries_iter)

    if output_fname.exists():
        try:
            logger.info("Reading existing Parquet file %s", output_fname)
            existing_table = pq.read_table(output_fname)
            schema = existing_table.schema
        except pa.lib.ArrowInvalid as e:
            logger.warning(
                "Parquet file %s, write damaged %s, resetting",
                output_fname,
                str(e),
            )
            existing_table = None
    else:
        logger.info("Creating Parquet from the scratch %s", output_fname)
        existing_table = None
        schema = VaultHistoricalRead.to_pyarrow_schema()

    if existing_table is not None:
        logger.info(
            "Detected existing file %s with %d rows",
            output_fname,
            len(existing_table),
        )
        # Clear existing entries for this chain
        mask = pc.and_(
            pc.equal(existing_table["chain"], chain_id),
            pc.greater_equal(existing_table["block_number"], start_block),
        )
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

    # Perform atomic update of the prices Parquet file
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=output_fname.parent,
        suffix=".parquet",
        delete=False,
    ) as tmp:
        # Initialize ParquetWriter with the schema
        temp_fname = tmp.name
        writer = pq.ParquetWriter(temp_fname, schema, compression=compression)

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
        for chunk in chunked(converted_iter, chunk_size):
            logger.debug(f"Processing Parquet chunk {chunks_done:,}, rows written so far {rows_written:,}")
            table = pa.Table.from_pylist(chunk, schema=schema)
            writer.write_table(table)
            rows_written += len(chunk)
            chunks_done += 1

        # Close the writer to finalize the file
        writer.close()
        os.replace(temp_fname, output_fname)

    size = output_fname.stat().st_size

    logger.info(
        f"Exported {rows_written} vault {frequency} price rows, file size is now {size:,} bytes",
    )

    if stateful:
        # Merge new reader states
        new_states = reader.save_reader_state()
        logger.info("Total %d updates reader states available", len(new_states))
        if len(vaults) > 0:
            assert len(new_states) > 0, f"Reader states are empty, this is a bug, chain_id: {chain_id}, vaults: {vaults}"
        reader_states = reader_states or {}
        reader_states.update(new_states)
    else:
        logger.info("Not a stateful scan, do not update states")

    return ParquetScanResult(
        rows_written=rows_written,
        rows_deleted=rows_deleted,
        output_fname=output_fname,
        chain_id=chain_id,
        file_size=size,
        existing=existing,
        existing_row_count=existing_row_count,
        chunks_done=chunks_done,
        reader_states=reader_states,
        start_block=start_block,
        end_block=end_block,
    )
