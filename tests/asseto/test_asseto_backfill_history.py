"""Regression tests for the Asseto historical backfill script helpers."""

import datetime
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn

import pytest

from eth_defi.chain import CHAIN_NAMES
from eth_defi.currency_api.client import DateRates
from eth_defi.currency_api.constants import SOURCE_NAME
from eth_defi.currency_api.database import CurrencyRateDatabase
from eth_defi.tokenised_fund.asseto import backfill
from eth_defi.tokenised_fund.asseto.constants import ASSETO_AOABT_HASHKEY
from eth_defi.tokenised_fund.asseto.offchain_api import AssetoOffchainProduct
from eth_defi.vault.base import VaultSpec

EXPLICIT_START_BLOCK = 123_456


def make_registry_product(chain_id: int, *, symbol: str = "AoABT") -> AssetoOffchainProduct:
    """Create one representative public Asseto registry product.

    :param chain_id:
        EVM chain containing the mocked token.
    :param symbol:
        Product symbol used by filtering tests.
    :return:
        Public registry product fixture.
    """

    return AssetoOffchainProduct(
        product_id=2,
        product_name="AoABT",
        full_name="Asseto test product",
        symbol=symbol,
        product_type="uda",
        chain_id=chain_id,
        chain_name="Ethereum" if chain_id == 1 else "HashKey Chain",
        contract_address=ASSETO_AOABT_HASHKEY.token,
        denomination_symbol="USDT",
        denomination_address=ASSETO_AOABT_HASHKEY.collateral,
        tvl=None,
        apy=None,
        introduction="Test Asseto product",
        protocol=None,
    )


@pytest.fixture
def backfill_history_module():
    """Return the Asseto backfill module."""

    return backfill


def test_unsupported_asseto_chain_is_excluded_from_backfill(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Exclude HashKey until it is supported by project and HyperSync mappings."""

    assert ASSETO_AOABT_HASHKEY.chain_id not in CHAIN_NAMES
    assert not backfill_history_module.is_supported_asseto_chain(ASSETO_AOABT_HASHKEY.chain_id)
    monkeypatch.setattr(backfill_history_module, "fetch_asseto_products", lambda: iter([make_registry_product(177)]))
    assert list(backfill_history_module.iter_selected_products()) == []


def test_supported_asseto_chain_uses_standard_rpc_configuration(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Select a registered HyperSync chain with its normal RPC environment variable."""

    ethereum_product = make_registry_product(1)
    monkeypatch.setattr(backfill_history_module, "fetch_asseto_products", lambda: iter([ethereum_product]))
    monkeypatch.setenv("JSON_RPC_ETHEREUM", "https://ethereum-rpc.example")

    assert backfill_history_module.get_asseto_rpc_env(ethereum_product.chain_id) == "JSON_RPC_ETHEREUM"
    assert list(backfill_history_module.iter_selected_products()) == [ethereum_product]


def test_missing_rpc_excludes_supported_asseto_chain(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Avoid partial backfills when the normal RPC variable is unset."""

    ethereum_product = make_registry_product(1)
    monkeypatch.setattr(backfill_history_module, "fetch_asseto_products", lambda: iter([ethereum_product]))
    monkeypatch.delenv("JSON_RPC_ETHEREUM", raising=False)

    assert list(backfill_history_module.iter_selected_products()) == []


def test_create_runtime_product_uses_registry_price_source(backfill_history_module) -> None:
    """Keep the registry id that drives non-pricer historical NAV reads."""

    registry_product = make_registry_product(1)
    first_seen_at = ASSETO_AOABT_HASHKEY.first_seen_at

    runtime_product = backfill_history_module.create_runtime_product(registry_product, EXPLICIT_START_BLOCK, first_seen_at)

    assert runtime_product.token == registry_product.contract_address
    assert runtime_product.offchain_product_id == registry_product.product_id
    assert runtime_product.offchain_product_name == registry_product.product_name
    assert runtime_product.manager is None
    assert runtime_product.pricer is None
    assert runtime_product.denomination_symbol == "USDT"


def test_stoken_registry_product_uses_synthetic_usd(backfill_history_module) -> None:
    """Map collateral-less Asseto stoken products to their USD accounting unit."""

    registry_product = replace(make_registry_product(1), product_type="stoken", denomination_symbol=None, denomination_address=None)
    runtime_product = backfill_history_module.create_runtime_product(registry_product, EXPLICIT_START_BLOCK, ASSETO_AOABT_HASHKEY.first_seen_at)

    assert backfill_history_module.resolve_asseto_denomination_symbol(registry_product) == "USD"
    assert runtime_product.collateral is None
    assert runtime_product.denomination_symbol == "USD"


def test_build_vaults_skips_product_without_accounting_denomination(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Do not initialise history when Asseto omits all denomination metadata."""

    product_without_collateral = replace(ASSETO_AOABT_HASHKEY, collateral=None, denomination_symbol=None)

    def unexpected_vault_creation(*_args: object, **_kwargs: object) -> NoReturn:
        message = "Products without accounting denominations must be skipped before adapter creation"
        raise AssertionError(message)

    monkeypatch.setattr(backfill_history_module, "create_vault_instance", unexpected_vault_creation)

    assert backfill_history_module.build_vaults(object(), [product_without_collateral], object()) == []


def test_build_vaults_includes_synthetic_usd_product(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Include stoken history even though Asseto publishes no collateral address."""

    product = replace(ASSETO_AOABT_HASHKEY, collateral=None, denomination_symbol="USD", pricer=None)
    vault = SimpleNamespace(
        address=product.token,
        uses_onchain_pricer=lambda: False,
        fetch_offchain_price_history=lambda: (SimpleNamespace(value=Decimal("1")),),
    )
    monkeypatch.setattr(backfill_history_module, "create_vault_instance", lambda *_args, **_kwargs: vault)

    assert backfill_history_module.build_vaults(object(), [product], object()) == [vault]
    assert vault.first_seen_at_block == product.first_seen_at_block


def test_select_cleanable_vault_ids_excludes_unconverted_and_inactive_products(backfill_history_module) -> None:
    """Clean only positive-NAV histories already expressed in USD units."""

    usdc_product = replace(ASSETO_AOABT_HASHKEY, chain_id=1, token="0x4867ad1a74b38b0aeff4fff251ed0dadae4f4630", symbol="CFSRS")
    hkd_product = replace(ASSETO_AOABT_HASHKEY, chain_id=1, token="0x6dc4674573380aff6c3359e19da5cbb6afceb5c3", symbol="CFSAI")
    rows = {
        VaultSpec(usdc_product.chain_id, usdc_product.token): {"Denomination": "USDC", "NAV": Decimal("1")},
        VaultSpec(hkd_product.chain_id, hkd_product.token): {"Denomination": "HKD", "NAV": Decimal("1")},
    }

    assert backfill_history_module.select_cleanable_vault_ids([usdc_product, hkd_product], rows) == {VaultSpec(usdc_product.chain_id, usdc_product.token).as_string_id()}

    rows[VaultSpec(usdc_product.chain_id, usdc_product.token)]["NAV"] = Decimal(0)
    assert backfill_history_module.select_cleanable_vault_ids([usdc_product, hkd_product], rows) == set()


def test_load_usd_exchange_rates_reads_hkd_history(tmp_path: Path, backfill_history_module) -> None:
    """Load the shared currency database in quote-units-per-USD order."""

    database_path = tmp_path / "exchange-rates.duckdb"
    database = CurrencyRateDatabase(database_path)
    try:
        database.upsert_rates(DateRates(date=datetime.date(2026, 7, 18), base_currency="usd", source=SOURCE_NAME, rows=[("hkd", 7.81)]))
    finally:
        database.close()

    rates = backfill_history_module.load_usd_exchange_rates(database_path, ["HKD", "USDC"])

    assert rates == {
        "HKD": (
            (
                int(datetime.datetime(2026, 7, 18, tzinfo=datetime.UTC).timestamp()),
                Decimal("7.81"),
            ),
        )
    }


def test_active_asseto_coverage_requires_live_history(backfill_history_module) -> None:
    """Fail closed when an active registry or positive-supply fund has a gap."""

    registry_product = replace(make_registry_product(1), tvl=Decimal("100"))
    runtime_product = backfill_history_module.create_runtime_product(registry_product, EXPLICIT_START_BLOCK, ASSETO_AOABT_HASHKEY.first_seen_at)
    spec = VaultSpec(runtime_product.chain_id, runtime_product.token)
    rows = {spec: {"Shares": Decimal("100"), "NAV": Decimal("100"), "Denomination": "USD"}}
    active_ids = backfill_history_module.resolve_active_asseto_product_ids([registry_product], [runtime_product], rows)

    assert active_ids == {runtime_product.token.lower()}
    backfill_history_module.validate_active_asseto_coverage(1, active_ids, [runtime_product], rows, active_ids)

    with pytest.raises(RuntimeError, match="missing price history"):
        backfill_history_module.validate_active_asseto_coverage(1, active_ids, [runtime_product], rows, set())

    rows[spec]["Denomination"] = "HKD"
    with pytest.raises(RuntimeError, match="USD-compatible live metadata"):
        backfill_history_module.validate_active_asseto_coverage(1, active_ids, [runtime_product], rows, active_ids)


def test_resolve_price_scan_start_block_uses_asseto_deployment(
    monkeypatch: pytest.MonkeyPatch,
    backfill_history_module,
) -> None:
    """Rewrite Asseto history from the registered product deployment by default."""

    monkeypatch.delenv("START_BLOCK", raising=False)

    assert backfill_history_module.resolve_price_scan_start_block([ASSETO_AOABT_HASHKEY]) == ASSETO_AOABT_HASHKEY.first_seen_at_block


def test_resolve_price_scan_start_block_honours_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
    backfill_history_module,
) -> None:
    """Allow operators to run a narrowly scoped diagnostic backfill."""

    monkeypatch.setenv("START_BLOCK", str(EXPLICIT_START_BLOCK))

    assert backfill_history_module.resolve_price_scan_start_block([ASSETO_AOABT_HASHKEY]) == EXPLICIT_START_BLOCK


def test_iter_selected_products_honours_symbol_filter(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Select a requested product when its chain meets all backfill requirements."""

    ethereum_product = make_registry_product(1)
    monkeypatch.setattr(backfill_history_module, "fetch_asseto_products", lambda: iter([ethereum_product]))
    monkeypatch.setenv("PRODUCTS", "aoabt")
    monkeypatch.setenv("NETWORKS", "ethereum")
    monkeypatch.setenv("JSON_RPC_ETHEREUM", "https://ethereum-rpc.example")

    assert list(backfill_history_module.iter_selected_products()) == [ethereum_product]


def test_resolve_frequency_defaults_to_daily(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Use daily samples unless an operator explicitly selects hourly scans."""

    monkeypatch.delenv("FREQUENCY", raising=False)

    assert backfill_history_module.resolve_frequency() == "1d"


def test_resolve_frequency_rejects_hourly_sampling(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Prevent hourly rows from being manufactured from daily Asseto NAV data."""

    monkeypatch.setenv("FREQUENCY", "1h")

    with pytest.raises(ValueError, match="only daily"):
        backfill_history_module.resolve_frequency()
