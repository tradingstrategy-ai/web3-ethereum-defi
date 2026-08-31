"""Focused no-RPC tests for the YieldBasis vault integration."""

import datetime
import logging
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest
from eth_utils import to_checksum_address
from web3.exceptions import ContractLogicError, Web3Exception

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance  # noqa: PLC2701
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_vault_protocol_name, is_activity_filter_exempt
from eth_defi.middleware import ProbablyNodeHasNoBlock
from eth_defi.research.vault_metrics import slugify_protocol
from eth_defi.vault.base import INSTANT_WITHDRAWAL_PERIOD, VaultSpec
from eth_defi.vault.fee import VaultFeeMode, get_vault_fee_mode
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.historical import scan_historical_prices_to_parquet
from eth_defi.vault.protocol_metadata import build_metadata_json
from eth_defi.vault.risk import VaultTechnicalRisk, get_vault_risk
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.yield_basis import historical_context, vault_catalog, vault_sync
from eth_defi.yield_basis.addresses import YIELD_BASIS_ACTIVE_MARKETS, YIELD_BASIS_STABLECOIN
from eth_defi.yield_basis.historical_context import YieldBasisHistoricalContextStore, YieldBasisHistoricalObservation, fetch_and_store_yield_basis_historical_context
from eth_defi.yield_basis.metrics import estimate_usd_stablecoin_swap_cost, redemption_usd_price_per_share, round_trip_usd_stablecoin_swap_cost, staked_ratio, temporary_redemption_discount, underlying_return, usd_stablecoin_investor_return
from eth_defi.yield_basis.tags import STRATEGY_TAGS
from eth_defi.yield_basis.vault import YieldBasisVault
from eth_defi.yield_basis.vault_catalog import YieldBasisMarket, YieldBasisScanPreparation

YIELD_BASIS_TEST_BLOCK: int = 123


class _Call:
    """Minimal fixed-block Web3 contract-call fake."""

    def __init__(self, result: object) -> None:
        self.result = result

    def call(self, *, block_identifier: int) -> object:
        """Return the configured result or raise its configured error.

        :param block_identifier:
            Historical block expected by this focused fake.
        :return:
            Configured contract-call result.
        """

        assert block_identifier == YIELD_BASIS_TEST_BLOCK
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class _TimestampSlicer(dict[int, datetime.datetime]):
    """Minimal closeable block-timestamp slicer fake."""

    def close(self) -> None:
        """Mirror the production slicer's explicit close operation."""


def test_yield_basis_reviewed_asset_decimals() -> None:
    """Keep redemption diagnostics aligned with deployed token precision.

    :return:
        None.
    """

    assert {market_id: review.asset_decimals for market_id, review in YIELD_BASIS_ACTIVE_MARKETS.items()} == {7: 8, 8: 8, 9: 18, 10: 18}


def test_yield_basis_reviewed_addresses_route_only_on_ethereum(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route reviewed LTs to VaultBase without an ERC-4626 probe."""

    review = YIELD_BASIS_ACTIVE_MARKETS[7]
    assert _get_hardcoded_protocol_features(review.lt_address, 1) == {
        ERC4626Feature.yield_basis_lt,
        ERC4626Feature.amm_pool_like,
        ERC4626Feature.share_price_equivalence,
    }
    assert _get_hardcoded_protocol_features(review.lt_address, 8453) is None
    assert _get_hardcoded_protocol_features(review.lt_address) is None
    monkeypatch.setattr("eth_defi.yield_basis.vault.fetch_yield_basis_lt", lambda _web3, _address: SimpleNamespace(functions=SimpleNamespace()))
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    vault = create_vault_instance(web3, review.lt_address, _get_hardcoded_protocol_features(review.lt_address, 1))
    assert isinstance(vault, YieldBasisVault)
    assert vault.features == {
        ERC4626Feature.yield_basis_lt,
        ERC4626Feature.amm_pool_like,
        ERC4626Feature.share_price_equivalence,
    }
    assert vault.fetch_denomination_token_address.__doc__
    assert vault.is_whitelisted_deposit() is False
    assert "permissionless" in vault.get_whitelist_notes().lower()


def test_yield_basis_classification_and_tags_are_market_making_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """USD denomination must not imply stable-yield or lending strategy."""

    assert get_vault_protocol_name({ERC4626Feature.yield_basis_lt}) == "YieldBasis"
    for review in YIELD_BASIS_ACTIVE_MARKETS.values():
        tags = STRATEGY_TAGS[review.lt_address.lower()]
        assert tags == {
            StrategyTag.market_making,
            StrategyTag.market_making_amm,
            StrategyTag.liquidity_provider,
            StrategyTag.amm,
        }
        assert StrategyTag.lending not in tags
    assert YIELD_BASIS_STABLECOIN.lower() == "0xf939e0a03fb07f59a73314e73794be0e57ac1b4e"

    monkeypatch.setattr("eth_defi.yield_basis.vault.fetch_yield_basis_lt", lambda _web3, _address: SimpleNamespace(functions=SimpleNamespace()))
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    known = YieldBasisVault(web3, VaultSpec(1, YIELD_BASIS_ACTIVE_MARKETS[7].lt_address))
    unknown = YieldBasisVault(web3, VaultSpec(1, "0x0000000000000000000000000000000000000001"))
    assert known.get_strategy_tags() == STRATEGY_TAGS[YIELD_BASIS_ACTIVE_MARKETS[7].lt_address.lower()]
    assert known.get_withdrawal_period() is INSTANT_WITHDRAWAL_PERIOD
    assert unknown.get_strategy_tags() is None
    notes = known.get_notes()
    assert notes is not None
    assert "[trading fees earned by the underlying Curve pool and YieldBasis LEVAMM](https://docs.yieldbasis.com/user/protocol/fee-mechanics)" in notes
    assert "Performance is shown in USD using the marginal amount returned by `preview_withdraw`" in notes
    assert "[Temporary Redemption Discount (TRD)](https://docs.yieldbasis.com/" in notes
    assert "principal-protected stablecoin vault" not in notes
    assert "Returns are not guaranteed" not in notes
    assert "crvUSD can also move away from one US dollar" not in notes
    assert "0.10% conversion" in notes
    assert "outside the historical equity curve" in notes
    assert "price impact" in notes


def test_yield_basis_activity_fee_risk_and_flags() -> None:
    """Expose protocol-wide scanner classifications and fee mode."""

    detection = ERC4262VaultDetection(
        chain=1,
        address=YIELD_BASIS_ACTIVE_MARKETS[7].lt_address.lower(),
        first_seen_at_block=YIELD_BASIS_ACTIVE_MARKETS[7].first_seen_at_block,
        first_seen_at=YIELD_BASIS_ACTIVE_MARKETS[7].first_seen_at,
        features={ERC4626Feature.yield_basis_lt},
        updated_at=datetime.datetime(2026, 8, 27, tzinfo=datetime.UTC).replace(tzinfo=None),
        deposit_count=0,
        redeem_count=0,
    )
    assert is_activity_filter_exempt(detection)
    assert get_vault_fee_mode("YieldBasis", detection.address) is VaultFeeMode.internalised_minting
    assert get_vault_risk("YieldBasis", detection.address) is VaultTechnicalRisk.low
    assert VaultFlag.market_making.value == "market_making"


def test_yield_basis_exposes_fixed_usd_entry_and_exit_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report the same fixed token-based cost in both directions.

    :param monkeypatch:
        Pytest patch fixture used to isolate LT construction without an RPC.
    :return:
        None.
    """

    monkeypatch.setattr("eth_defi.yield_basis.vault.fetch_yield_basis_lt", lambda _web3, _address: SimpleNamespace(functions=SimpleNamespace()))
    vault = YieldBasisVault(SimpleNamespace(eth=SimpleNamespace(chain_id=1)), VaultSpec(1, YIELD_BASIS_ACTIVE_MARKETS[7].lt_address), default_block_identifier=YIELD_BASIS_TEST_BLOCK)

    assert vault.fetch_denomination_token_address(YIELD_BASIS_TEST_BLOCK) is None
    assert vault.fetch_denomination_token() is None
    assert vault.get_deposit_fee(YIELD_BASIS_TEST_BLOCK) == pytest.approx(0.001)
    assert vault.get_withdraw_fee(YIELD_BASIS_TEST_BLOCK) == pytest.approx(0.001)
    fee_data = vault.get_fee_data()
    assert fee_data.fee_mode is VaultFeeMode.internalised_minting
    assert fee_data.management is None
    assert fee_data.performance is None
    assert fee_data.deposit == pytest.approx(0.001)
    assert fee_data.withdraw == pytest.approx(0.001)


def test_yield_basis_zero_supply_has_no_historical_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat a deployed but unseeded LT as an expected empty observation."""

    lt = SimpleNamespace(
        functions=SimpleNamespace(
            pricePerShare=lambda: _Call(10**18),
            updated_balances=lambda: _Call((0, 0)),
        )
    )
    monkeypatch.setattr("eth_defi.yield_basis.vault.fetch_yield_basis_lt", lambda _web3, _address: lt)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    vault = YieldBasisVault(web3, VaultSpec(1, YIELD_BASIS_ACTIVE_MARKETS[7].lt_address))

    assert vault.fetch_historical_observation(YIELD_BASIS_TEST_BLOCK) is None


@pytest.mark.parametrize(
    ("preview_result", "expected_log"),
    (
        (ContractLogicError("pool cannot quote this block"), "preview_withdraw reverted"),
        (0, "preview_withdraw returned zero assets"),
    ),
)
def test_yield_basis_unavailable_historical_preview_leaves_a_logged_gap(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, preview_result: object, expected_log: str) -> None:
    """Skip one deterministic unavailable redemption preview without fallback.

    A contract revert or amount that rounds to zero cannot improve by retrying
    the same historical block. The missing sample must be visible to operators
    and must not silently use fundamental PPS as the primary curve.

    :param monkeypatch:
        Pytest patch fixture used to isolate contract reads.
    :param caplog:
        Captured warning log used to verify observability.
    :param preview_result:
        Revert or zero result returned by the fixed-block contract fake.
    :param expected_log:
        Operator-visible reason expected for the omitted sample.
    :return:
        None.
    """

    review = YIELD_BASIS_ACTIVE_MARKETS[7]
    lt = SimpleNamespace(
        functions=SimpleNamespace(
            ASSET_TOKEN=lambda: _Call(review.asset_address),
            pricePerShare=lambda: _Call(10**18),
            updated_balances=lambda: _Call((10**18, 0)),
            preview_withdraw=lambda _shares: _Call(preview_result),
        )
    )
    curve_pool = SimpleNamespace(functions=SimpleNamespace(price_oracle=lambda: _Call(2 * 10**18)))
    monkeypatch.setattr("eth_defi.yield_basis.vault.fetch_yield_basis_lt", lambda _web3, _address: lt)
    monkeypatch.setattr("eth_defi.yield_basis.vault.fetch_yield_basis_curve_pool", lambda _web3, _address: curve_pool)
    monkeypatch.setattr(YieldBasisVault, "fetch_curve_pool_address", lambda _self, _block_identifier: "0x0000000000000000000000000000000000000001")
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    vault = YieldBasisVault(web3, VaultSpec(1, review.lt_address), default_block_identifier=YIELD_BASIS_TEST_BLOCK)

    with caplog.at_level(logging.WARNING):
        assert vault.fetch_historical_observation(YIELD_BASIS_TEST_BLOCK) is None
    assert expected_log in caplog.text


def test_yield_basis_context_prefill_skips_zero_supply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Complete a threaded prefill when an LT has no effective supply yet."""

    block_timestamp = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    monkeypatch.setattr(
        historical_context,
        "fetch_exact_block_timestamps_using_hypersync_cached",
        lambda **_kwargs: _TimestampSlicer({YIELD_BASIS_TEST_BLOCK: block_timestamp}),
    )
    vault = SimpleNamespace(
        chain_id=1,
        address=YIELD_BASIS_ACTIVE_MARKETS[7].lt_address,
        first_seen_at_block=YIELD_BASIS_TEST_BLOCK,
        fetch_historical_observation=lambda _block_number: None,
    )
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    context_path = tmp_path / "empty-context.duckdb"

    result = fetch_and_store_yield_basis_historical_context(
        web3=web3,
        vaults=(vault,),
        start_block=YIELD_BASIS_TEST_BLOCK,
        end_block=YIELD_BASIS_TEST_BLOCK + 1,
        step=1,
        max_workers=1,
        hypersync_client=object(),
        context_path=context_path,
    )

    assert result.observations_fetched == 0
    assert result.observations_inserted == 0


@pytest.mark.parametrize("error_type", (Web3Exception, ProbablyNodeHasNoBlock))
def test_yield_basis_context_skips_only_predeployment_contract_errors(error_type: type[Exception]) -> None:
    """Propagate Web3 and provider failures after reviewed deployment.

    :param error_type:
        Error surfaced by a direct Web3 call or the fallback provider wrapper.
    """

    def fail_historical_read(_block_number: int) -> None:
        """Simulate unavailable historical contract state."""

        message = "historical state unavailable"
        raise error_type(message)

    vault = SimpleNamespace(
        address=YIELD_BASIS_ACTIVE_MARKETS[7].lt_address,
        first_seen_at_block=YIELD_BASIS_TEST_BLOCK,
        fetch_historical_observation=fail_historical_read,
    )

    assert historical_context._fetch_optional_yield_basis_observation(vault, YIELD_BASIS_TEST_BLOCK - 1, 1_000) is None
    with pytest.raises(error_type, match="historical state unavailable"):
        historical_context._fetch_optional_yield_basis_observation(vault, YIELD_BASIS_TEST_BLOCK, 1_001)


def test_yield_basis_context_omits_predeployment_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid provider retries by never scheduling an LT before deployment."""

    sample_blocks = (YIELD_BASIS_TEST_BLOCK - 1, YIELD_BASIS_TEST_BLOCK)
    timestamps = {block_number: datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC) for block_number in sample_blocks}
    monkeypatch.setattr(historical_context, "fetch_exact_block_timestamps_using_hypersync_cached", lambda **_kwargs: _TimestampSlicer(timestamps))
    called_blocks = []

    def fetch_empty_observation(block_number: int) -> None:
        """Record only state calls that survive deployment filtering."""

        called_blocks.append(block_number)

    vault = SimpleNamespace(
        chain_id=1,
        address=YIELD_BASIS_ACTIVE_MARKETS[7].lt_address,
        first_seen_at_block=YIELD_BASIS_TEST_BLOCK,
        fetch_historical_observation=fetch_empty_observation,
    )
    result = fetch_and_store_yield_basis_historical_context(
        web3=SimpleNamespace(eth=SimpleNamespace(chain_id=1)),
        vaults=(vault,),
        start_block=sample_blocks[0],
        end_block=YIELD_BASIS_TEST_BLOCK + 1,
        step=1,
        max_workers=1,
        hypersync_client=object(),
        context_path=tmp_path / "predeployment-context.duckdb",
        blocks=sample_blocks,
    )

    assert called_blocks == [YIELD_BASIS_TEST_BLOCK]
    assert result.observations_fetched == 0


def test_yield_basis_context_commits_completed_batches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Preserve completed context batches when a later state read fails."""

    review = YIELD_BASIS_ACTIVE_MARKETS[7]
    sample_blocks = (YIELD_BASIS_TEST_BLOCK, YIELD_BASIS_TEST_BLOCK + 1)
    timestamps = {block_number: datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC) for block_number in sample_blocks}
    monkeypatch.setattr(historical_context, "fetch_exact_block_timestamps_using_hypersync_cached", lambda **_kwargs: _TimestampSlicer(timestamps))
    monkeypatch.setattr(historical_context, "YIELD_BASIS_CONTEXT_COMMIT_TASKS", 1)

    def fetch_then_fail(block_number: int) -> dict[str, object]:
        """Return one valid source payload, then simulate an RPC failure."""

        if block_number > YIELD_BASIS_TEST_BLOCK:
            message = "later provider failure"
            raise Web3Exception(message)
        return {
            "lt_address": review.lt_address,
            "asset_address": review.asset_address,
            "asset_decimals": review.asset_decimals,
            "raw_asset_crvusd_price": 2 * 10**18,
            "raw_asset_price_per_share": 10**18,
            "raw_preview_shares": 10**18,
            "raw_redemption_assets": 9 * 10**7,
            "raw_effective_supply": 10**18,
            "raw_staked_supply": 0,
        }

    vault = SimpleNamespace(
        chain_id=1,
        address=review.lt_address,
        first_seen_at_block=YIELD_BASIS_TEST_BLOCK,
        fetch_historical_observation=fetch_then_fail,
    )
    context_path = tmp_path / "resumable-context.duckdb"
    with pytest.raises(Web3Exception, match="later provider failure"):
        fetch_and_store_yield_basis_historical_context(
            web3=SimpleNamespace(eth=SimpleNamespace(chain_id=1)),
            vaults=(vault,),
            start_block=sample_blocks[0],
            end_block=sample_blocks[-1] + 1,
            step=1,
            max_workers=1,
            hypersync_client=object(),
            context_path=context_path,
            blocks=sample_blocks,
        )

    with YieldBasisHistoricalContextStore(context_path) as store:
        assert store.count_observations(chain_id=1, lt_address=review.lt_address) == 1


def test_yield_basis_context_is_idempotent_and_conflict_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Persist raw valuation context without ART constraints or duplicate rows."""

    review = YIELD_BASIS_ACTIVE_MARKETS[7]
    observation = YieldBasisHistoricalObservation(
        chain_id=1,
        block_number=100,
        block_timestamp=1_000,
        lt_address=review.lt_address,
        asset_address=review.asset_address,
        asset_decimals=review.asset_decimals,
        raw_asset_crvusd_price=2 * 10**18,
        raw_asset_price_per_share=10**18,
        raw_preview_shares=10**18,
        raw_redemption_assets=9 * 10**7,
        raw_effective_supply=10**18,
        raw_staked_supply=2 * 10**17,
    )
    context_path = tmp_path / "context.duckdb"
    with YieldBasisHistoricalContextStore(context_path) as store:
        assert store.insert_observations((observation, observation)) == 1
        assert store.insert_observations((observation,)) == 0
        constraints = store.connection.execute(
            "SELECT count(*) FROM duckdb_constraints() WHERE table_name LIKE 'yield_basis_%' AND constraint_type IN ('PRIMARY KEY', 'UNIQUE')",
        ).fetchone()[0]
        assert constraints == 0
        reads = list(store.iter_observations(chain_id=1, lt_address=review.lt_address, start_block=0, end_block=200, step=50))
        assert len(reads) == 1
        assert reads[0].asset_decimals == review.asset_decimals
        assert reads[0].asset_price_per_share == Decimal(1)
        assert reads[0].temporary_redemption_discount == Decimal("-0.1")
        assert reads[0].share_price == Decimal("1.8")
        assert reads[0].staked_ratio == Decimal("0.2")
        with pytest.raises(ValueError, match="context conflict"):
            store.insert_observations((replace(observation, raw_asset_price_per_share=2 * 10**18),))
        with pytest.raises(ValueError, match="context conflict"):
            store.insert_observations((replace(observation, raw_redemption_assets=8 * 10**7),))

    monkeypatch.setattr("eth_defi.yield_basis.vault.fetch_yield_basis_lt", lambda _web3, _address: SimpleNamespace(functions=SimpleNamespace()))
    vault = YieldBasisVault(SimpleNamespace(eth=SimpleNamespace(chain_id=1)), VaultSpec(1, review.lt_address))
    vault.historical_context_path = context_path
    historical_reads = tuple(vault.get_historical_reader(stateful=True).fetch_contextual_historical_reads(0, 200, 50))
    expected_value = Decimal("1.8")
    assert len(historical_reads) == 1
    assert historical_reads[0].share_price == expected_value
    assert historical_reads[0].total_supply == 1
    assert historical_reads[0].total_assets == expected_value


def test_yield_basis_context_removes_only_legacy_rows_without_redemption_inputs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Preserve complete legacy rows and remove rows that cannot model TRD.

    The migration fills asset precision from the immutable reviewed pair. The
    otherwise-complete row remains valid because endpoint conversion cost is
    no longer historical context. The reviewed incomplete-preview row is
    visibly removed for backfill, while an unreviewed incomplete row remains
    untouched.

    :param tmp_path:
        Isolated legacy DuckDB path.
    :param caplog:
        Captured warning log used to verify visible cleanup.
    :return:
        None.
    """

    review = YIELD_BASIS_ACTIVE_MARKETS[7]
    context_path = tmp_path / "legacy-context.duckdb"
    connection = duckdb.connect(str(context_path))
    try:
        connection.execute(
            """
            CREATE TABLE yield_basis_historical_context (
                chain_id UINTEGER NOT NULL,
                block_number UBIGINT NOT NULL,
                block_timestamp UBIGINT NOT NULL,
                lt_address VARCHAR NOT NULL,
                asset_address VARCHAR NOT NULL,
                raw_asset_crvusd_price VARCHAR NOT NULL,
                raw_asset_price_per_share VARCHAR NOT NULL,
                raw_preview_shares VARCHAR,
                raw_redemption_assets VARCHAR,
                redemption_missing_reason VARCHAR,
                raw_effective_supply VARCHAR NOT NULL,
                raw_staked_supply VARCHAR NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO yield_basis_historical_context VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 100, 1_000, review.lt_address.lower(), review.asset_address.lower(), str(2 * 10**18), str(10**18), str(10**18), str(9 * 10**7), None, str(10**18), "0"),
        )
        connection.execute(
            "INSERT INTO yield_basis_historical_context VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 150, 1_500, review.lt_address.lower(), review.asset_address.lower(), str(2 * 10**18), str(10**18), None, None, "ContractLogicError", str(10**18), "0"),
        )
        # A removed or not-yet-reviewed market must remain preserved without
        # preventing the supported products from opening their shared store.
        connection.execute(
            "INSERT INTO yield_basis_historical_context VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 175, 1_750, "0x0000000000000000000000000000000000000001", "0x0000000000000000000000000000000000000002", str(2 * 10**18), str(10**18), None, None, "legacy unreviewed product", str(10**18), "0"),
        )
    finally:
        connection.close()

    replacement = YieldBasisHistoricalObservation(
        chain_id=1,
        block_number=150,
        block_timestamp=1_500,
        lt_address=review.lt_address,
        asset_address=review.asset_address,
        asset_decimals=review.asset_decimals,
        raw_asset_crvusd_price=2 * 10**18,
        raw_asset_price_per_share=10**18,
        raw_preview_shares=10**18,
        raw_redemption_assets=9 * 10**7,
        raw_effective_supply=10**18,
        raw_staked_supply=0,
    )
    with caplog.at_level(logging.WARNING), YieldBasisHistoricalContextStore(context_path) as store:
        assert store.count_observations(chain_id=1, lt_address=review.lt_address) == 1
        unreviewed_row = store.connection.execute(
            "SELECT count(*), max(asset_decimals) FROM yield_basis_historical_context WHERE lt_address = ?",
            ("0x0000000000000000000000000000000000000001",),
        ).fetchone()
        assert store.insert_observations((replacement,)) == 1
        migrated = tuple(store.iter_observations(chain_id=1, lt_address=review.lt_address, start_block=0, end_block=200, step=50))

    assert "Removing 1 legacy YieldBasis context rows" in caplog.text
    assert unreviewed_row == (1, None)
    assert tuple(row.block_number for row in migrated) == (100, 150)
    assert all(row.asset_decimals == review.asset_decimals for row in migrated)
    assert all(row.share_price == Decimal("1.8") for row in migrated)


def test_yield_basis_context_uses_latest_observation_in_each_half_open_bucket(tmp_path: Path) -> None:
    """Select the newest row per bucket and exclude the end boundary.

    :param tmp_path:
        Isolated context database path.
    :return:
        None.
    """

    review = YIELD_BASIS_ACTIVE_MARKETS[7]
    observation = YieldBasisHistoricalObservation(
        chain_id=1,
        block_number=100,
        block_timestamp=1_000,
        lt_address=review.lt_address,
        asset_address=review.asset_address,
        asset_decimals=review.asset_decimals,
        raw_asset_crvusd_price=2 * 10**18,
        raw_asset_price_per_share=10**18,
        raw_preview_shares=10**18,
        raw_redemption_assets=9 * 10**7,
        raw_effective_supply=10**18,
        raw_staked_supply=0,
    )
    observations = tuple(replace(observation, block_number=block_number, block_timestamp=block_number * 10) for block_number in (100, 149, 150, 200))
    with YieldBasisHistoricalContextStore(tmp_path / "bucket-context.duckdb") as store:
        assert store.insert_observations(observations) == len(observations)
        bounded = tuple(store.iter_observations(chain_id=1, lt_address=review.lt_address, start_block=100, end_block=200, step=50))
        with_end_bucket = tuple(store.iter_observations(chain_id=1, lt_address=review.lt_address, start_block=100, end_block=201, step=50))

    assert tuple(row.block_number for row in bounded) == (149, 150)
    assert tuple(row.block_number for row in with_end_bucket) == (149, 150, 200)


def test_common_parquet_writer_consumes_yield_basis_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the contextual adapter through the actual common writer."""

    review = YIELD_BASIS_ACTIVE_MARKETS[7]
    context_path = tmp_path / "writer-context.duckdb"
    observation = YieldBasisHistoricalObservation(
        chain_id=1,
        block_number=100,
        block_timestamp=1_000,
        lt_address=review.lt_address,
        asset_address=review.asset_address,
        asset_decimals=review.asset_decimals,
        raw_asset_crvusd_price=2 * 10**18,
        raw_asset_price_per_share=10**18,
        raw_preview_shares=10**18,
        raw_redemption_assets=9 * 10**7,
        raw_effective_supply=10**18,
        raw_staked_supply=0,
    )
    with YieldBasisHistoricalContextStore(context_path) as store:
        assert store.insert_observations((observation,)) == 1

    monkeypatch.setattr("eth_defi.yield_basis.vault.fetch_yield_basis_lt", lambda _web3, _address: SimpleNamespace(functions=SimpleNamespace()))
    vault = YieldBasisVault(SimpleNamespace(eth=SimpleNamespace(chain_id=1)), VaultSpec(1, review.lt_address))
    vault.first_seen_at_block = 1
    vault.historical_context_path = context_path
    monkeypatch.setattr(vault, "fetch_denomination_token_address", lambda _block_identifier="latest": None)
    monkeypatch.setattr(vault, "fetch_share_token_address", lambda _block_identifier="latest": review.lt_address)
    token_cache = SimpleNamespace(
        filename=tmp_path / "token-cache.sqlite",
        load_token_details_with_multicall=lambda **_kwargs: None,
    )
    output_path = tmp_path / "yield-basis.parquet"

    result = scan_historical_prices_to_parquet(
        output_fname=output_path,
        web3=SimpleNamespace(eth=SimpleNamespace(chain_id=1)),
        web3factory=object(),
        vaults=[vault],
        token_cache=token_cache,
        start_block=1,
        end_block=200,
        step=50,
        max_workers=1,
        vault_addresses={review.lt_address.lower()},
    )

    frame = pd.read_parquet(output_path)
    assert result["rows_written"] == 1
    assert len(frame) == 1
    assert frame.iloc[0]["address"] == review.lt_address.lower()
    assert Decimal(str(frame.iloc[0]["share_price"])) == Decimal("1.8")
    assert Decimal(str(frame.iloc[0]["total_assets"])) == Decimal("1.8")


def test_yield_basis_pre_scan_validates_reviewed_factory_products(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enumerate all reviewed products and surface a newer market for review."""

    component_addresses = {
        market_id: {
            "cryptopool": to_checksum_address(f"0x{market_id + 100:040x}"),
            "amm": to_checksum_address(f"0x{market_id + 200:040x}"),
            "price_oracle": to_checksum_address(f"0x{market_id + 300:040x}"),
            "virtual_pool": to_checksum_address(f"0x{market_id + 400:040x}"),
            "staker": to_checksum_address(f"0x{market_id + 500:040x}"),
        }
        for market_id in YIELD_BASIS_ACTIVE_MARKETS
    }
    market_count = {"value": len(YIELD_BASIS_ACTIVE_MARKETS) + min(YIELD_BASIS_ACTIVE_MARKETS)}
    factory = SimpleNamespace(
        functions=SimpleNamespace(
            STABLECOIN=lambda: _Call(YIELD_BASIS_STABLECOIN),
            market_count=lambda: _Call(market_count["value"]),
            markets=lambda market_id: _Call(
                (
                    YIELD_BASIS_ACTIVE_MARKETS[market_id].asset_address,
                    component_addresses[market_id]["cryptopool"],
                    component_addresses[market_id]["amm"],
                    YIELD_BASIS_ACTIVE_MARKETS[market_id].lt_address,
                    component_addresses[market_id]["price_oracle"],
                    component_addresses[market_id]["virtual_pool"],
                    component_addresses[market_id]["staker"],
                )
            ),
        )
    )
    lt_by_address = {}
    curve_by_address = {}
    amm_by_address = {}
    for market_id, review in YIELD_BASIS_ACTIVE_MARKETS.items():
        components = component_addresses[market_id]
        lt_by_address[review.lt_address.lower()] = SimpleNamespace(
            functions=SimpleNamespace(
                ASSET_TOKEN=lambda review=review: _Call(review.asset_address),
                STABLECOIN=lambda: _Call(YIELD_BASIS_STABLECOIN),
                CRYPTOPOOL=lambda components=components: _Call(components["cryptopool"]),
                amm=lambda components=components: _Call(components["amm"]),
            )
        )
        curve_by_address[components["cryptopool"].lower()] = SimpleNamespace(
            functions=SimpleNamespace(
                coins=lambda coin_id, review=review: _Call(YIELD_BASIS_STABLECOIN if coin_id == 0 else review.asset_address),
                price_oracle=lambda: _Call(10**18),
            )
        )
        amm_by_address[components["amm"].lower()] = SimpleNamespace(
            functions=SimpleNamespace(
                STABLECOIN=lambda: _Call(YIELD_BASIS_STABLECOIN),
                COLLATERAL=lambda components=components: _Call(components["cryptopool"]),
                LT_CONTRACT=lambda review=review: _Call(review.lt_address),
                is_killed=lambda: _Call(False),
            )
        )

    monkeypatch.setattr(vault_catalog, "fetch_yield_basis_factory", lambda _web3: factory)
    monkeypatch.setattr(vault_catalog, "fetch_yield_basis_lt", lambda _web3, address: lt_by_address[address.lower()])
    monkeypatch.setattr(vault_catalog, "fetch_yield_basis_curve_pool", lambda _web3, address: curve_by_address[address.lower()])
    monkeypatch.setattr(vault_catalog, "fetch_yield_basis_amm", lambda _web3, address: amm_by_address[address.lower()])
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, get_code=lambda _address, **_kwargs: b"\x01"))

    preparation = vault_catalog.fetch_yield_basis_scan_preparation(web3, YIELD_BASIS_TEST_BLOCK)
    assert preparation.review_required == ()
    assert {product.market_id for product in preparation.products} == set(YIELD_BASIS_ACTIVE_MARKETS)

    market_count["value"] += 1
    expanded = vault_catalog.fetch_yield_basis_scan_preparation(web3, YIELD_BASIS_TEST_BLOCK)
    assert expanded.products == preparation.products
    assert expanded.review_required == ("unreviewed YieldBasis market IDs present: 11-11",)


def test_yield_basis_catalogue_sync_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refresh catalogue fields safely when generic metadata reads later fail."""

    review = YIELD_BASIS_ACTIVE_MARKETS[7]
    product = YieldBasisMarket(
        review=review,
        cryptopool=to_checksum_address("0x1000000000000000000000000000000000000001"),
        amm=to_checksum_address("0x2000000000000000000000000000000000000001"),
        killed=False,
    )
    preparation = YieldBasisScanPreparation(1, YIELD_BASIS_TEST_BLOCK, True, YIELD_BASIS_STABLECOIN, (product,), ())
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, get_block=lambda _block_number: {"timestamp": 1_700_000_000}))
    broken_scan = {"value": False}

    def create_scan_record(_web3: object, detection: ERC4262VaultDetection, _block_number: int, _token_cache: object) -> dict[str, object]:
        """Return healthy metadata first and a broken marker on demand."""

        return {
            "_detection_data": detection,
            "features": set(detection.features),
            "Name": "<broken: temporary RPC failure>" if broken_scan["value"] else "YieldBasis source name",
            "Protocol": "<unknown>",
            "Denomination": None,
        }

    monkeypatch.setattr(vault_sync, "create_vault_scan_record", create_scan_record)
    database = vault_sync.VaultDatabase()

    first = vault_sync.fetch_and_sync_yield_basis_vault_catalogue(web3=web3, vault_db=database, token_cache={}, preparation=preparation)
    second = vault_sync.fetch_and_sync_yield_basis_vault_catalogue(web3=web3, vault_db=database, token_cache={}, preparation=preparation)
    database.rows[VaultSpec(1, review.lt_address)]["Name"] = "Stale generic candidate name"
    broken_scan["value"] = True
    recovered = vault_sync.fetch_and_sync_yield_basis_vault_catalogue(web3=web3, vault_db=database, token_cache={}, preparation=preparation)
    row = database.rows[VaultSpec(1, review.lt_address)]

    assert first.inserted == 1
    assert second.inserted == 0
    assert second.updated == 1
    assert recovered.updated == 1
    assert row["Name"] == "yb-LP WBTC · market 7"
    assert row["Denomination"] == "USD"
    assert row["_synthetic_usd_denomination"] is True
    assert row["_detection_data"].first_seen_at_block == review.first_seen_at_block
    assert row["_detection_data"].features == {
        ERC4626Feature.yield_basis_lt,
        ERC4626Feature.amm_pool_like,
        ERC4626Feature.share_price_equivalence,
    }

    empty_database = vault_sync.VaultDatabase()
    withheld = vault_sync.fetch_and_sync_yield_basis_vault_catalogue(web3=web3, vault_db=empty_database, token_cache={}, preparation=preparation)
    assert withheld.products == 0
    assert withheld.inserted == 0
    assert not empty_database.rows
    assert "metadata read returned" in withheld.review_required[-1]


def test_yield_basis_protocol_metadata() -> None:
    """Render public metadata under the canonical exported protocol slug."""

    repository_root = Path(__file__).resolve().parents[2]
    metadata = build_metadata_json(repository_root / "eth_defi/data/vaults/metadata/yieldbasis.yaml", "https://example.invalid")
    assert metadata["name"] == "YieldBasis"
    assert metadata["slug"] == slugify_protocol(metadata["name"]) == "yieldbasis"
    expected_short_description = "YieldBasis is an AMM protocol that aims to retain the equivalent performance of holding the underlying asset."
    assert metadata["short_description"].replace("\n", " ") == expected_short_description
    assert metadata["logos"] == {
        "generic": "https://example.invalid/vault-protocol-metadata/yieldbasis/generic.png",
        "dark": "https://example.invalid/vault-protocol-metadata/yieldbasis/dark.png",
        "light": "https://example.invalid/vault-protocol-metadata/yieldbasis/light.png",
    }
    assert metadata["links"]["defillama"] == "https://defillama.com/protocol/yield-basis"
    assert "BTC or ETH prices" in metadata["long_description"]
    assert "Temporary Redemption Discount" in metadata["long_description"]


def test_yield_basis_underlying_and_redemption_metrics() -> None:
    """Separate underlying PPS, gross USD and endpoint-cost performance."""

    scale = 10**18
    assert underlying_return(scale, scale) == Decimal("0")
    assert redemption_usd_price_per_share(scale, 90_000_000, 2 * scale, asset_decimals=8) == Decimal("1.8")
    assert redemption_usd_price_per_share(scale, 9 * 10**17, 2 * scale, asset_decimals=18) == Decimal("1.8")
    assert temporary_redemption_discount(scale, 90_000_000, scale, asset_decimals=8) == Decimal("-0.1")
    assert temporary_redemption_discount(scale, 9 * 10**17, scale, asset_decimals=18) == Decimal("-0.1")
    assert staked_ratio(10 * scale, 2 * scale) == Decimal("0.2")
    underlying_token = YIELD_BASIS_ACTIVE_MARKETS[7].asset_address
    assert estimate_usd_stablecoin_swap_cost(underlying_token) == pytest.approx(0.001)
    assert round_trip_usd_stablecoin_swap_cost(underlying_token) == Decimal("0.001999")
    assert usd_stablecoin_investor_return(Decimal(1), Decimal(1), underlying_token) == Decimal("-0.001999")
    assert usd_stablecoin_investor_return(Decimal(1), Decimal("0.96"), underlying_token) == Decimal("-0.04191904")
    with pytest.raises(ValueError, match="outside their valid range"):
        redemption_usd_price_per_share(scale, 0, 2 * scale, asset_decimals=18)
