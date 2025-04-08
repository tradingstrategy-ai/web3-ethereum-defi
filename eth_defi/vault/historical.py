"""Read historical state of vaults.

- Use multicall to get data points for multiple vaults once
- Include
    - Share price
    - TVL
    - Fees

See :py:class:`VaultHistoricalReadMulticaller` for usage.
"""
import logging
from collections import defaultdict
import datetime
from pathlib import Path

from typing import Iterable, TypedDict

import ipdb
from eth_typing import HexAddress
from joblib import Parallel, delayed
from web3 import Web3

from eth_defi.chain import EVM_BLOCK_TIMES
from eth_defi.event_reader.multicall_batcher import EncodedCall, read_multicall_historical, EncodedCallResult
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.token import TokenDetails, TokenDiskCache
from eth_defi.utils import chunked
from eth_defi.vault.base import VaultBase, VaultHistoricalReader, VaultHistoricalRead


logger = logging.getLogger(__name__)


class ParquetScanResult(TypedDict):
    """Result of generating historical prices Parquet file."""
    existing: bool
    chain_id: int
    rows_written: int
    rows_deleted: int
    existing_row_count: int
    output_fname: Path
    file_size: int


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

        if token_cache is None:
            token_cache = TokenDiskCache()

        self.token_cache = token_cache

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

    def _prepare_reader(self, vault: VaultBase):
        return vault.get_historical_reader()

    def _prepare_denomination_token(self, vault: VaultBase) -> HexAddress:
        return vault.fetch_denomination_token_address()

    def prepare_readers(
        self,
        vaults: list[VaultBase],
    ) -> dict[HexAddress, VaultHistoricalReader]:
        """Create readrs for vaults."""
        logger.info(
        "Preparing readers for %d vaults, using %d threads",
    len(vaults),
            self.max_workers,
        )

        assert len(vaults) > 0

        chain_id = vaults[0].chain_id

        # Warm up token disk cache for denomination tokens.
        # We need to load this up before because we need to calculate share price for amount 1 in denomination token (USDC)
        token_addresses = Parallel(n_jobs=self.max_workers, backend='threading')(delayed(self._prepare_denomination_token)(v) for v in vaults)
        token_addresses = [a for a in token_addresses if a is not None]
        self.token_cache.load_token_details_with_multicall(
            chain_id=chain_id,
            web3factory=self.web3factory,
            addresses=token_addresses,
        )

        # Each vault reader creation causes ~5 RPC call as it initialises the token information.
        # We do parallel to cut down the time here.
        results = Parallel(n_jobs=self.max_workers, backend='threading')(delayed(self._prepare_reader)(v) for v in vaults)
        readers = {r.address: r for r in results}
        return readers

    def generate_vault_historical_calls(
        self,
        readers: dict[HexAddress, VaultHistoricalReader],
    ) -> Iterable[EncodedCall]:
        """Generate multicalls for each vault to read its state at any block."""
        for reader in readers.values():
            yield from reader.construct_multicalls()

    def read_historical(
        self,
        vaults: list[VaultBase],
        start_block: int,
        end_block: int,
        step: int,
    ) -> Iterable[VaultHistoricalRead]:
        """Create an iterable that extracts vault record from RPC.

        :return:
            Unordered results
        """
        readers = self.prepare_readers(vaults)
        calls = list(self.generate_vault_historical_calls(readers))
        for combined_result in read_multicall_historical(
            web3factory=self.web3factory,
            calls=calls,
            start_block=start_block,
            end_block=end_block,
            step=step,
            display_progress=f"Reading historical vault price data with {self.max_workers} workers, {start_block:,} - {end_block:,} blocks",
            max_workers=self.max_workers,
            ):

            # Transform single multicall call results to calls batched by vault-results
            block_number = combined_result.block_number
            timestamp = combined_result.timestamp
            vault_data: dict[HexAddress, list[EncodedCallResult]] = defaultdict(list)
            logger.debug(
                "Got %d call results for block %s",
                len(combined_result.results),
                block_number,
            )
            for call_result in combined_result.results:
                vault: HexAddress = call_result.call.extra_data["vault"]
                vault_data[vault].append(call_result)

            for vault_address, results in vault_data.items():
                reader = readers[vault_address]
                yield reader.process_result(block_number, timestamp, results)


def scan_historical_prices_to_parquet(
    output_fname: Path,
    web3: Web3,
    web3factory: Web3Factory,
    vaults: list[VaultBase],
    token_cache: TokenDiskCache,
    step_duration=datetime.timedelta(hours=24),
    start_block=None,
    end_block=None,
    step=None,
    chunk_size=1024,
    compression="zstd",
    max_workers=8,
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

    :return:
        Scan report.
    """

    import pyarrow as pa
    import pyarrow.parquet as pq
    import pyarrow.compute as pc

    assert isinstance(output_fname, Path)
    if start_block is not None:
        assert type(start_block) == int

    if end_block is not None:
        assert type(end_block) == int

    chain_id = web3.eth.chain_id

    assert all(v.first_seen_at_block for v in vaults), f"You need to set vault.first_seen_at_block hint in order to run this reader"
    assert all(v.chain_id == chain_id for v in vaults), f"All vaults must be on the same chain"

    if start_block is None:
        start_block = min(v.first_seen_at_block for v in vaults)

    if end_block is None:
        end_block = get_almost_latest_block_number(web3)

    reader = VaultHistoricalReadMulticaller(
        web3factory,
        supported_quote_tokens=None,
        max_workers=max_workers,
        token_cache=token_cache,
    )

    # Note this is an approx,
    # manual tuning will be needed
    if step is None:
        block_time = EVM_BLOCK_TIMES.get(chain_id)
        assert block_time is not None, f"Block time not configured for chain: {chain_id}"
        step = step_duration // datetime.timedelta(seconds=block_time)
    else:
        block_time = None

    entries_iter = reader.read_historical(
        vaults=vaults,
        start_block=start_block,
        end_block=end_block,
        step=step,
    )

    def converter(entries_iter: Iterable[VaultHistoricalRead]) -> Iterable[dict]:
        for entry in entries_iter:
            yield entry.export()

    converted_iter = converter(entries_iter)

    schema = VaultHistoricalRead.to_pyarrow_schema()

    if output_fname.exists():
        try:
            existing_table = pq.read_table(output_fname)
        except pa.lib.ArrowInvalid as e:
            logger.warning(
                "Parquet file %s, write damaged %s, resetting",
                output_fname,
                str(e),
            )
            existing_table = None
    else:
        existing_table = None

    if existing_table is not None:

        logger.info(
            "Detected existing file %s with %d rows",
            output_fname,
            len(existing_table)
        )
        # Clear existing entries for this chain
        mask = pc.equal(existing_table['chain'], chain_id)
        rows_deleted = pc.sum(mask).as_py() or 0
        existing_table = existing_table.filter(pc.invert(mask))
        existing_row_count = existing_table.num_rows
        logger.info(
            "Removed existing %d rows for chain %d from the vault time-series data, existing table has %d rows",
            rows_deleted,
            chain_id,
            existing_row_count,
        )
        existing = True
    else:
        existing_table = None
        rows_deleted = 0
        existing = False
        existing_row_count = 0

    # Initialize ParquetWriter with the schema
    writer = pq.ParquetWriter(output_fname, schema, compression=compression)

    if existing_table is not None:
        writer.write_table(existing_table)

    rows_written = 0

    logger.info(
        "Starting vault historical price export to %s, we have %d vaults, range %d - %d, block step is %d, block time is %s seconds",
        output_fname,
        len(vaults),
        start_block,
        end_block,
        step,
        block_time,
    )

    for chunk in chunked(converted_iter, chunk_size):
        table = pa.Table.from_pylist(chunk, schema=schema)
        writer.write_table(table)
        rows_written += len(chunk)

    # Close the writer to finalize the file
    writer.close()

    size = output_fname.stat().st_size

    logger.info(
        f"Exported {rows_written} rows, file size is now {size:,} bytes"
    )

    return ParquetScanResult(
        rows_written=rows_written,
        rows_deleted=rows_deleted,
        output_fname=output_fname,
        chain_id=chain_id,
        file_size=size,
        existing=existing,
        existing_row_count=existing_row_count,
    )

