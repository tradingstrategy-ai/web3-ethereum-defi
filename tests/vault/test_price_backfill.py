"""Test the numbered-migration price backfill helper without provider access."""

import datetime
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs import price_backfill
from eth_defi.vault_protocols.repairs.price_backfill import BACKFILL_BLOCKS_ENV, PriceBackfillTarget, read_backfill_block_limit, run_price_backfill

#: Reviewed Ethereum vault with a missing early history.
ETHEREUM_VAULT = "0x00000000000000000000000000000000000000e1"

#: Reviewed Monad vault with a missing early history.
MONAD_VAULT = "0x00000000000000000000000000000000000000a1"

#: Unrelated vault sharing the price file.
OTHER_VAULT = "0x00000000000000000000000000000000000000f1"

#: Minimal raw price schema read by the helper.
SCHEMA = pa.schema([("chain", pa.uint32()), ("address", pa.string()), ("block_number", pa.uint64()), ("timestamp", pa.timestamp("ms")), ("share_price", pa.float64())])


def _rows(chain_id: int, address: str, blocks: list[int], share_price: float = 1.0) -> list[dict]:
    """Build raw price rows with one-hour timestamps.

    :param chain_id: Vault chain.
    :param address: Vault address.
    :param blocks: Block numbers of the rows.
    :param share_price: Share price of every row.
    :return: Row dictionaries matching :py:data:`SCHEMA`.
    """
    return [{"chain": chain_id, "address": address, "block_number": block, "timestamp": datetime.datetime(2026, 9, 1) + datetime.timedelta(hours=block % 1000), "share_price": share_price} for block in blocks]


@pytest.fixture
def pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Create metadata, raw prices, reader state and timestamp caches, and fake providers.

    The fake writer mirrors the common address-scoped writer: it deletes the
    selected vault's rows inside the half-open range and appends new samples.

    :param tmp_path: Temporary directory owned by pytest.
    :param monkeypatch: Replaces RPC, Hypersync and the historical writer.
    :return: Paths and call records used by the tests.
    """
    monkeypatch.delenv(BACKFILL_BLOCKS_ENV, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = {}
    for chain_id, address, first_seen in ((1, ETHEREUM_VAULT, 100), (143, MONAD_VAULT, 1_000)):
        detection = ERC4262VaultDetection(chain_id, address, first_seen, datetime.datetime(2026, 9, 1), {ERC4626Feature.accountable_like}, datetime.datetime(2026, 10, 1), 1, 1)
        rows[VaultSpec(chain_id, address)] = {"Protocol": "Accountable", "_detection_data": detection}
    VaultDatabase(rows=rows).write(data_dir / "vault-metadata-db.pickle")
    table = pa.Table.from_pylist(_rows(1, ETHEREUM_VAULT, [500, 600]) + _rows(143, MONAD_VAULT, [2_000]) + _rows(1, OTHER_VAULT, [150, 200, 550]), schema=SCHEMA)
    pq.write_table(table, data_dir / "vault-prices-1h.parquet")
    (data_dir / "vault-reader-state-1h.pickle").write_bytes(b"reader state")
    timestamp_cache = tmp_path / "block-timestamp"
    timestamp_cache.mkdir()
    for chain_id in (1, 143):
        (timestamp_cache / f"{chain_id}-timestamps.duckdb").write_bytes(b"dense cache")
    monkeypatch.setenv("TIMESTAMP_CACHE", str(timestamp_cache))

    calls = []
    state = SimpleNamespace(monad_readable=True, corrupt_later_rows=False, change_later_value=False)

    def fake_scan(*, output_fname: Path, web3: object, start_block: int, end_block: int, vault_addresses: set[str], timestamp_cache_file: Path, **_kwargs: object) -> dict:
        address = next(iter(vault_addresses))
        calls.append((web3.eth.chain_id, address, start_block, end_block, timestamp_cache_file))
        existing = pq.read_table(output_fname).to_pylist()
        kept = [row for row in existing if not (row["address"] == address and start_block <= row["block_number"] < end_block)]
        if state.corrupt_later_rows:
            kept = [row for row in kept if not (row["address"] == address and row["block_number"] >= end_block)]
        if state.change_later_value:
            kept = [row | {"share_price": 2.0} if row["address"] == address and row["block_number"] >= end_block else row for row in kept]
        new = _rows(web3.eth.chain_id, address, list(range(start_block, end_block, 50)))
        pq.write_table(pa.Table.from_pylist(kept + new, schema=SCHEMA), output_fname)
        return {
            "chain_id": web3.eth.chain_id,
            "start_block": start_block,
            "end_block": end_block,
            "rows_deleted": len(existing) - len(kept) if not state.corrupt_later_rows else sum(row["address"] == address and start_block <= row["block_number"] < end_block for row in existing),
            "existing_row_count": len(kept) if not state.corrupt_later_rows else len(existing) - sum(row["address"] == address and start_block <= row["block_number"] < end_block for row in existing),
            "rows_written": len(new),
            "rows_written_by_vault": {address: len(new)},
            "price_rows_written_by_vault": {address: len(new)},
        }

    def fake_web3(rpc_url: str) -> SimpleNamespace:
        return SimpleNamespace(eth=SimpleNamespace(chain_id=int(rpc_url), get_code=lambda *_args, **_kwargs: b"\x01"))

    monkeypatch.setattr(price_backfill, "read_json_rpc_url", str)
    monkeypatch.setattr(price_backfill, "create_multi_provider_web3", fake_web3)
    monkeypatch.setattr(price_backfill, "MultiProviderWeb3Factory", lambda _rpc_url: None)
    monkeypatch.setattr(price_backfill, "get_almost_latest_block_number", lambda _web3: 10_000)
    monkeypatch.setattr(price_backfill, "configure_hypersync_from_env", lambda _web3: SimpleNamespace(hypersync_client=None))
    monkeypatch.setattr(price_backfill, "create_vault_instance", lambda *_args, **_kwargs: SimpleNamespace(first_seen_at_block=None))
    monkeypatch.setattr(price_backfill, "fetch_monad_state_available", lambda _web3, _block: state.monad_readable)
    monkeypatch.setattr(price_backfill, "scan_historical_prices_to_parquet", fake_scan)
    return SimpleNamespace(data_dir=data_dir, timestamp_cache=timestamp_cache, calls=calls, state=state)


#: Reviewed gaps: first-seen block to the first collected price row.
TARGETS = (
    PriceBackfillTarget(1, ETHEREUM_VAULT, 100, 500),
    PriceBackfillTarget(143, MONAD_VAULT, 1_000, 2_000),
)


def _context(data_dir: Path, *, dry_run: bool) -> MigrationContext:
    """Create a migration context for one attempt.

    :param data_dir: Pipeline data directory.
    :param dry_run: Whether the attempt is a preview.
    :return: Context without journal persistence.
    """
    return MigrationContext(data_dir, dry_run, script_name="016-test.py")


def test_preview_backfills_without_writing(pipeline: SimpleNamespace) -> None:
    """Preview validates every target on scratch copies and changes no file.

    1. Both targets are scanned over their full reviewed ranges.
    2. Preview uses a copied timestamp cache, not the shared one.
    3. Every pipeline file and the shared timestamp cache are byte-identical.

    :param pipeline: Fake pipeline and providers.
    :return: ``None`` after checking the preview.
    """
    before = {path.name: path.read_bytes() for path in pipeline.data_dir.iterdir()}
    caches = {path.name: path.read_bytes() for path in pipeline.timestamp_cache.iterdir()}
    context = _context(pipeline.data_dir, dry_run=True)

    outcomes = run_price_backfill(context, TARGETS)

    # 1. Both targets are scanned over their full reviewed ranges.
    assert [(call[0], call[1], call[2], call[3]) for call in pipeline.calls] == [(1, ETHEREUM_VAULT, 100, 500), (143, MONAD_VAULT, 1_000, 2_000)]
    assert [outcome["status"] for outcome in outcomes] == ["written", "written"]
    assert outcomes[0]["rows_written"] == 8  # noqa: PLR2004 - Samples every 50 blocks in [100, 500).
    assert outcomes[0]["preserved_later_rows"] == 2  # noqa: PLR2004 - Rows at blocks 500 and 600.

    # 2. Preview uses a copied timestamp cache, not the shared one.
    assert all(call[4] != pipeline.timestamp_cache for call in pipeline.calls)

    # 3. Every pipeline file and the shared timestamp cache are unchanged.
    assert {path.name: path.read_bytes() for path in pipeline.data_dir.iterdir()} == before
    assert {path.name: path.read_bytes() for path in pipeline.timestamp_cache.iterdir()} == caches
    assert context.other_data["price_backfill_published"] is False


def test_apply_publishes_with_backup(pipeline: SimpleNamespace) -> None:
    """Apply replaces only the reviewed rows and keeps the original as a backup.

    1. The published file contains the backfilled rows and every preserved row.
    2. The hard-link backup holds the original bytes and is journalled.
    3. Reader state is untouched.

    :param pipeline: Fake pipeline and providers.
    :return: ``None`` after checking the published file.
    """
    price_path = pipeline.data_dir / "vault-prices-1h.parquet"
    original = price_path.read_bytes()
    context = _context(pipeline.data_dir, dry_run=False)

    run_price_backfill(context, TARGETS)

    # 1. The published file contains the backfilled rows and every preserved row.
    rows = pq.read_table(price_path).to_pylist()
    ethereum_blocks = sorted(row["block_number"] for row in rows if row["address"] == ETHEREUM_VAULT)
    assert ethereum_blocks == [*range(100, 500, 50), 500, 600]
    assert sorted(row["block_number"] for row in rows if row["address"] == OTHER_VAULT) == [150, 200, 550]

    # 2. The hard-link backup holds the original bytes and is journalled.
    backup = Path(context.other_data["backup_paths"][0])
    assert backup.read_bytes() == original
    assert str(price_path.resolve()) in context.modified_files

    # 3. Reader state is untouched.
    assert (pipeline.data_dir / "vault-reader-state-1h.pickle").read_bytes() == b"reader state"
    assert context.other_data["price_backfill_published"] is True


def test_monad_state_unavailable_is_reported_and_preserved(pipeline: SimpleNamespace) -> None:
    """An evicted Monad range is reported, while Ethereum is still backfilled.

    :param pipeline: Fake pipeline and providers.
    :return: ``None`` after checking the Monad outcome.
    """
    pipeline.state.monad_readable = False
    outcomes = run_price_backfill(_context(pipeline.data_dir, dry_run=True), TARGETS)
    assert [outcome["status"] for outcome in outcomes] == ["written", "monad_state_unavailable"]
    assert [call[1] for call in pipeline.calls] == [ETHEREUM_VAULT]


def test_block_limit_is_preview_only(pipeline: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """The development block limit shortens ranges in preview and is refused in apply.

    :param pipeline: Fake pipeline and providers.
    :param monkeypatch: Sets the development limit.
    :return: ``None`` after checking both modes.
    """
    monkeypatch.setenv(BACKFILL_BLOCKS_ENV, "100")
    outcomes = run_price_backfill(_context(pipeline.data_dir, dry_run=True), TARGETS)
    assert [(outcome["planned_start_block"], outcome["planned_end_block"]) for outcome in outcomes] == [(100, 200), (1_000, 1_100)]
    assert pipeline.data_dir.joinpath("vault-prices-1h.parquet").exists()
    with pytest.raises(ValueError, match="development preview limit"):
        run_price_backfill(_context(pipeline.data_dir, dry_run=False), TARGETS)


@pytest.mark.parametrize(("value", "dry_run", "expected"), [(None, True, None), ("", False, None), ("250", True, 250)])
def test_read_backfill_block_limit(monkeypatch: pytest.MonkeyPatch, value: str | None, *, dry_run: bool, expected: int | None) -> None:
    """Read an unset or valid development limit.

    :param monkeypatch: Sets the environment variable.
    :param value: Environment value, or ``None`` when unset.
    :param dry_run: Whether the attempt is a preview.
    :param expected: Expected limit.
    :return: ``None`` after checking the value.
    """
    if value is None:
        monkeypatch.delenv(BACKFILL_BLOCKS_ENV, raising=False)
    else:
        monkeypatch.setenv(BACKFILL_BLOCKS_ENV, value)
    assert read_backfill_block_limit(dry_run=dry_run) == expected


def test_read_backfill_block_limit_rejects_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero limit is invalid.

    :param monkeypatch: Sets the environment variable.
    :return: ``None`` after checking the error.
    """
    monkeypatch.setenv(BACKFILL_BLOCKS_ENV, "0")
    with pytest.raises(ValueError, match="positive block count"):
        read_backfill_block_limit(dry_run=True)


def test_changed_later_rows_abort_before_publishing(pipeline: SimpleNamespace) -> None:
    """A writer that touches rows at or after the end block is rejected.

    :param pipeline: Fake pipeline and providers.
    :return: ``None`` after checking that production data is unchanged.
    """
    price_path = pipeline.data_dir / "vault-prices-1h.parquet"
    original = price_path.read_bytes()
    pipeline.state.corrupt_later_rows = True
    with pytest.raises(RuntimeError, match=r"(changed the count|unexpected staged row count)"):
        run_price_backfill(_context(pipeline.data_dir, dry_run=False), TARGETS)
    assert price_path.read_bytes() == original
    assert not list(pipeline.data_dir.glob("*.bak-*"))


def test_changed_later_value_aborts_before_publishing(pipeline: SimpleNamespace) -> None:
    """A writer that changes a preserved value without changing any count is rejected.

    :param pipeline: Fake pipeline and providers.
    :return: ``None`` after checking that production data is unchanged.
    """
    price_path = pipeline.data_dir / "vault-prices-1h.parquet"
    original = price_path.read_bytes()
    pipeline.state.change_later_value = True
    with pytest.raises(RuntimeError, match="changed rows at or after its end block in share_price"):
        run_price_backfill(_context(pipeline.data_dir, dry_run=False), TARGETS)
    assert price_path.read_bytes() == original


def test_missing_vault_is_reported(pipeline: SimpleNamespace) -> None:
    """A target absent from metadata is reported and never scanned.

    :param pipeline: Fake pipeline and providers.
    :return: ``None`` after checking the outcome.
    """
    target = PriceBackfillTarget(1, "0x00000000000000000000000000000000000000b1", 100, 500)
    outcomes = run_price_backfill(_context(pipeline.data_dir, dry_run=True), (target,))
    assert [outcome["status"] for outcome in outcomes] == ["missing_vault"]
    assert pipeline.calls == []
