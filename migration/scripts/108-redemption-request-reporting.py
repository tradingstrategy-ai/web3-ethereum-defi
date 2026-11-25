# ruff: noqa: N999 - numbered migration module
"""Repair the persisted Plutus Hedge and Base gTrade request reporting.

Purpose: Remove false admin closures and record the observed redemption request gate.
Scope: Arbitrum Plutus Hedge and Base gTrade USDC metadata, and verified HedgeVaultV2 price rows.
Branch: fix/plutus-gtrade-redemption-reporting
PR title: fix: correct Plutus Hedge and Base gTrade redemption reporting
PR: Pending publication
Preserves: Historical prices, timestamps, other vaults, settlement and reader state.
Expected: Two metadata rows and only redemption_open and max_redeem in matching V2 history.

LLM-generated operational migration. Changes require human review.
Preview through the numbered migration runner with the scanner stopped.
"""

import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.gains.vault import BASE_GTRADE_USDC, GainsVault
from eth_defi.erc_4626.vault_protocol.plutus.vault import PLUTUS_HEDGE_V2_BLOCK, PlutusVault
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.price_backfill import PRICE_FILE_NAME, _assert_rows_unchanged, publish_staged_price_backfill

PLUTUS_HEDGE = VaultSpec(42161, "0x58bfc95a864e18e8f3041d2fcd3418f48393fe6a")
V2_IMPLEMENTATION = bytes.fromhex("f2b0b9cceaf4a58807168bbf99499c7093bb46d1")
IMPLEMENTATION_SLOT = int.from_bytes(Web3.keccak(text="eip1967.proxy.implementation")) - 1
HISTORY_FIELDS = ("redemption_open", "max_redeem")


def stage_plutus_history(source: Path, destination: Path, request_open_by_block: dict[int, bool]) -> int:
    """Stage just the two status fields at verified V2 source blocks.

    :param source: Existing raw price parquet.
    :param destination: Writable staged file on the same filesystem.
    :param request_open_by_block: Exact block reads from the verified V2 implementation.
    :return: Number of matching rows.
    """
    matched = 0
    block_numbers = pa.array(sorted(request_open_by_block), type=pa.uint64())

    def mask(table: pa.Table) -> pa.ChunkedArray:
        """Select only the reviewed vault and the successfully verified blocks."""
        identity = pc.and_(pc.equal(table["chain"], PLUTUS_HEDGE.chain_id), pc.equal(pc.ascii_lower(table["address"]), PLUTUS_HEDGE.vault_address))
        return pc.fill_null(pc.and_(identity, pc.is_in(table["block_number"], value_set=pc.cast(block_numbers, table["block_number"].type))), False)

    with pq.ParquetFile(source) as original:
        schema = original.schema_arrow
        state_type = schema.field("redemption_open").type
        boolean_state = pa.types.is_boolean(state_type)
        if not (boolean_state or pa.types.is_string(state_type) or pa.types.is_large_string(state_type)):
            raise ValueError(f"Unsupported redemption_open type: {state_type}")
        with pq.ParquetWriter(destination, schema) as writer:
            for batch in tqdm(original.iter_batches(batch_size=100_000), desc="Staging Plutus Hedge history"):
                table = pa.Table.from_batches([batch])
                selected = mask(table)
                count = pc.sum(pc.cast(selected, pa.int64())).as_py() or 0
                matched += count
                if count:
                    status = []
                    for block, selected_row in zip(table["block_number"].to_pylist(), selected.to_pylist(), strict=True):
                        value = request_open_by_block[block] if selected_row else None
                        status.append(value if boolean_state or value is None else str(value).lower())
                    opens = pc.if_else(selected, pa.array(status, type=state_type), table["redemption_open"])
                    # The scanner writes float NaN through a Pandas Arrow
                    # conversion, which stores it as a Parquet null.
                    caps = pc.if_else(selected, pa.scalar(None, type=schema.field("max_redeem").type), table["max_redeem"])
                    for field, values in zip(HISTORY_FIELDS, (opens, caps), strict=True):
                        table = table.set_column(schema.get_field_index(field), schema.field(field), values)
                writer.write_table(table)

    with pq.ParquetFile(source) as original, pq.ParquetFile(destination) as staged:
        assert original.metadata.num_rows == staged.metadata.num_rows
        assert original.schema_arrow.equals(staged.schema_arrow, check_metadata=True)
        for old, new in zip(original.iter_batches(batch_size=100_000), staged.iter_batches(batch_size=100_000), strict=True):
            before, after = pa.Table.from_batches([old]), pa.Table.from_batches([new])
            _assert_rows_unchanged(before.drop(HISTORY_FIELDS), after.drop(HISTORY_FIELDS), "Plutus price data")
            unrelated = pc.invert(mask(before))
            _assert_rows_unchanged(before.filter(unrelated), after.filter(unrelated), "Other vaults and unverified blocks")
    return matched


def fetch_history_status(source: Path, fetch_at_block: Callable[[int], bool | None], max_workers: int = 4) -> dict[int, bool]:
    """Read request availability at stored V2 price blocks.

    A recognized later implementation is outside this repair. An RPC failure
    raises before the migrator publishes either staged change.

    :param source: Existing raw price parquet.
    :param fetch_at_block: Read a verified block or return None for other implementations.
    :param max_workers: Number of parallel source block reads.
    :return: Successfully verified block observations.
    """
    rows = pq.read_table(
        source,
        columns=["block_number"],
        filters=[
            ("chain", "=", PLUTUS_HEDGE.chain_id),
            ("address", "=", PLUTUS_HEDGE.vault_address),
            ("block_number", ">=", PLUTUS_HEDGE_V2_BLOCK),
        ],
    )
    blocks = sorted(set(rows.column("block_number").to_pylist()))
    observations = Parallel(n_jobs=max_workers, backend="threading", return_as="generator")(delayed(fetch_at_block)(block) for block in blocks)
    return {block: opened for block, opened in zip(blocks, tqdm(observations, total=len(blocks), desc="Reading Plutus request history"), strict=True) if opened is not None}


def migrate(context: MigrationContext) -> None:
    """Refresh reviewed metadata and stage scoped history through the runner."""
    database = context.metadata()
    scoped_vaults = ((PLUTUS_HEDGE, "Plutus", PlutusVault), (BASE_GTRADE_USDC, "Gains Network", GainsVault))
    updates = {}
    for spec, protocol, adapter in scoped_vaults:
        row = database.rows.get(spec)
        if row is None:
            continue
        if row.get("Protocol") != protocol:
            raise ValueError(f"Unexpected protocol for {spec.as_string_id()}")
        web3 = create_multi_provider_web3(read_json_rpc_url(spec.chain_id))
        if web3.eth.chain_id != spec.chain_id:
            raise ValueError(f"Wrong RPC chain for {spec.as_string_id()}")
        observation = adapter(web3, spec).fetch_redemption_request_observation("latest")
        if observation is None or observation.request_open is None:
            raise ValueError(f"Redemption request status unavailable for {spec.as_string_id()}")
        updates[spec] = row | {
            "_redemption_request_observation": observation.export(),
            "_redemption_closed_reason": observation.closed_reason,
            "_redemption_next_open": observation.next_open,
        }

    if PLUTUS_HEDGE in updates:
        path = context.data_dir / PRICE_FILE_NAME
        rpc_url = read_json_rpc_url(PLUTUS_HEDGE.chain_id)
        worker = threading.local()

        def fetch_at_block(block: int) -> bool | None:
            """Resolve the exact proxy implementation before using its ABI."""
            if not hasattr(worker, "vault"):
                web3 = create_multi_provider_web3(rpc_url)
                worker.vault = PlutusVault(web3, PLUTUS_HEDGE)
            vault = worker.vault
            implementation = vault.web3.eth.get_storage_at(vault.address, IMPLEMENTATION_SLOT, block_identifier=block)[-20:]
            if implementation != V2_IMPLEMENTATION:
                return None
            return not vault.fetch_withdrawals_paused(block)

        max_workers = int(os.environ.get("MAX_WORKERS", "4"))
        if max_workers < 1:
            raise ValueError("MAX_WORKERS must be positive")
        history = fetch_history_status(path, fetch_at_block, max_workers=max_workers)
        if history:
            with tempfile.TemporaryDirectory(dir=context.data_dir, prefix="plutus-redemption-") as folder:
                staged = Path(folder) / PRICE_FILE_NAME
                matched = stage_plutus_history(path, staged, history)
                context.report(historical_rows=matched, first_block=min(history), last_block=max(history), history_fields=list(HISTORY_FIELDS))
                if not context.dry_run:
                    publish_staged_price_backfill(context, path, staged)
        else:
            context.report(historical_rows=0)

    database.rows.update(updates)
    context.report(inspected_rows=len(updates))
    context.commit_metadata()
