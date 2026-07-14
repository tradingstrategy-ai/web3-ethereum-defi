"""Tests for ERC-4626 settlement scan orchestration."""

import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from hexbytes import HexBytes
from web3.datastructures import AttributeDict

from eth_defi.erc_4626 import settlement_scan as settlement_scan_module
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.settlement_scan import VaultSettlementScanRange, select_vault_settlement_scan_ranges, select_vault_settlement_scan_ranges_for_chain
from eth_defi.token import TokenDiskCache
from eth_defi.vault import scan_all_chains as scan_all_chains_module
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.settlement_data import VaultSettlement, VaultSettlementDatabase
from eth_defi.vault.vaultdb import VaultDatabase

LAGOON_ADDRESS = "0xabc0000000000000000000000000000000000000"
D2_ADDRESS = "0xd200000000000000000000000000000000000000"
EMBER_ADDRESS = "0xe000000000000000000000000000000000000000"
OTHER_ADDRESS = "0xdef0000000000000000000000000000000000000"
CHAIN2_ADDRESS = "0x2220000000000000000000000000000000000000"


class EmptySettlementDb:
    """Minimal settlement database stand-in for range selection tests."""

    def get_latest_block_number(self, _chain_id: int, _address: str) -> int | None:
        """Return no existing settlement progress."""
        return None

    def get_latest_scanned_block_number(self, _chain_id: int, _address: str) -> int | None:
        """Return no existing scan progress."""
        return None


def make_vault_db() -> VaultDatabase:
    """Create a minimal vault database with one Lagoon vault and one non-Lagoon vault."""
    now = datetime.datetime(2026, 2, 1, 0, 0, 0)
    lagoon_detection = ERC4262VaultDetection(
        chain=1,
        address=LAGOON_ADDRESS,
        first_seen_at_block=100,
        first_seen_at=now,
        features={ERC4626Feature.lagoon_like, ERC4626Feature.erc_7540_like},
        updated_at=now,
        deposit_count=10,
        redeem_count=5,
    )
    d2_detection = ERC4262VaultDetection(
        chain=1,
        address=D2_ADDRESS,
        first_seen_at_block=100,
        first_seen_at=now,
        features={ERC4626Feature.d2_like},
        updated_at=now,
        deposit_count=10,
        redeem_count=5,
    )
    ember_detection = ERC4262VaultDetection(
        chain=1,
        address=EMBER_ADDRESS,
        first_seen_at_block=100,
        first_seen_at=now,
        features={ERC4626Feature.ember_like},
        updated_at=now,
        deposit_count=10,
        redeem_count=5,
    )
    other_detection = ERC4262VaultDetection(
        chain=1,
        address=OTHER_ADDRESS,
        first_seen_at_block=100,
        first_seen_at=now,
        features=set(),
        updated_at=now,
        deposit_count=10,
        redeem_count=5,
    )
    return VaultDatabase(
        rows={
            VaultSpec(1, LAGOON_ADDRESS): {
                "Address": LAGOON_ADDRESS,
                "Protocol": "Lagoon Finance",
                "features": lagoon_detection.features,
                "_detection_data": lagoon_detection,
            },
            VaultSpec(1, OTHER_ADDRESS): {
                "Address": OTHER_ADDRESS,
                "Protocol": "Generic",
                "features": other_detection.features,
                "_detection_data": other_detection,
            },
            VaultSpec(1, D2_ADDRESS): {
                "Address": D2_ADDRESS,
                "Protocol": "D2 Finance",
                "features": d2_detection.features,
                "_detection_data": d2_detection,
            },
            VaultSpec(1, EMBER_ADDRESS): {
                "Address": EMBER_ADDRESS,
                "Protocol": "Ember",
                "features": ember_detection.features,
                "_detection_data": ember_detection,
            },
        }
    )


def make_multichain_vault_db() -> VaultDatabase:
    """Create a vault database with supported vaults on two chains."""
    vault_db = make_vault_db()
    now = datetime.datetime(2026, 2, 1, 0, 0, 0)
    chain2_detection = ERC4262VaultDetection(
        chain=2,
        address=CHAIN2_ADDRESS,
        first_seen_at_block=100,
        first_seen_at=now,
        features={ERC4626Feature.lagoon_like},
        updated_at=now,
        deposit_count=10,
        redeem_count=5,
    )
    vault_db.rows[VaultSpec(2, CHAIN2_ADDRESS)] = {
        "Address": CHAIN2_ADDRESS,
        "Protocol": "Lagoon Finance",
        "features": chain2_detection.features,
        "_detection_data": chain2_detection,
    }
    return vault_db


def test_select_vault_settlement_scan_ranges_incremental_lagoon_only(tmp_path: Path) -> None:
    """Scan ranges start after the latest stored settlement block."""
    pytest.importorskip("duckdb")

    raw_prices = pd.DataFrame(
        [
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 100},
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 200},
            {"chain": 1, "address": OTHER_ADDRESS, "block_number": 100},
            {"chain": 1, "address": OTHER_ADDRESS, "block_number": 200},
        ]
    )
    db = VaultSettlementDatabase(tmp_path / "vault-settlements.duckdb")
    try:
        db.upsert_settlements(
            [
                VaultSettlement(
                    chain_id=1,
                    address=LAGOON_ADDRESS,
                    block_number=150,
                    protocol="Lagoon Finance",
                    block_hash="0x" + "11" * 32,
                    timestamp=datetime.datetime(2026, 2, 1, 12, 0, 0),
                    tx_hash="0x" + "22" * 32,
                )
            ]
        )

        ranges = select_vault_settlement_scan_ranges(
            make_vault_db(),
            raw_prices,
            db,
            supported_features={ERC4626Feature.lagoon_like},
        )

        assert len(ranges) == 1
        assert ranges[0].chain_id == 1
        assert ranges[0].address == LAGOON_ADDRESS
        assert ranges[0].start_block == 151
        assert ranges[0].end_block == 200
    finally:
        db.close()


def test_select_vault_settlement_scan_ranges_uses_empty_scan_state(tmp_path: Path) -> None:
    """Empty settlement scans advance the next range without requiring event rows."""
    pytest.importorskip("duckdb")

    raw_prices = pd.DataFrame(
        [
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 100},
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 200},
        ]
    )
    db = VaultSettlementDatabase(tmp_path / "vault-settlements.duckdb")
    try:
        db.upsert_scan_state([(1, LAGOON_ADDRESS, 200)])

        ranges = select_vault_settlement_scan_ranges(
            make_vault_db(),
            raw_prices,
            db,
            supported_features={ERC4626Feature.lagoon_like},
        )

        assert ranges == []
    finally:
        db.close()


def test_select_vault_settlement_scan_ranges_for_chain_uses_scan_end_block(tmp_path: Path) -> None:
    """Production chain scans can select ranges without reading raw price parquet."""
    pytest.importorskip("duckdb")

    db = VaultSettlementDatabase(tmp_path / "vault-settlements.duckdb")
    try:
        db.upsert_scan_state([(1, LAGOON_ADDRESS, 150)])

        ranges = select_vault_settlement_scan_ranges_for_chain(
            make_vault_db(),
            db,
            chain_id=1,
            end_block=200,
            supported_features={ERC4626Feature.lagoon_like},
        )

        assert ranges == [
            VaultSettlementScanRange(chain_id=1, address=LAGOON_ADDRESS, start_block=151, end_block=200),
        ]
    finally:
        db.close()


def test_select_vault_settlement_scan_ranges_forced_lagoon_backfill(tmp_path: Path) -> None:
    """Forced backfill ranges are intersected with raw price block ranges."""
    pytest.importorskip("duckdb")

    raw_prices = pd.DataFrame(
        [
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 100},
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 200},
        ]
    )
    db = VaultSettlementDatabase(tmp_path / "vault-settlements.duckdb")
    try:
        db.upsert_settlements(
            [
                VaultSettlement(
                    chain_id=1,
                    address=LAGOON_ADDRESS,
                    block_number=150,
                    protocol="Lagoon Finance",
                    block_hash="0x" + "11" * 32,
                    timestamp=datetime.datetime(2026, 2, 1, 12, 0, 0),
                    tx_hash="0x" + "22" * 32,
                )
            ]
        )
        ranges = select_vault_settlement_scan_ranges(
            make_vault_db(),
            raw_prices,
            db,
            supported_features={ERC4626Feature.lagoon_like},
            forced_start_block=120,
            forced_end_block=180,
        )

        assert len(ranges) == 1
        assert ranges[0].start_block == 120
        assert ranges[0].end_block == 180
    finally:
        db.close()


def test_select_vault_settlement_scan_ranges_includes_supported_protocols() -> None:
    """Generic settlement scans include Lagoon, D2 and Ember vaults."""
    raw_prices = pd.DataFrame(
        [
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 100},
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 200},
            {"chain": 1, "address": D2_ADDRESS, "block_number": 110},
            {"chain": 1, "address": D2_ADDRESS, "block_number": 210},
            {"chain": 1, "address": EMBER_ADDRESS, "block_number": 120},
            {"chain": 1, "address": EMBER_ADDRESS, "block_number": 220},
            {"chain": 1, "address": OTHER_ADDRESS, "block_number": 100},
            {"chain": 1, "address": OTHER_ADDRESS, "block_number": 200},
        ]
    )
    ranges = select_vault_settlement_scan_ranges(make_vault_db(), raw_prices, EmptySettlementDb())

    assert [(item.address, item.start_block, item.end_block) for item in ranges] == [
        (LAGOON_ADDRESS, 100, 200),
        (D2_ADDRESS, 110, 210),
        (EMBER_ADDRESS, 120, 220),
    ]


def test_update_settlement_database_for_chain_advances_ember_skipped_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skipped Ember processing events write no settlement but advance the watermark."""

    class FakeEmberVault:
        """Minimal Ember settlement reader stand-in."""

        def __init__(self, address: str) -> None:
            self.address = address
            self.chain_id = 1
            self.web3 = None

    class FakeDatabase:
        """Capture the scan watermark without requiring DuckDB."""

        def upsert_settlements(self, settlements: list[VaultSettlement]) -> int:
            self.settlements = settlements
            return len(settlements)

        def upsert_scan_state(self, scan_states: list[tuple[int, str, int]]) -> int:
            self.scan_states = scan_states
            return len(scan_states)

    ember_topic = "0x" + "33" * 32
    monkeypatch.setattr(settlement_scan_module, "EmberVault", FakeEmberVault)
    monkeypatch.setattr(
        settlement_scan_module,
        "create_vault_instance",
        lambda web3, address, features, token_cache: FakeEmberVault(address),
    )
    monkeypatch.setattr(settlement_scan_module, "get_ember_settlement_events_by_topic", lambda _vault: {ember_topic: "RequestProcessed"})
    monkeypatch.setattr(
        settlement_scan_module,
        "fetch_vault_settlement_logs_for_addresses",
        lambda **_kwargs: [AttributeDict({"address": EMBER_ADDRESS, "topics": [HexBytes(ember_topic)], "blockNumber": 20})],
    )
    monkeypatch.setattr(settlement_scan_module, "build_ember_settlement_rows_from_logs", lambda *_args, **_kwargs: [])

    database = FakeDatabase()
    result = settlement_scan_module._update_settlement_database_for_chain(
        database=database,
        web3=object(),
        chain_id=1,
        ranges=[VaultSettlementScanRange(chain_id=1, address=EMBER_ADDRESS, start_block=10, end_block=20)],
        rows_by_key={(1, EMBER_ADDRESS): {"features": {ERC4626Feature.ember_like}, "_detection_data": SimpleNamespace(chain=1, address=EMBER_ADDRESS)}},
        token_cache=TokenDiskCache(),
        use_hypersync=False,
        chunk_size=50_000,
    )

    assert result.rows_written == 0
    assert result.scanned_vaults == 1
    assert database.settlements == []
    assert database.scan_states == [(1, EMBER_ADDRESS, 20)]


def test_update_settlement_database_for_chain_batches_all_vaults_and_skips_bad_vaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """A chain settlement scan fetches all usable vault logs as one address batch."""

    class FakeLagoonVault:
        """Minimal LagoonVault stand-in for the chain batch test."""

        def __init__(self, address: str) -> None:
            self.address = address
            self.chain_id = 1
            self.web3 = None

    class FakeD2Vault:
        """Minimal D2Vault stand-in for the chain batch test."""

        def __init__(self, address: str) -> None:
            self.address = address
            self.chain_id = 1
            self.web3 = None

    class FakeDatabase:
        """Capture settlement rows written by the batch helper."""

        def __init__(self) -> None:
            self.settlements: list[VaultSettlement] = []

        def upsert_settlements(self, settlements: list[VaultSettlement]) -> int:
            """Store rows for assertions."""
            self.settlements.extend(settlements)
            return len(settlements)

        def upsert_scan_state(self, scan_states: list[tuple[int, str, int]]) -> int:
            """Store scan-state updates for assertions."""
            self.scan_states = scan_states
            return len(scan_states)

    lagoon_topic = "0x" + "11" * 32
    d2_topic = "0x" + "22" * 32
    fetch_calls = []

    def fake_create_vault_instance(web3, address, features, token_cache):
        """Return a fake vault for every metadata row."""
        assert web3 is not None
        assert token_cache is not None
        if address == OTHER_ADDRESS:
            raise RuntimeError("broken vault")
        if features == {ERC4626Feature.d2_like}:
            return FakeD2Vault(address)
        return FakeLagoonVault(address)

    def fake_fetch_logs(**kwargs):
        """Return logs for both vaults plus one out-of-range log."""
        fetch_calls.append(kwargs)
        return [
            AttributeDict({"address": LAGOON_ADDRESS, "topics": [HexBytes(lagoon_topic)], "blockNumber": 15, "logIndex": 0}),
            AttributeDict({"address": D2_ADDRESS, "topics": [HexBytes(d2_topic)], "blockNumber": 25, "logIndex": 1}),
            AttributeDict({"address": LAGOON_ADDRESS, "topics": [HexBytes(lagoon_topic)], "blockNumber": 9, "logIndex": 2}),
            AttributeDict({"address": D2_ADDRESS, "topics": [HexBytes(lagoon_topic)], "blockNumber": 25, "logIndex": 3}),
        ]

    def fake_build_rows(vault: FakeLagoonVault | FakeD2Vault, logs: list[AttributeDict], event_by_topic: dict) -> list[VaultSettlement]:
        """Build one generic row for each surviving log."""
        topic = lagoon_topic if isinstance(vault, FakeLagoonVault) else d2_topic
        assert event_by_topic == {topic: "SettleDeposit"}
        return [
            VaultSettlement(
                chain_id=1,
                address=vault.address,
                block_number=int(log["blockNumber"]),
                protocol="Lagoon Finance",
                block_hash="0x" + "22" * 32,
                timestamp=datetime.datetime(2026, 2, 1, 12, 0, 0),
                tx_hash="0x" + f"{int(log['logIndex']) + 1:064x}",
                event_name="SettleDeposit",
            )
            for log in logs
        ]

    monkeypatch.setattr(settlement_scan_module, "LagoonVault", FakeLagoonVault)
    monkeypatch.setattr(settlement_scan_module, "D2Vault", FakeD2Vault)
    monkeypatch.setattr(settlement_scan_module, "create_vault_instance", fake_create_vault_instance)
    monkeypatch.setattr(settlement_scan_module, "get_settlement_events_by_topic", lambda _vault: {lagoon_topic: "SettleDeposit"})
    monkeypatch.setattr(settlement_scan_module, "get_d2_settlement_events_by_topic", lambda _vault: {d2_topic: "SettleDeposit"})
    monkeypatch.setattr(settlement_scan_module, "fetch_vault_settlement_logs_for_addresses", fake_fetch_logs)
    monkeypatch.setattr(settlement_scan_module, "build_lagoon_settlement_rows_from_logs", fake_build_rows)
    monkeypatch.setattr(settlement_scan_module, "build_d2_settlement_rows_from_logs", fake_build_rows)

    rows_by_key = {
        (1, LAGOON_ADDRESS): {
            "features": {ERC4626Feature.lagoon_like},
            "_detection_data": SimpleNamespace(chain=1, address=LAGOON_ADDRESS),
        },
        (1, D2_ADDRESS): {
            "features": {ERC4626Feature.d2_like},
            "_detection_data": SimpleNamespace(chain=1, address=D2_ADDRESS),
        },
        (1, OTHER_ADDRESS): {
            "features": {ERC4626Feature.lagoon_like},
            "_detection_data": SimpleNamespace(chain=1, address=OTHER_ADDRESS),
        },
    }
    database = FakeDatabase()
    update_result = settlement_scan_module._update_settlement_database_for_chain(
        database=database,
        web3=object(),
        chain_id=1,
        ranges=[
            VaultSettlementScanRange(chain_id=1, address=LAGOON_ADDRESS, start_block=10, end_block=20),
            VaultSettlementScanRange(chain_id=1, address=D2_ADDRESS, start_block=20, end_block=30),
            VaultSettlementScanRange(chain_id=1, address=OTHER_ADDRESS, start_block=20, end_block=30),
        ],
        rows_by_key=rows_by_key,
        token_cache=TokenDiskCache(),
        use_hypersync=False,
        chunk_size=50_000,
    )

    assert update_result.rows_written == 2
    assert update_result.scanned_vaults == 2
    assert len(fetch_calls) == 1
    assert set(fetch_calls[0]["addresses"]) == {LAGOON_ADDRESS, D2_ADDRESS}
    assert fetch_calls[0]["topic0_list"] == [lagoon_topic, d2_topic]
    assert fetch_calls[0]["start_block"] == 10
    assert fetch_calls[0]["end_block"] == 30
    assert [(row.address, row.block_number) for row in database.settlements] == [
        (LAGOON_ADDRESS, 15),
        (D2_ADDRESS, 25),
    ]
    assert sorted(database.scan_states) == [
        (1, LAGOON_ADDRESS, 20),
        (1, D2_ADDRESS, 30),
    ]


def test_update_settlement_database_for_chain_advances_empty_scan_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """A prepared vault with no matching logs still advances its scan watermark."""

    class FakeLagoonVault:
        """Minimal LagoonVault stand-in for an empty scan."""

        def __init__(self, address: str) -> None:
            self.address = address
            self.chain_id = 1
            self.web3 = None

    class FakeDatabase:
        """Capture settlement rows and scan states written by the batch helper."""

        def __init__(self) -> None:
            self.settlements: list[VaultSettlement] = []
            self.scan_states: list[tuple[int, str, int]] = []

        def upsert_settlements(self, settlements: list[VaultSettlement]) -> int:
            """Store rows for assertions."""
            self.settlements.extend(settlements)
            return len(settlements)

        def upsert_scan_state(self, scan_states: list[tuple[int, str, int]]) -> int:
            """Store scan-state updates for assertions."""
            self.scan_states = scan_states
            return len(scan_states)

    lagoon_topic = "0x" + "11" * 32

    monkeypatch.setattr(settlement_scan_module, "LagoonVault", FakeLagoonVault)

    def fake_create_vault_instance(_web3, address, _features, token_cache):
        """Return a fake vault while matching the production call signature."""
        assert token_cache is not None
        return FakeLagoonVault(address)

    monkeypatch.setattr(settlement_scan_module, "create_vault_instance", fake_create_vault_instance)
    monkeypatch.setattr(settlement_scan_module, "get_settlement_events_by_topic", lambda _vault: {lagoon_topic: "SettleDeposit"})
    monkeypatch.setattr(settlement_scan_module, "fetch_vault_settlement_logs_for_addresses", lambda **_kwargs: [])

    database = FakeDatabase()
    update_result = settlement_scan_module._update_settlement_database_for_chain(
        database=database,
        web3=object(),
        chain_id=1,
        ranges=[
            VaultSettlementScanRange(chain_id=1, address=LAGOON_ADDRESS, start_block=10, end_block=20),
        ],
        rows_by_key={
            (1, LAGOON_ADDRESS): {
                "features": {ERC4626Feature.lagoon_like},
                "_detection_data": SimpleNamespace(chain=1, address=LAGOON_ADDRESS),
            },
        },
        token_cache=TokenDiskCache(),
        use_hypersync=False,
        chunk_size=50_000,
    )

    assert update_result.rows_written == 0
    assert update_result.scanned_vaults == 1
    assert database.settlements == []
    assert database.scan_states == [(1, LAGOON_ADDRESS, 20)]


def test_fetch_and_store_vault_settlements_filters_chain_ids(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Per-chain settlement scan only processes the requested chain id."""

    class FakeSettlementDb:
        """Minimal settlement database used by the orchestration test."""

        def __init__(self, _path: Path) -> None:
            self.saved = False
            self.closed = False

        def get_latest_block_number(self, _chain_id: int, _address: str) -> int | None:
            """Return no incremental progress."""
            return None

        def get_latest_scanned_block_number(self, _chain_id: int, _address: str) -> int | None:
            """Return no incremental progress."""
            return None

        def save(self) -> None:
            """Record that the database was saved."""
            self.saved = True

        def close(self) -> None:
            """Record that the database was closed."""
            self.closed = True

    update_calls = []
    raw_prices = pd.DataFrame(
        [
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 100},
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 120},
            {"chain": 2, "address": CHAIN2_ADDRESS, "block_number": 200},
            {"chain": 2, "address": CHAIN2_ADDRESS, "block_number": 220},
        ]
    )

    def fake_update_settlement_database_for_chain(**kwargs) -> settlement_scan_module.ChainSettlementUpdateResult:
        """Capture selected ranges for assertions."""
        update_calls.append(kwargs)
        return settlement_scan_module.ChainSettlementUpdateResult(rows_written=len(kwargs["ranges"]), scanned_vaults=len(kwargs["ranges"]))

    vault_db_path = tmp_path / "vault-db.pickle"
    raw_price_path = tmp_path / "raw-prices.parquet"
    vault_db_path.touch()
    raw_price_path.touch()

    monkeypatch.setattr(settlement_scan_module.VaultDatabase, "read", staticmethod(lambda _path: make_multichain_vault_db()))
    read_parquet_calls = []

    def fake_read_parquet(*_args, **kwargs):
        """Capture parquet read parameters."""
        read_parquet_calls.append(kwargs)
        return raw_prices

    monkeypatch.setattr(settlement_scan_module.pd, "read_parquet", fake_read_parquet)
    monkeypatch.setattr(settlement_scan_module, "VaultSettlementDatabase", FakeSettlementDb)
    monkeypatch.setattr(settlement_scan_module, "create_multi_provider_web3", lambda _rpc_url: object())
    monkeypatch.setattr(settlement_scan_module, "_update_settlement_database_for_chain", fake_update_settlement_database_for_chain)

    result = settlement_scan_module.fetch_and_store_vault_settlements(
        vault_db_path=vault_db_path,
        raw_price_path=raw_price_path,
        settlement_db_path=tmp_path / "settlements.duckdb",
        rpc_urls_by_chain={1: "rpc1", 2: "rpc2"},
        chain_ids={2},
    )

    assert result.candidate_vaults == 1
    assert result.scanned_vaults == 1
    assert result.scanned_chains == 1
    assert result.failed_chains == 0
    assert result.rows_written == 1
    assert read_parquet_calls[0]["filters"] == [("chain", "in", [2])]
    assert [call["chain_id"] for call in update_calls] == [2]
    assert update_calls[0]["ranges"] == [VaultSettlementScanRange(chain_id=2, address=CHAIN2_ADDRESS, start_block=200, end_block=220)]


def test_fetch_and_store_vault_settlements_continues_after_failed_chain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Graceful settlement scans count a failed chain and continue with other chains."""

    class FakeSettlementDb:
        """Minimal settlement database used by the failure test."""

        def __init__(self, _path: Path) -> None:
            pass

        def get_latest_block_number(self, _chain_id: int, _address: str) -> int | None:
            """Return no incremental progress."""
            return None

        def get_latest_scanned_block_number(self, _chain_id: int, _address: str) -> int | None:
            """Return no incremental progress."""
            return None

        def save(self) -> None:
            """No-op save."""

        def close(self) -> None:
            """No-op close."""

    raw_prices = pd.DataFrame(
        [
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 100},
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 120},
            {"chain": 2, "address": CHAIN2_ADDRESS, "block_number": 200},
            {"chain": 2, "address": CHAIN2_ADDRESS, "block_number": 220},
        ]
    )

    def fake_update_settlement_database_for_chain(**kwargs) -> settlement_scan_module.ChainSettlementUpdateResult:
        """Fail chain 1 and succeed chain 2."""
        if kwargs["chain_id"] == 1:
            raise RuntimeError("chain failed")
        return settlement_scan_module.ChainSettlementUpdateResult(rows_written=len(kwargs["ranges"]), scanned_vaults=len(kwargs["ranges"]))

    vault_db_path = tmp_path / "vault-db.pickle"
    raw_price_path = tmp_path / "raw-prices.parquet"
    vault_db_path.touch()
    raw_price_path.touch()

    monkeypatch.setattr(settlement_scan_module.VaultDatabase, "read", staticmethod(lambda _path: make_multichain_vault_db()))
    monkeypatch.setattr(settlement_scan_module.pd, "read_parquet", lambda *_args, **_kwargs: raw_prices)
    monkeypatch.setattr(settlement_scan_module, "VaultSettlementDatabase", FakeSettlementDb)
    monkeypatch.setattr(settlement_scan_module, "create_multi_provider_web3", lambda _rpc_url: object())
    monkeypatch.setattr(settlement_scan_module, "_update_settlement_database_for_chain", fake_update_settlement_database_for_chain)

    result = settlement_scan_module.fetch_and_store_vault_settlements(
        vault_db_path=vault_db_path,
        raw_price_path=raw_price_path,
        settlement_db_path=tmp_path / "settlements.duckdb",
        rpc_urls_by_chain={1: "rpc1", 2: "rpc2"},
        fail_gracefully=True,
    )

    assert result.scanned_chains == 1
    assert result.failed_chains == 1
    assert result.scanned_vaults == 1
    assert result.rows_written == 1

    with pytest.raises(RuntimeError, match="chain failed"):
        settlement_scan_module.fetch_and_store_vault_settlements(
            vault_db_path=vault_db_path,
            raw_price_path=raw_price_path,
            settlement_db_path=tmp_path / "settlements.duckdb",
            rpc_urls_by_chain={1: "rpc1", 2: "rpc2"},
            fail_gracefully=False,
        )


def test_scan_chain_vault_settlements_marks_failed_dashboard_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A graceful chain batch failure is exposed as a failed dashboard row."""

    def fake_fetch_and_store_vault_settlements_for_chain(**kwargs):
        """Return a failed settlement scan summary."""
        assert kwargs["vault_db"] is vault_db
        assert kwargs["chain_id"] == 1
        assert kwargs["rpc_url"] == "rpc"
        assert kwargs["end_block"] == 123
        return settlement_scan_module.VaultSettlementScanResult(
            candidate_vaults=3,
            scanned_vaults=0,
            skipped_vaults=0,
            rows_written=0,
            scanned_chains=0,
            failed_chains=1,
        )

    vault_db = make_vault_db()
    monkeypatch.setattr(scan_all_chains_module, "fetch_and_store_vault_settlements_for_chain", fake_fetch_and_store_vault_settlements_for_chain)

    result = scan_all_chains_module.scan_chain_vault_settlements(
        chain=scan_all_chains_module.ChainConfig(name="Ethereum", env_var="JSON_RPC_ETHEREUM", scan_vaults=True),
        vault_db=vault_db,
        chain_id=1,
        rpc_url="rpc",
        end_block=123,
        settlement_db_path=tmp_path / "settlements.duckdb",
        settlement_start_block=None,
        settlement_end_block=None,
    )

    assert result.name == "Ethereum settlements"
    assert result.status == "failed"
    assert result.error == "1 settlement chain batch failed"
    assert result.vault_count == 3
