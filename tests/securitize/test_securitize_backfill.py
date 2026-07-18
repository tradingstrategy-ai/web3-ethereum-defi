"""Regression tests for the targeted Securitize backfill helper."""

from types import SimpleNamespace

import pytest

from eth_defi.tokenised_fund.securitize import backfill
from eth_defi.tokenised_fund.securitize.description import ACRED_ETHEREUM, ARCOIN_ETHEREUM, SECURITIZE_PRODUCTS
from eth_defi.tokenised_fund.securitize.redstone import REDSTONE_SECURITIZE_FEEDS

DEPLOYMENT_BLOCK = 100
EXPLICIT_START_BLOCK = 75
EXPECTED_HISTORICAL_ROWS = 2


@pytest.fixture
def backfill_history_module():
    """Return the Securitize backfill module."""

    return backfill


def test_fetch_contract_deployment_block_uses_binary_search(backfill_history_module) -> None:
    """Locate the first block with runtime code without scanning all blocks."""

    deployment_block = 83
    web3 = SimpleNamespace(
        eth=SimpleNamespace(
            get_code=lambda _address, block_identifier: b"code" if block_identifier >= deployment_block else b"",
        )
    )

    assert backfill_history_module.fetch_contract_deployment_block(web3, "0x0000000000000000000000000000000000000001", 100) == deployment_block


def test_default_backfill_includes_complete_product_registry(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Process every reviewed product when no scoped repair filter is set."""

    monkeypatch.delenv("SECURITIZE_PRODUCTS", raising=False)
    products = list(backfill_history_module.iter_products())

    assert {(product.chain_id, product.token) for product in products} == set(SECURITIZE_PRODUCTS)
    estimated_product_count = sum(product.estimated_nav_per_share is not None for product in products)
    assert sum(backfill_history_module.has_historical_price(product) for product in products) == estimated_product_count + len(REDSTONE_SECURITIZE_FEEDS)


def test_create_price_row_report_distinguishes_priced_and_metadata_only(backfill_history_module) -> None:
    """Report non-null price coverage without counting metadata-only products."""

    result = {
        "end_block": REDSTONE_SECURITIZE_FEEDS[ACRED_ETHEREUM.chain_id, ACRED_ETHEREUM.token].first_block,
        "rows_written_by_vault": {ACRED_ETHEREUM.token: EXPECTED_HISTORICAL_ROWS},
        "price_rows_written_by_vault": {ACRED_ETHEREUM.token: 1},
    }

    report = backfill_history_module.create_price_row_report([ACRED_ETHEREUM, ARCOIN_ETHEREUM], result, scan_enabled=True)

    assert report[0]["historical_rows"] == EXPECTED_HISTORICAL_ROWS
    assert report[0]["price_rows"] == 1
    assert report[0]["status"] == "priced"
    assert report[1]["historical_rows"] == 0
    assert report[1]["price_rows"] == 0
    assert report[1]["status"] == "no NAV source"


def test_create_price_row_report_rejects_missing_expected_price(backfill_history_module) -> None:
    """Fail a completed scan when a configured product silently has no price."""

    feed = REDSTONE_SECURITIZE_FEEDS[ACRED_ETHEREUM.chain_id, ACRED_ETHEREUM.token]
    result = {
        "end_block": feed.first_block,
        "rows_written_by_vault": {ACRED_ETHEREUM.token: 1},
        "price_rows_written_by_vault": {},
    }

    with pytest.raises(RuntimeError, match="Apollo Diversified Credit"):
        backfill_history_module.create_price_row_report([ACRED_ETHEREUM], result, scan_enabled=True)


def test_fetch_contract_deployment_block_rejects_non_contract(backfill_history_module) -> None:
    """Reject an address that does not contain runtime code at the scan end."""

    web3 = SimpleNamespace(eth=SimpleNamespace(get_code=lambda *_args, **_kwargs: b""))

    with pytest.raises(ValueError, match="No contract code"):
        backfill_history_module.fetch_contract_deployment_block(web3, "0x0000000000000000000000000000000000000001", 100)


def test_resolve_price_scan_start_block_uses_earliest_deployment(
    monkeypatch: pytest.MonkeyPatch,
    backfill_history_module,
) -> None:
    """Start a targeted rewrite at the earliest selected deployment by default."""

    monkeypatch.delenv("START_BLOCK", raising=False)

    assert backfill_history_module.resolve_price_scan_start_block([200, DEPLOYMENT_BLOCK]) == DEPLOYMENT_BLOCK


def test_resolve_price_scan_start_block_honours_override(
    monkeypatch: pytest.MonkeyPatch,
    backfill_history_module,
) -> None:
    """Allow operators to narrow a diagnostic scan explicitly."""

    monkeypatch.setenv("START_BLOCK", str(EXPLICIT_START_BLOCK))
    assert backfill_history_module.resolve_price_scan_start_block([DEPLOYMENT_BLOCK]) == EXPLICIT_START_BLOCK
