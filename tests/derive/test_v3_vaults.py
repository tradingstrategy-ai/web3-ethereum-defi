"""Public Derive v3 native vault discovery and history."""

from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.derive import tags as derive_tags
from eth_defi.derive.v3_constants import DERIVE_V3_CHAIN_ID, make_derive_v3_vault_address
from eth_defi.derive.v3_vault_data_export import build_raw_prices_dataframe, create_derive_v3_vault_row, merge_into_vault_database
from eth_defi.derive.v3_vault_metrics import DeriveV3VaultDatabase
from eth_defi.derive.v3_vaults import DeriveV3Vault, DeriveV3VaultClient, DeriveV3VaultPrice
from eth_defi.perp_dex.parquet import build_registered_perp_vault_index, finalise_perp_metric_columns
from eth_defi.vault import post_processing, scan_all_chains, top_vaults_json
from eth_defi.vault.base import VaultHistoricalRead
from eth_defi.vault.curator import identify_curator
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.fixture
def vault_record() -> dict:
    """Provide a compact public vault listing record.

    :return: API-shaped test record.
    """
    return {
        "protocol": {
            "subaccount_id": 12345,
            "total_shares": "12001.93999209852",
            "config": {
                "management_fee_bps": 100,
                "performance_fee_bps": 1000,
                "deposit_spot_asset": "0x57b03e14d409adc7fab6cfc44b5886cad2d5f02b",
                "cooldown_sec": 86400,
            },
            "closed": False,
        },
        "name": "Example vault",
        "description": "Example strategy",
        "curator": "0x51c82385d2b2b170b8acd54199912e3365625249",
        "nav_usd": "12003.157554207816",
        "simulated_share_price_usd": "0.999936088107",
        "whitelist_only": False,
    }


def test_v3_timestamp_units_and_idempotent_storage(tmp_path: Path, vault_record: dict) -> None:
    """Preserve decimal precision and both documented and observed time units.

    :param tmp_path: Temporary directory for file-backed DuckDB.
    :param vault_record: API-shaped vault fixture.
    :return: ``None``.
    """
    vault = DeriveV3Vault.from_api(vault_record)
    raw_point = {"ts": 1790208000, "share_price": "0.999946852319", "nav": "12003.282080456953", "total_shares": "12001.93999209852"}
    second_point = dict(raw_point, ts=1790121600000)
    point = DeriveV3VaultPrice.from_api(vault.subaccount_id, raw_point)
    earlier = DeriveV3VaultPrice.from_api(vault.subaccount_id, second_point)

    assert point.timestamp.isoformat() == "2026-09-24T00:00:00"
    assert earlier.timestamp.isoformat() == "2026-09-23T00:00:00"
    assert point.share_price == Decimal("0.999946852319")

    db = DeriveV3VaultDatabase(tmp_path / "derive-v3.duckdb")
    try:
        db.store_vault("testnet", vault, [point, earlier])
        first_seen = db.get_vault_metadata("testnet").iloc[0]["observed_at"]
        db.store_vault("testnet", vault, [point, earlier])
        assert len(db.get_vault_metadata("testnet")) == 1
        assert db.get_vault_metadata("testnet").iloc[0]["observed_at"] == first_seen
        prices = db.get_vault_prices("testnet")
        assert len(prices) == len([point, earlier])
        assert prices.iloc[-1]["share_price"] == "0.999946852319"
        assert db.get_vault_prices("mainnet").empty
    finally:
        db.close()


def test_v3_public_testnet_vault_api() -> None:
    """Exercise Derive's real public testnet listing and history endpoints.

    The integration is unauthenticated. It validates the source shape and
    timestamp units before these fields feed a historical vault dataset.

    :return: ``None``.
    """
    client = DeriveV3VaultClient(network="testnet")
    try:
        vaults = list(client.fetch_vaults(page_size=5))
        assert vaults, "Derive v3 testnet returned no public vaults"
        vault = next((item for item in vaults if not item.closed), vaults[0])
        assets = client.fetch_spot_assets()
        assert vault.deposit_spot_asset.lower() in assets
        assert assets[vault.deposit_spot_asset.lower()][0]
        points = list(client.fetch_vault_performance(vault.subaccount_id, limit=3))
        assert points, f"Derive v3 testnet vault {vault.subaccount_id} returned no performance history"
        assert all(point.subaccount_id == vault.subaccount_id for point in points)
        assert all(point.timestamp.tzinfo is None for point in points)
        assert all(point.share_price > 0 for point in points)
        assert len({point.timestamp for point in points}) == len(points)
        full_vault = client.fetch_vault(vault.subaccount_id)
        assert full_vault["protocol"]["subaccount_id"] == vault.subaccount_id
    finally:
        client.close()


def test_v3_public_mainnet_listing() -> None:
    """Exercise the canonical production public listing, including empty state.

    :return: ``None``.
    """
    client = DeriveV3VaultClient(network="mainnet")
    try:
        result = client._post("public/get_vaults", {"page": 1, "page_size": 1})
        assert isinstance(result["vaults"], list)
        assert isinstance(result["pagination"]["count"], int)
    finally:
        client.close()


@pytest.mark.parametrize("timestamp_multiplier", [1, 1000])
def test_v3_history_pagination_uses_seconds_bound(timestamp_multiplier: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Convert either source timestamp unit to the API's seconds bound.

    :param timestamp_multiplier: Source timestamp unit scale.
    :param monkeypatch: Replace public HTTP transport with fixed pages.
    :return: ``None``.
    """
    client = DeriveV3VaultClient(network="testnet")
    requests: list[dict] = []
    oldest_first_page_seconds = 1790121600

    def fake_post(method: str, params: dict) -> dict:
        """Return two fixed source pages.

        :param method: Public API method name.
        :param params: Source request body.
        :return: API-shaped performance page.
        """
        assert method == "public/get_vault_performance_history"
        requests.append(params.copy())
        timestamps = [1790208000, oldest_first_page_seconds] if len(requests) == 1 else [1790035200]
        return {"points": [{"ts": value * timestamp_multiplier, "share_price": "1", "nav": "100", "total_shares": "100"} for value in timestamps]}

    monkeypatch.setattr(client, "_post", fake_post)
    try:
        points = list(client.fetch_vault_performance(86273, limit=2))
        assert [point.timestamp.isoformat() for point in points] == ["2026-09-24T00:00:00", "2026-09-23T00:00:00", "2026-09-22T00:00:00"]
        assert requests[1]["to"] == oldest_first_page_seconds
    finally:
        client.close()


def test_v3_mainnet_export_preserves_shorter_history(tmp_path: Path, vault_record: dict) -> None:  # noqa: PLR0914 - end-to-end export test carries its temporary paths
    """Keep old mainnet prices and exclude testnet metadata and prices.

    :param tmp_path: Isolated DuckDB, pickle and Parquet paths.
    :param vault_record: API-shaped vault source record.
    :return: ``None``.
    """
    vault = DeriveV3Vault.from_api(vault_record)
    vault.deposit_symbol = "USDC"
    vault.deposit_decimals = 6
    older = DeriveV3VaultPrice.from_api(vault.subaccount_id, {"ts": 1790121600, "share_price": "0.9", "nav": "10800", "total_shares": "12000"})
    newer = DeriveV3VaultPrice.from_api(vault.subaccount_id, {"ts": 1790208000, "share_price": "1.1", "nav": "13200", "total_shares": "12000"})
    db_path = tmp_path / "derive-v3-mainnet-vaults.duckdb"
    metadata_path = tmp_path / "vault-metadata-db.pickle"
    parquet_path = tmp_path / "vault-prices-1h.parquet"
    db = DeriveV3VaultDatabase(db_path)
    try:
        db.store_vault("testnet", vault, [older])
        assert build_raw_prices_dataframe(db).empty
        assert not metadata_path.exists()
        db.store_vault("mainnet", vault, [older, newer])
        merge_into_vault_database(db, metadata_path)
        spec = next(iter(VaultDatabase.read(metadata_path).rows))
        row = VaultDatabase.read(metadata_path).rows[spec]
        assert spec.chain_id == DERIVE_V3_CHAIN_ID
        assert spec.vault_address == make_derive_v3_vault_address(vault.subaccount_id)
        assert row["Denomination"] == "USD"
        assert row["_derive_deposit_asset"]["symbol"] == "USDC"
        assert row["First seen"] == older.timestamp
        assert row["Mgmt fee"] == pytest.approx(0.01)
        assert row["Perf fee"] == pytest.approx(0.10)
        closed_record = db.get_vault_metadata("mainnet").iloc[0].to_dict()
        closed_record["closed"] = True
        _, closed_row = create_derive_v3_vault_row(closed_record)
        assert closed_row["_deposit_permission"] == "permissionless"
        assert closed_row["_deposit_closed_reason"] == "Derive vault is closed"
        closed_record["whitelist_only"] = True
        _, closed_whitelist_row = create_derive_v3_vault_row(closed_record)
        assert closed_whitelist_row["_deposit_permission"] == "whitelisted"
        initial_prices = build_raw_prices_dataframe(db)
        assert len(initial_prices) == len([older, newer])
        assert initial_prices["share_price"].tolist() == [0.9, 1.1]
        VaultHistoricalRead.write_uncleaned_parquet(initial_prices, parquet_path)
        db.con.execute("DELETE FROM vault_prices WHERE network = 'mainnet' AND timestamp = ?", [older.timestamp])
        merge_into_vault_database(db, metadata_path)
        assert VaultDatabase.read(metadata_path).rows[spec]["First seen"] == older.timestamp
    finally:
        db.close()

    steps = post_processing.merge_native_protocols(merge_derive_v3=True, uncleaned_parquet_path=parquet_path, derive_v3_db_path=db_path)
    assert steps == {"derive-v3-price-merge": True}
    prices = post_processing.pd.read_parquet(parquet_path)
    assert len(prices) == len([older, newer])
    assert prices.sort_values("timestamp")["share_price"].tolist() == [0.9, 1.1]
    db = DeriveV3VaultDatabase(db_path)
    try:
        db.con.execute("DELETE FROM vault_prices WHERE network = 'mainnet'")
    finally:
        db.close()
    empty_steps = post_processing.merge_native_protocols(merge_derive_v3=True, uncleaned_parquet_path=parquet_path, derive_v3_db_path=db_path)
    assert empty_steps == {"derive-v3-price-merge": True}
    assert len(post_processing.pd.read_parquet(parquet_path)) == len([older, newer])
    cleaned_path = tmp_path / "cleaned-vault-prices-1h.parquet"
    assert post_processing.clean_prices(vault_db_path=metadata_path, uncleaned_path=parquet_path, cleaned_path=cleaned_path)
    assert make_derive_v3_vault_address(vault.subaccount_id) in post_processing.pd.read_parquet(cleaned_path)["address"].to_numpy()
    output = top_vaults_json.main(
        data_dir=tmp_path,
        vault_db_path=metadata_path,
        parquet_path=cleaned_path,
        output_path=tmp_path / "vaults.json",
        core3_db_path=tmp_path / "missing-core3.duckdb",
        xerberus_db_path=tmp_path / "missing-xerberus.duckdb",
        feed_db_path=tmp_path / "missing-feed.duckdb",
    )
    exported = next(item for item in output["vaults"] if item["address"] == spec.vault_address)
    assert exported["other_data"]["derive"]["deposit_asset"]["symbol"] == "USDC"
    assert exported["other_data"]["derive"]["curator"] == vault.curator
    assert exported["strategy_tags"] is None
    assert exported["curator_slug"] is None
    assert exported["curator_name"] is None
    assert output["curators"] == {}
    assert exported["other_data"].get("perp_dex") is None


def test_v3_curator_attribution_requires_verified_identity() -> None:
    """Do not turn a Derive vault title into a curator record.

    :return: ``None``.
    """
    address = make_derive_v3_vault_address(12345)
    kwargs = {"chain_id": DERIVE_V3_CHAIN_ID, "vault_token_symbol": "Example", "vault_name": "Gauntlet Example", "vault_address": address, "protocol_slug": "derive"}
    assert identify_curator(**kwargs) is None
    assert identify_curator(**kwargs, declared_curator_slug="gauntlet") == "gauntlet"


def test_v3_perp_registration_is_vault_specific(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave non-perpetual Derive rows outside the shared perp account index.

    :param monkeypatch: Add one temporary reviewed strategy classification.
    :return: ``None``.
    """
    tagged = make_derive_v3_vault_address(12345)
    untagged = make_derive_v3_vault_address(12346)
    monkeypatch.setitem(derive_tags.STRATEGY_TAGS, tagged, {StrategyTag.perpetual_futures})
    prices = pd.DataFrame({"chain": [DERIVE_V3_CHAIN_ID, DERIVE_V3_CHAIN_ID], "address": [tagged, untagged], "timestamp": [pd.Timestamp("2026-09-24"), pd.Timestamp("2026-09-24")]})
    registered = build_registered_perp_vault_index(prices)
    assert registered.tolist() == [(DERIVE_V3_CHAIN_ID, tagged)]
    finalised = finalise_perp_metric_columns(prices, registered)
    assert finalised["perp_position_data_status"].tolist() == ["not_collected", "not_applicable"]


def test_v3_all_chain_flag_and_empty_mainnet_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Schedule a mainnet item and leave shared outputs untouched when empty.

    :param tmp_path: Isolated scanner output directory.
    :param monkeypatch: Replace the public client with an empty mainnet source.
    :return: ``None``.
    """
    protocols = scan_all_chains.build_active_protocols(False, False, False, False, False, False, False, scan_derive_v3=True)
    assert "Derive V3" in protocols

    class EmptyClient:
        """Provide an empty mainnet vault listing for the scanner wrapper."""

        def __init__(self, network: str) -> None:
            assert network == "mainnet"
            self.network = network

        def fetch_vaults(self):  # noqa: PLR6301 - fake preserves the production client signature
            """Return no public vaults.

            :return: Empty source iterator.
            """
            return iter(())

        def close(self) -> None:
            """Close the fake client.

            :return: ``None``.
            """

    monkeypatch.setattr(scan_all_chains, "DeriveV3VaultClient", EmptyClient)
    metadata_path = tmp_path / "vault-metadata-db.pickle"
    result = scan_all_chains.scan_derive_v3_fn(tmp_path / "derive.duckdb", metadata_path)
    assert result.status == "success"
    assert result.vault_count == 0
    assert not metadata_path.exists()


def test_v3_perp_observation_requires_vault_tag(tmp_path: Path, vault_record: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Publish unavailable positions only for an evidence-classified vault.

    :param tmp_path: Isolated file-backed observation database.
    :param vault_record: API-shaped vault source record.
    :param monkeypatch: Temporary strategy evidence map.
    :return: ``None``.
    """
    vault = DeriveV3Vault.from_api(vault_record)
    address = make_derive_v3_vault_address(vault.subaccount_id)
    db = DeriveV3VaultDatabase(tmp_path / "derive-v3.duckdb")
    try:
        db.store_vault("mainnet", vault, [])
        assert db.con.execute("SELECT count(*) FROM perp_vault_account_observations").fetchone()[0] == 0
        monkeypatch.setitem(derive_tags.STRATEGY_TAGS, address, {StrategyTag.perpetual_futures})
        db.store_vault("mainnet", vault, [])
        _, metadata_row = create_derive_v3_vault_row(db.get_vault_metadata("mainnet").iloc[0].to_dict())
        assert metadata_row["_strategy_tags"] == {StrategyTag.perpetual_futures}
        assert metadata_row["_flags"] == {VaultFlag.perp_dex_trading_vault}
        row = db.con.execute("SELECT total_equity, position_data_status FROM perp_vault_account_observations").fetchone()
        assert row == (vault.nav_usd, "authentication_required")
        assert db.con.execute("SELECT count(*) FROM perp_vault_position_observations").fetchone()[0] == 0
        db.store_vault("testnet", vault, [])
        assert db.con.execute("SELECT count(*) FROM perp_vault_account_observations").fetchone()[0] == 1
        with pytest.raises(ValueError, match="Only Derive v3 mainnet"):
            create_derive_v3_vault_row(db.get_vault_metadata("testnet").iloc[0].to_dict())
    finally:
        db.close()
