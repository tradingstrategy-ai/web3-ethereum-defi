"""No-RPC tests for GMX vault adapter routing."""

import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_utils import to_checksum_address

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import (
    ERC4262VaultDetection,
    ERC4626Feature,
    get_vault_protocol_name,
    passes_price_scan_activity_filter,
)
from eth_defi.gmx.historical_context import GMXHistoricalContextStore
from eth_defi.gmx.historical_oracle import GMXHistoricalSharePriceObservation
from eth_defi.gmx.vault import GMXHistoricalReader, GMXLiquidityVault, GMXMarketVault
from eth_defi.vault.base import DEFAULT_HISTORICAL_SHARE_PRICE_CHANGE_THRESHOLD, VaultHistoricalRead, VaultSpec
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.historical import VaultHistoricalReadMulticaller, scan_historical_prices_to_parquet
from eth_defi.vault.risk import VaultTechnicalRisk

GM_TOKEN = to_checksum_address("0x1000000000000000000000000000000000000001")
GLV_TOKEN = to_checksum_address("0x2000000000000000000000000000000000000002")
LONG_TOKEN = to_checksum_address("0x3000000000000000000000000000000000000001")
SHORT_TOKEN = to_checksum_address("0x3000000000000000000000000000000000000002")


def test_gmx_features_route_to_read_only_adapters() -> None:
    """Reconstruct GM and GLV adapters solely from persisted feature data."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=42161))

    gm = create_vault_instance(web3, GM_TOKEN, {ERC4626Feature.gmx_gm})
    glv = create_vault_instance(web3, GLV_TOKEN, {ERC4626Feature.gmx_glv})

    assert isinstance(gm, GMXMarketVault)
    assert isinstance(glv, GMXLiquidityVault)
    assert ERC4626Feature.share_price_equivalence in gm.features
    assert ERC4626Feature.share_price_equivalence in glv.features
    assert gm.get_historical_reader(stateful=True).uses_contextual_history
    assert glv.get_historical_reader(stateful=True).uses_contextual_history
    assert get_vault_protocol_name({ERC4626Feature.gmx_gm}) == "GMX"
    assert get_vault_protocol_name({ERC4626Feature.gmx_glv}) == "GMX"


def test_gmx_fee_interface_matches_protocol_liquidity_pools() -> None:
    """GMX exposes no manager-level fee, matching pools such as HLP."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=42161))

    for vault_class, token in ((GMXMarketVault, GM_TOKEN), (GMXLiquidityVault, GLV_TOKEN)):
        vault = vault_class(web3, VaultSpec(42161, token))
        assert vault.get_management_fee("latest") == 0.0
        assert vault.get_performance_fee("latest") == 0.0
        assert vault.get_deposit_fee("latest") == 0.0
        assert vault.get_withdraw_fee("latest") == 0.0
        assert not vault.has_custom_fees()

        fee_data = vault.get_fee_data()
        assert fee_data.fee_mode == VaultFeeMode.feeless
        assert fee_data.management == 0.0
        assert fee_data.performance == 0.0
        assert fee_data.deposit == 0.0
        assert fee_data.withdraw == 0.0
        assert fee_data.can_calculate_investor_net_performance()


def test_gmx_is_classified_as_low_technical_risk() -> None:
    """GMX adapters inherit the shared low technical-risk classification."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=42161))
    vault = GMXMarketVault(web3, VaultSpec(42161, GM_TOKEN))

    assert vault.get_risk() == VaultTechnicalRisk.low


def test_gmx_vaults_are_classified_as_market_making_liquidity_provision() -> None:
    """GM and GLV shares fund the GMX liquidity pools that settle trades."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=42161))

    for vault_class, token in ((GMXMarketVault, GM_TOKEN), (GMXLiquidityVault, GLV_TOKEN)):
        vault = vault_class(web3, VaultSpec(42161, token))
        assert {VaultFlag.market_making, VaultFlag.liquidity_provision}.issubset(vault.get_flags())


def test_gmx_vaults_explain_single_sided_usdc_performance_equivalence() -> None:
    """GM and GLV expose the common performance-interpretation note."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=42161))
    expected = "The vault performance approximates the performance of a single-sided USDC deposit."

    for vault_class, token in ((GMXMarketVault, GM_TOKEN), (GMXLiquidityVault, GLV_TOKEN)):
        vault = vault_class(web3, VaultSpec(42161, token))
        assert vault.get_notes() == expected


def test_gmx_change_filter_ignores_deposits_and_retains_share_price_moves() -> None:
    """GMX rows reflect performance changes rather than LP cash flows."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=42161))
    vault = GMXMarketVault(web3, VaultSpec(42161, GM_TOKEN))
    timestamp = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC).replace(tzinfo=None)

    def read(block_number: int, share_price: str, total_supply: str) -> VaultHistoricalRead:
        """Create one deterministic contextual observation."""

        price = Decimal(share_price)
        supply = Decimal(total_supply)
        return VaultHistoricalRead(
            vault=vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=price,
            total_assets=price * supply,
            total_supply=supply,
            performance_fee=None,
            management_fee=None,
            errors=None,
        )

    previous = read(1, "1", "100")
    deposit_only = read(2, "1", "1000")
    below_threshold = read(3, "1.0005", "1000")
    above_threshold = read(4, "1.002", "1000")

    assert DEFAULT_HISTORICAL_SHARE_PRICE_CHANGE_THRESHOLD == pytest.approx(0.001)
    assert deposit_only.is_share_price_almost_equal(previous)
    assert below_threshold.is_share_price_almost_equal(previous)
    assert not above_threshold.is_share_price_almost_equal(previous)
    assert not deposit_only.is_almost_equal(previous)


def test_gmx_reader_uses_supply_normalised_value_event(tmp_path: Path) -> None:
    """A value event avoids dependence on a historical Reader deployment."""

    context_path = tmp_path / "context.duckdb"
    observation = GMXHistoricalSharePriceObservation(
        chain_id=42161,
        block_number=123,
        block_timestamp=1_700_000_005,
        transaction_hash="0xabc",
        log_index=4,
        product_address=GM_TOKEN,
        raw_value=250 * 10**30,
        raw_supply=100 * 10**18,
        event_name="MarketPoolValueInfo",
    )
    with GMXHistoricalContextStore(context_path) as store:
        assert store.insert_share_price(observation)

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=42161))
    vault = GMXMarketVault(web3, VaultSpec(42161, GM_TOKEN))
    vault.first_seen_at_block = 1
    vault.historical_context_path = context_path
    reads = tuple(GMXHistoricalReader(vault).fetch_contextual_historical_reads(1, 200, 100))

    assert len(reads) == 1
    assert reads[0].share_price == Decimal("2.5")
    assert reads[0].total_assets == Decimal(250)
    assert reads[0].total_supply == Decimal(100)


def test_gmx_catalogue_evidence_bypasses_erc4626_deposit_count() -> None:
    """Catalogue-verified GMX products remain eligible with no ERC-4626 events."""

    observed_at = datetime.datetime.fromtimestamp(0, datetime.UTC).replace(tzinfo=None)
    detection = ERC4262VaultDetection(
        chain=42161,
        address=GM_TOKEN,
        first_seen_at_block=1,
        first_seen_at=observed_at,
        features={ERC4626Feature.gmx_gm},
        updated_at=observed_at,
        deposit_count=0,
        redeem_count=0,
    )

    assert passes_price_scan_activity_filter(detection, min_deposit_threshold=2)


def test_empty_address_repair_cannot_broaden_parquet_deletion(tmp_path: Path) -> None:
    """An empty bounded selection fails before opening or deleting Parquet."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=42161))
    with pytest.raises(ValueError, match="cannot be empty"):
        scan_historical_prices_to_parquet(
            output_fname=tmp_path / "prices.parquet",
            web3=web3,
            web3factory=None,
            vaults=[],
            token_cache={},
            start_block=1,
            end_block=2,
            vault_addresses=set(),
        )


def test_stateful_reader_preparation_skips_contextual_reader_state() -> None:
    """A populated ERC-4626 state mapping does not break a GMX reader."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=42161))
    vault = GMXMarketVault(web3, VaultSpec(42161, GM_TOKEN))
    vault.first_seen_at_block = 1
    token_cache = SimpleNamespace(
        load_token_details_with_multicall=lambda **_kwargs: None,
    )
    multicaller = VaultHistoricalReadMulticaller(
        web3factory=SimpleNamespace(),
        supported_quote_tokens=None,
        max_workers=1,
        token_cache=token_cache,
    )
    unrelated_state = {VaultSpec(42161, "0x5000000000000000000000000000000000000001"): {"last_block": 1}}

    readers = multicaller.prepare_readers([vault], stateful=True, saved_states=unrelated_state)

    assert readers[vault.address].reader_state is None
