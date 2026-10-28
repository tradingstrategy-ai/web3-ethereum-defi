"""Copied-state migration rehearsal and strictly bounded application."""

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.antarctic import historical_context
from eth_defi.erc_4626.vault_protocol.antarctic import migration as module
from eth_defi.erc_4626.vault_protocol.antarctic.constants import ANTARCTIC_DEPLOYMENTS
from eth_defi.erc_4626.vault_protocol.antarctic.historical_context import AntarcticHistoricalContextStore
from eth_defi.erc_4626.vault_protocol.antarctic.migration import parse_antarctic_migration_dry_run
from eth_defi.testing.antarctic import RecordedAntarcticProvider, RecordedAntarcticStream, create_antarctic_test_metadata, load_antarctic_settlements, write_antarctic_test_prices
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

FIXTURES = Path(__file__).parent / "fixtures"


def test_antarctic_migration_mode() -> None:
    """Require an explicit recognised false literal before application.

    Configuration mistakes cannot accidentally select a persistent operation.

    :return: None.
    """
    assert parse_antarctic_migration_dry_run(None)
    assert parse_antarctic_migration_dry_run("true")
    assert not parse_antarctic_migration_dry_run("false")
    with pytest.raises(ValueError):
        parse_antarctic_migration_dry_run("typo")


def test_antarctic_migration_rehearsal_and_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PLR0914
    """Rehearse the real migration on copied files before a scoped application.

    Original prices, metadata, reader state and foreign context survive dry run.
    Applying replaces only the reviewed two identities and preserves sentinels.

    :param tmp_path: Isolated pipeline and rehearsal volume.
    :param monkeypatch: Recorded network transport and environment configuration.
    :return: None.
    """
    records = load_antarctic_settlements(FIXTURES / "antarctic-settlements.json")
    web3 = Web3(RecordedAntarcticProvider(FIXTURES / "antarctic-rpc.json"))
    cache = TokenDiskCache(tmp_path / "seed-tokens.sqlite")
    metadata = tmp_path / "vault-metadata-db.pickle"
    context = tmp_path / "vault-historical-context.duckdb"
    prices = tmp_path / "vault-prices-1h.parquet"
    reader_state = tmp_path / "reader-state.pickle"
    foreign = VaultSpec(42161, "0x" + "77" * 20)
    try:
        database = create_antarctic_test_metadata(web3, cache)
        database.rows[foreign] = {"sentinel": "unrelated metadata"}
        database.write(metadata)
        start = min(r.block_number for r in records)
        end = max(r.block_number for r in records) + 1
        with AntarcticHistoricalContextStore(context) as store:
            store.connection.execute("CREATE TABLE foreign_history AS SELECT 42 AS sentinel")
            for d in ANTARCTIC_DEPLOYMENTS:
                store.replace_range(d.address, start, end, iter(r for r in records if r.pool_address == d.address))
        write_antarctic_test_prices(web3, cache, tmp_path, start, end)
        frame = pd.read_parquet(prices)
        sentinel = frame.iloc[[-1]].copy()
        sentinel["address"] = foreign.vault_address
        sentinel["share_price"] = 123.456
        pd.concat([frame, sentinel]).to_parquet(prices, index=False)
        # Remove an old cached source row while retaining its advanced cursor.
        # Explicit migration must fetch this older range again, not just tail it.
        with AntarcticHistoricalContextStore(context) as store:
            old = min((r for r in records if r.kind == "AddLiquidity"), key=lambda r: r.block_number)
            store.connection.execute("DELETE FROM antarctic_settlements WHERE pool_address=? AND block_number=?", [old.pool_address, old.block_number])
        reader_state.write_bytes(b"critical unrelated reader state")
    finally:
        cache.close()

    async def open_stream(client: object, query: object) -> RecordedAntarcticStream:  # noqa: ARG001, RUF029
        return RecordedAntarcticStream(query, records)

    monkeypatch.setattr(historical_context, "open_hypersync_stream", open_stream)
    monkeypatch.setattr(module, "get_pipeline_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "create_multi_provider_web3", lambda *a, **k: web3)  # noqa: ARG005
    monkeypatch.setattr(module, "MultiProviderWeb3Factory", lambda *a, **k: lambda: web3)  # noqa: ARG005
    monkeypatch.setattr(module, "read_json_rpc_url", lambda *a: "https://recorded.invalid")  # noqa: ARG005

    monkeypatch.setattr(module, "configure_hypersync_from_env", lambda *a: SimpleNamespace(hypersync_client=object()))  # noqa: ARG005
    monkeypatch.setattr(module, "get_almost_latest_block_number", lambda *a: 510301932)  # noqa: ARG005
    monkeypatch.setattr(module, "DEFAULT_TIMESTAMP_CACHE_FOLDER", tmp_path / "timestamps")
    monkeypatch.setattr(TokenDiskCache, "DEFAULT_TOKEN_DISK_CACHE_PATH", tmp_path / "apply-tokens.sqlite")
    monkeypatch.setenv("MAX_WORKERS", "1")
    monkeypatch.setenv("DRY_RUN", "true")
    paths = (metadata, prices, context, reader_state)
    before = {p: p.read_bytes() for p in paths}
    module.main()
    assert all(p.read_bytes() == before[p] for p in paths)
    monkeypatch.setenv("DRY_RUN", "false")
    module.main()
    assert reader_state.read_bytes() == before[reader_state]
    database = VaultDatabase.read(metadata)
    assert database.rows[foreign] == {"sentinel": "unrelated metadata"}
    assert all(database.rows[VaultSpec(42161, d.address)]["Protocol"] == "Antarctic" for d in ANTARCTIC_DEPLOYMENTS)
    after = pd.read_parquet(prices)
    pd.testing.assert_frame_equal(after[after["address"] == foreign.vault_address].reset_index(drop=True), sentinel.reset_index(drop=True))
    with AntarcticHistoricalContextStore(context) as store:
        assert store.connection.execute("SELECT sentinel FROM foreign_history").fetchone() == (42,)
        assert list(store.iter_settlements(old.pool_address, old.block_number, old.block_number + 1)) == [old]
