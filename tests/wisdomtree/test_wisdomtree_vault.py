"""Test WisdomTree WTGXX routing, read-only flows and issuer NAV parsing."""

# ruff: noqa: ARG001, ARG002, ARG005, DTZ001, PLC2701, PLR2004, PLR6301, PLW0108

import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.tokenised_fund.wisdomtree import backfill
from eth_defi.tokenised_fund.wisdomtree.constants import ETHEREUM_CHAIN_ID, WTGXX_ETHEREUM
from eth_defi.tokenised_fund.wisdomtree.historical import WisdomTreeVaultHistoricalReader, WisdomTreeVaultReaderState
from eth_defi.tokenised_fund.wisdomtree.nav import WisdomTreeAPIError, WisdomTreeNAVPoint, fetch_wisdomtree_nav_history
from eth_defi.tokenised_fund.wisdomtree.vault import WISDOMTREE_RESTRICTED_FLOW_REASON, WisdomTreeVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.risk import VaultTechnicalRisk
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.fixture
def backfill_module():
    """Return the WisdomTree backfill module."""

    return backfill


def test_wisdomtree_hardcoded_classification_is_chain_aware() -> None:
    """Route the reviewed WTGXX deployment only on Ethereum."""

    assert _get_hardcoded_protocol_features(WTGXX_ETHEREUM.token, chain_id=ETHEREUM_CHAIN_ID) == {ERC4626Feature.wisdomtree_like}
    assert _get_hardcoded_protocol_features(WTGXX_ETHEREUM.token, chain_id=8453) is None


def test_wisdomtree_vault_is_read_only() -> None:
    """Do not advertise an incomplete permissioned lifecycle as public flows."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    vault = create_vault_instance(web3, WTGXX_ETHEREUM.token, features={ERC4626Feature.wisdomtree_like})
    assert isinstance(vault, WisdomTreeVault)
    assert vault.fetch_deposit_closed_reason() == WISDOMTREE_RESTRICTED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == WISDOMTREE_RESTRICTED_FLOW_REASON
    with pytest.raises(NotImplementedError):
        vault.get_deposit_manager()
    assert vault.get_fee_data().fee_mode == VaultFeeMode.internalised_skimming
    assert vault.get_management_fee("latest") == pytest.approx(0.0025)
    assert get_vault_protocol_name({ERC4626Feature.wisdomtree_like}) == "WisdomTree"
    assert vault.get_risk() == VaultTechnicalRisk.dangerous
    assert "Historical NAV data is not publicly available" in vault.get_notes()
    assert "permissioned DataSpan API" in vault.fetch_scan_record_extra_data()["_notes"]


def test_wisdomtree_nav_history_uses_documented_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse current/history wrapper responses and retain chronological order."""

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"history": [{"date": "2026-07-02", "nav": "1.00"}, {"asOfDate": "2026-07-01T00:00:00Z", "netAssetValue": 1}]}

    class Session:
        def get(self, *args, **kwargs):
            assert kwargs["params"] == {"ticker": "WTGXX", "history": "true"}
            assert kwargs["headers"]["x-wt-dataspan-key"] == "test-key"
            return Response()

    points = list(fetch_wisdomtree_nav_history("WTGXX", api_key="test-key", session=Session()))
    assert points == [WisdomTreeNAVPoint(datetime.datetime(2026, 7, 1), Decimal("1")), WisdomTreeNAVPoint(datetime.datetime(2026, 7, 2), Decimal("1.00"))]


def test_wisdomtree_nav_requires_explicit_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never silently substitute a one-dollar estimate for official NAV."""

    monkeypatch.delenv("WISDOMTREE_DATASPAN_API_KEY", raising=False)
    with pytest.raises(WisdomTreeAPIError, match="WISDOMTREE_DATASPAN_API_KEY"):
        list(fetch_wisdomtree_nav_history("WTGXX"))


def test_wisdomtree_historical_total_assets_uses_block_timestamp_nav(monkeypatch: pytest.MonkeyPatch) -> None:
    """Combine historical supply only with the NAV available at that block."""

    first_nav_at = datetime.datetime(2026, 7, 1)
    second_nav_at = datetime.datetime(2026, 7, 2)
    historical_block = 22_900_000

    def fetch_block(block_identifier: int) -> dict[str, int]:
        assert block_identifier == historical_block
        return {"timestamp": int((first_nav_at + datetime.timedelta(hours=12)).replace(tzinfo=datetime.UTC).timestamp())}

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, get_block=fetch_block))
    vault = WisdomTreeVault(web3, VaultSpec(ETHEREUM_CHAIN_ID, WTGXX_ETHEREUM.token))
    vault._nav_history = (
        WisdomTreeNAVPoint(first_nav_at, Decimal("1.01")),
        WisdomTreeNAVPoint(second_nav_at, Decimal("1.25")),
    )
    monkeypatch.setattr(vault, "fetch_total_supply", lambda block_identifier="latest": Decimal("10"))

    assert vault.fetch_share_price(historical_block) == Decimal("1.01")
    assert vault.fetch_total_assets(historical_block) == Decimal("10.10")
    assert vault.fetch_total_assets("latest") == Decimal("12.50")


def test_wisdomtree_migration_preserves_unrelated_reader_state(backfill_module) -> None:
    """Drop only WTGXX state before rebuilding its raw history."""

    other = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    selected = VaultSpec(1, WTGXX_ETHEREUM.token)
    cross_chain_twin = VaultSpec(8453, WTGXX_ETHEREUM.token)
    states = {other: {"keep": True}, selected: {"replace": True}, cross_chain_twin: {"keep_twin": True}}
    assert backfill_module.remove_selected_reader_states(states) == {other: {"keep": True}, cross_chain_twin: {"keep_twin": True}}


def test_wisdomtree_stateful_reader_updates_without_denomination_token() -> None:
    """Persist successful USD NAV observations during stateful backfills."""

    class DummyShareToken:
        @staticmethod
        def convert_to_decimals(raw_amount: int) -> Decimal:
            return Decimal(raw_amount) / Decimal(100)

    timestamp = datetime.datetime(2026, 7, 2, tzinfo=datetime.UTC).replace(tzinfo=None)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=ETHEREUM_CHAIN_ID))
    vault = WisdomTreeVault(web3, VaultSpec(ETHEREUM_CHAIN_ID, WTGXX_ETHEREUM.token))
    vault.first_seen_at_block = 1
    vault.__dict__["share_token"] = DummyShareToken()
    vault.fetch_share_price_at = lambda _timestamp: Decimal("1.25")
    reader = WisdomTreeVaultHistoricalReader(vault, stateful=True)
    call = EncodedCall(func_name="totalSupply", address=WTGXX_ETHEREUM.token, data=b"", extra_data={"function": "totalSupply"})
    result = EncodedCallResult(call=call, success=True, result=(1_000).to_bytes(32, "big"), block_identifier=123)
    result.timestamp = timestamp

    read = reader.process_result(123, timestamp, [result])

    assert read.total_supply == Decimal(10)
    assert read.share_price == Decimal("1.25")
    assert read.total_assets == Decimal("12.50")
    assert isinstance(reader.reader_state, WisdomTreeVaultReaderState)
    assert reader.reader_state.last_block == 123
    assert reader.reader_state.last_tvl == Decimal("12.50")
    assert reader.reader_state.last_share_price == Decimal("1.25")


def test_wisdomtree_migration_cleaning_scope_is_single_vault(backfill_module) -> None:
    """Pass only WTGXX to the cleaned-history replacement helper."""

    assert backfill_module.selected_vault_addresses() == {WTGXX_ETHEREUM.token}
    assert len(backfill_module.selected_vault_spec_ids()) == 1


def test_wisdomtree_backfill_starts_at_deployment(monkeypatch: pytest.MonkeyPatch, backfill_module) -> None:
    """Fetch maximum history unless an operator explicitly narrows the scan."""

    monkeypatch.delenv("START_BLOCK", raising=False)
    assert backfill_module.resolve_start_block() == WTGXX_ETHEREUM.first_seen_at_block
    monkeypatch.setenv("START_BLOCK", "123")
    assert backfill_module.resolve_start_block() == 123


def test_wisdomtree_metadata_upsert_preserves_ethereum_watermark(backfill_module) -> None:
    """A one-token migration cannot claim the full chain has been scanned."""

    database = VaultDatabase(last_scanned_block={1: 12_345, 8453: 99})
    backfill_module.upsert_selected_metadata(database, end_block=99_999, row={"Name": "WTGXX"})
    assert database.last_scanned_block == {1: 12_345, 8453: 99}
    assert VaultSpec(1, WTGXX_ETHEREUM.token) in database.rows


def test_wisdomtree_dry_run_skips_history_writer(monkeypatch: pytest.MonkeyPatch, backfill_module, tmp_path: Path) -> None:
    """Do not invoke raw or cleaned Parquet writers in a dry run."""

    calls: list[str] = []
    monkeypatch.setattr(backfill_module, "require_price_scan_key", lambda: calls.append("api-key-check"))
    monkeypatch.setattr(backfill_module, "read_json_rpc_url", lambda _chain_id: "http://example.invalid")
    monkeypatch.setattr(backfill_module, "create_multi_provider_web3", lambda _url: SimpleNamespace(eth=SimpleNamespace(block_number=99)))
    monkeypatch.setattr(backfill_module, "TokenDiskCache", lambda: object())
    monkeypatch.setattr(backfill_module, "create_vault_scan_record", lambda *args, **kwargs: {})
    monkeypatch.setattr(backfill_module, "scan_historical_prices_to_parquet", lambda *args, **kwargs: calls.append("scanner"))
    monkeypatch.setattr(backfill_module, "replace_cleaned_vault_histories", lambda *args, **kwargs: calls.append("cleaner"))
    backfill_module.run_backfill(dry_run=True, scan_prices=True, clean_prices=True, frequency="1d", vault_db_path=tmp_path / "vaults.pickle", raw_price_path=tmp_path / "raw.parquet", cleaned_price_path=tmp_path / "cleaned.parquet", reader_state_path=tmp_path / "state.pickle")
    assert calls == []
    assert not list(tmp_path.iterdir())


def test_wisdomtree_missing_api_key_fails_before_metadata_write(monkeypatch: pytest.MonkeyPatch, backfill_module, tmp_path: Path) -> None:
    """Validate the private API key before reading or writing vault metadata."""

    calls: list[str] = []

    def fail_api_key_check() -> None:
        calls.append("api-key-check")
        message = "Missing WisdomTree API key"
        raise RuntimeError(message)

    monkeypatch.setattr(backfill_module, "require_price_scan_key", fail_api_key_check)
    monkeypatch.setattr(backfill_module, "read_json_rpc_url", lambda _chain_id: calls.append("rpc-read"))
    with pytest.raises(RuntimeError, match="Missing WisdomTree API key"):
        backfill_module.run_backfill(dry_run=False, scan_prices=True, clean_prices=True, frequency="1d", vault_db_path=tmp_path / "vaults.pickle", raw_price_path=tmp_path / "raw.parquet", cleaned_price_path=tmp_path / "cleaned.parquet", reader_state_path=tmp_path / "state.pickle")
    assert calls == ["api-key-check"]
    assert not list(tmp_path.iterdir())


def test_wisdomtree_metadata_only_does_not_require_api_key(monkeypatch: pytest.MonkeyPatch, backfill_module, tmp_path: Path) -> None:
    """Write public metadata without requiring private DataSpan access."""

    calls: list[str] = []

    class DummyTokenCache:
        def commit(self) -> None:
            calls.append("token-cache-commit")

    monkeypatch.setattr(backfill_module, "require_price_scan_key", lambda: calls.append("api-key-check"))
    monkeypatch.setattr(backfill_module, "read_json_rpc_url", lambda _chain_id: "http://example.invalid")
    monkeypatch.setattr(backfill_module, "create_multi_provider_web3", lambda _url: SimpleNamespace(eth=SimpleNamespace(block_number=99)))
    monkeypatch.setattr(backfill_module, "TokenDiskCache", DummyTokenCache)
    monkeypatch.setattr(backfill_module, "create_vault_scan_record", lambda *args, **kwargs: {"Name": "WisdomTree WTGXX"})

    backfill_module.run_backfill(dry_run=False, scan_prices=False, clean_prices=True, frequency="1d", vault_db_path=tmp_path / "vaults.pickle", raw_price_path=tmp_path / "raw.parquet", cleaned_price_path=tmp_path / "cleaned.parquet", reader_state_path=tmp_path / "state.pickle")

    assert calls == ["token-cache-commit"]
    assert (tmp_path / "vaults.pickle").exists()
    assert not (tmp_path / "raw.parquet").exists()
