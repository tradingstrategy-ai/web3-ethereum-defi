"""Regression tests for the Asseto historical backfill script helpers."""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.asseto.constants import ASSETO_AOABT_HASHKEY
from eth_defi.asseto.offchain_api import AssetoOffchainProduct
from eth_defi.chain import CHAIN_NAMES
from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase

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
    """Load the hyphenated Asseto backfill script as a Python module."""

    script_path = Path(__file__).parents[2] / "scripts" / "asseto" / "backfill-history.py"
    spec = importlib.util.spec_from_file_location("asseto_backfill_history", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_resolve_price_scan_start_block_uses_asseto_deployment(
    monkeypatch: pytest.MonkeyPatch,
    backfill_history_module,
    tmp_path: Path,
) -> None:
    """Rewrite Asseto history from the registered product deployment by default."""

    monkeypatch.delenv("START_BLOCK", raising=False)

    assert backfill_history_module.resolve_price_scan_start_block([ASSETO_AOABT_HASHKEY], tmp_path) == ASSETO_AOABT_HASHKEY.first_seen_at_block


def test_resolve_price_scan_start_block_clips_to_timestamp_cache(
    monkeypatch: pytest.MonkeyPatch,
    backfill_history_module,
    tmp_path: Path,
) -> None:
    """Do not ask a sparse local timestamp cache for unavailable early blocks."""

    monkeypatch.delenv("START_BLOCK", raising=False)
    first_cached_block = ASSETO_AOABT_HASHKEY.first_seen_at_block + 1_000
    cache = BlockTimestampDatabase.create(ASSETO_AOABT_HASHKEY.chain_id, tmp_path)
    try:
        cache.import_chain_data(
            ASSETO_AOABT_HASHKEY.chain_id,
            pd.Series(data=[1_700_000_000], index=[first_cached_block]),
        )
    finally:
        cache.close()

    assert backfill_history_module.resolve_price_scan_start_block([ASSETO_AOABT_HASHKEY], tmp_path) == first_cached_block


def test_resolve_price_scan_start_block_honours_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
    backfill_history_module,
    tmp_path: Path,
) -> None:
    """Allow operators to run a narrowly scoped diagnostic backfill."""

    monkeypatch.setenv("START_BLOCK", str(EXPLICIT_START_BLOCK))

    assert backfill_history_module.resolve_price_scan_start_block([ASSETO_AOABT_HASHKEY], tmp_path) == EXPLICIT_START_BLOCK


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
