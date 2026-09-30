"""Deterministic Antarctic source, adapter and persistence regressions."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance  # noqa: PLC2701
from eth_defi.erc_4626.core import ERC4626Feature, is_activity_filter_exempt
from eth_defi.erc_4626.discovery_base import DEFAULT_HARDCODED_VAULT_LEAD_SOURCES
from eth_defi.erc_4626.vault_protocol.antarctic import historical_context
from eth_defi.erc_4626.vault_protocol.antarctic.constants import ANTARCTIC_DEPLOYMENTS
from eth_defi.erc_4626.vault_protocol.antarctic.historical_context import AntarcticHistoricalContextStore
from eth_defi.erc_4626.vault_protocol.antarctic.vault import AntarcticVault
from eth_defi.testing.antarctic import RecordedAntarcticProvider, RecordedAntarcticStream, create_antarctic_test_metadata, load_antarctic_settlements
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultHistoricalReader, VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def records() -> list:
    """Load real AMLP/AHLP event fixtures, including bootstrap observations."""
    return load_antarctic_settlements(FIXTURES / "antarctic-settlements.json")


@pytest.fixture
def web3() -> Web3:
    """Use recorded state transport with the production adapter implementation."""
    return Web3(RecordedAntarcticProvider(FIXTURES / "antarctic-rpc.json"))


def test_antarctic_metadata_and_classification(web3: Web3, tmp_path: Path) -> None:
    """Both reviewed LP tokens are chain-scoped and publicly read-only."""
    cache = TokenDiskCache(tmp_path / "tokens.sqlite")
    try:
        database = create_antarctic_test_metadata(web3, cache)
        leads = dict(DEFAULT_HARDCODED_VAULT_LEAD_SOURCES)["Antarctic"]
        assert len(leads) == 2  # noqa: PLR2004
        for deployment in ANTARCTIC_DEPLOYMENTS:
            features = _get_hardcoded_protocol_features(deployment.address, 42161)
            assert features == {ERC4626Feature.antarctic_like, ERC4626Feature.share_price_equivalence}
            assert _get_hardcoded_protocol_features(deployment.address, 1) is None
            assert _get_hardcoded_protocol_features(deployment.manager, 42161) is None
            vault = create_vault_instance(web3, deployment.address, features, token_cache=cache)
            assert isinstance(vault, AntarcticVault)
            assert vault.share_token.decimals == 18  # noqa: PLR2004
            assert vault.denomination_token.decimals == 6  # noqa: PLR2004
            assert vault.get_estimated_lock_up().days == 7  # noqa: PLR2004
            assert vault.fetch_minimum_deposit() == Decimal(10)
            assert vault.get_deposit_manager() is None
            assert vault.get_deposit_manager_capability() is None
            with pytest.raises(NotImplementedError):
                vault.fetch_share_price()
            with pytest.raises(NotImplementedError):
                vault.fetch_total_assets()
            row = database.rows[VaultSpec(42161, deployment.address)]
            assert is_activity_filter_exempt(row["_detection_data"])
            assert row["_share_price_source"].value == "smart-contract-event"
            assert row["Mgmt fee"] is None and row["Perf fee"] is None
        first, second = [AntarcticVault(web3, VaultSpec(42161, d.address)) for d in ANTARCTIC_DEPLOYMENTS]
        assert first.get_strategy_tags() == {StrategyTag.market_making, StrategyTag.perpetual_futures}
        assert second.get_strategy_tags() is None
        assert first.get_historical_reader(False).share_price_change_threshold == 0
        assert VaultHistoricalReader.share_price_change_threshold.fget(first.get_historical_reader(False)) == 0.001  # noqa: PLR2004
    finally:
        cache.close()


def test_antarctic_context_replay_reorg_and_wide_integers(records: list, tmp_path: Path) -> None:
    """Exact context preserves foreign tables, handles removals and retains repairs."""
    event = next(r for r in records if r.kind == "AddLiquidity")
    path = tmp_path / "context.duckdb"
    with AntarcticHistoricalContextStore(path) as store:
        store.connection.execute("CREATE TABLE foreign_history AS SELECT 42 AS sentinel")
        assert store.replace_range(event.pool_address, event.block_number, event.block_number + 1, iter([event])) == (1, 1)
        assert store.replace_range(event.pool_address, event.block_number, event.block_number + 1, iter([event])) == (1, 0)
        store.acknowledge_repair(event.pool_address, event.block_number + 1)
        assert store.fetch_cursor(event.pool_address)[1] is None
        changed = replace(event, block_hash="0x" + "99" * 32, raw_shares=2**255)
        assert store.replace_range(event.pool_address, event.block_number, event.block_number + 1, iter([changed])) == (1, 1)
        assert next(store.iter_settlements(event.pool_address, event.block_number, event.block_number + 1)).raw_shares == 2**255
        store.replace_range(event.pool_address, event.block_number, event.block_number + 1, iter(()))
        assert list(store.iter_settlements(event.pool_address, event.block_number, event.block_number + 1)) == []
        assert store.fetch_cursor(event.pool_address)[1] == event.block_number
        cursor = store.fetch_cursor(event.pool_address)
        with pytest.raises(ValueError):
            store.replace_range(event.pool_address, event.block_number, event.block_number + 1, iter([replace(event, block_number=event.block_number + 2)]))
        assert store.fetch_cursor(event.pool_address) == cursor
        assert store.connection.execute("SELECT sentinel FROM foreign_history").fetchone() == (42,)
        assert store.connection.execute("SELECT count(*) FROM duckdb_constraints() WHERE table_name LIKE 'antarctic_%' AND constraint_type IN ('PRIMARY KEY','UNIQUE')").fetchone() == (0,)


def test_antarctic_context_bulk_duplicate_ingestion(records: list, tmp_path: Path) -> None:
    """Exercise file-backed staging/hash joins beyond the live 980-event history."""
    event = next(r for r in records if r.kind == "AddLiquidity")
    events = [replace(event, log_index=i, raw_shares=2**255 + i) for i in range(10000)]
    with AntarcticHistoricalContextStore(tmp_path / "bulk.duckdb") as store:
        assert store.replace_range(event.pool_address, event.block_number, event.block_number + 1, iter(events)) == (10000, 10000)
        assert store.replace_range(event.pool_address, event.block_number, event.block_number + 1, iter(events)) == (10000, 0)
        assert len(list(store.iter_settlements(event.pool_address, event.block_number, event.block_number + 1, canonical_only=True))) == 1


def test_antarctic_recorded_hypersync_decoder_and_reader(records: list, web3: Web3, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real decoded source transport reaches the reader; redemptions never price it."""

    async def open_stream(client: object, query: object) -> RecordedAntarcticStream:  # noqa: ARG001, RUF029
        return RecordedAntarcticStream(query, records)

    monkeypatch.setattr(historical_context, "open_hypersync_stream", open_stream)
    for deployment in ANTARCTIC_DEPLOYMENTS:
        selected = [r for r in records if r.pool_address == deployment.address]
        start, end = min(r.block_number for r in selected), max(r.block_number for r in selected) + 1
        result = historical_context.fetch_and_store_antarctic_history(web3=web3, hypersync_client=object(), pool_start_blocks={deployment.address: start}, end_block=end, context_path=tmp_path / "context.duckdb", timestamp_cache_path=tmp_path / "timestamps")
        assert result.observations_fetched == len(selected)
        vault = AntarcticVault(web3, VaultSpec(42161, deployment.address))
        vault.historical_context_path = tmp_path / "context.duckdb"
        reads = list(vault.get_historical_reader(False).fetch_contextual_historical_reads(start, end, 999))
        assert reads[0].share_price == 1 and reads[0].total_assets is None
        assert all(read.total_supply is None for read in reads)
        assert {read.block_number for read in reads} == {r.block_number for r in selected if r.kind == "AddLiquidity"}
        replay = historical_context.fetch_and_store_antarctic_history(web3=web3, hypersync_client=object(), pool_start_blocks={deployment.address: start}, end_block=end + 1000, context_path=tmp_path / "context.duckdb", timestamp_cache_path=tmp_path / "timestamps")
        assert replay.observations_inserted == 0
        with AntarcticHistoricalContextStore(tmp_path / "context.duckdb") as store:
            assert store.fetch_cursor(deployment.address)[0] == end + 1000


def test_antarctic_partial_source_does_not_commit(records: list, web3: Web3, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A partial source response cannot delete history or advance its cursor.

    The decoder yields actual events before the incomplete stream ends. The
    completed-range check must fail before any replacement is committed.

    :param records: Actual settlement fixtures.
    :param web3: Recorded Arbitrum state transport.
    :param tmp_path: Isolated context and timestamp caches.
    :param monkeypatch: Incomplete source transport replacement.
    :return: None.
    """
    event = next(r for r in records if r.kind == "AddLiquidity")
    path = tmp_path / "context.duckdb"
    with AntarcticHistoricalContextStore(path) as store:
        store.replace_range(event.pool_address, event.block_number, event.block_number + 1, iter([event]))
        store.acknowledge_repair(event.pool_address, event.block_number + 1)
        original_cursor = store.fetch_cursor(event.pool_address)

    class IncompleteStream(RecordedAntarcticStream):
        async def recv(self) -> object | None:
            response = await super().recv()
            if response is not None:
                response.next_block = self.query.from_block + 1
            return response

    async def open_stream(_client: object, query: object) -> IncompleteStream:  # noqa: RUF029
        return IncompleteStream(query, records)

    monkeypatch.setattr(historical_context, "open_hypersync_stream", open_stream)
    with pytest.raises(RuntimeError, match="Incomplete Antarctic source range"):
        historical_context.fetch_and_store_antarctic_history(web3=web3, hypersync_client=object(), pool_start_blocks={event.pool_address: event.block_number}, end_block=event.block_number + 1000, context_path=path, timestamp_cache_path=tmp_path / "timestamps")
    with AntarcticHistoricalContextStore(path) as store:
        assert store.fetch_cursor(event.pool_address) == original_cursor
        assert list(store.iter_settlements(event.pool_address, event.block_number, event.block_number + 1)) == [event]
