"""Tests for recurring tokenised-fund price-feed scheduling."""

import datetime
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eth_defi.tokenised_fund import price_backfill
from eth_defi.tokenised_fund.backfill import PROTOCOL_BACKFILLS
from eth_defi.tokenised_fund.price_backfill import TokenisedFundPriceBackfillConfig, build_price_backfill_plan, parse_vault_addresses, run_price_backfill
from eth_defi.tokenised_fund.scan import (
    TOKENISED_FUND_PRICE_CAPABILITIES,
    TOKENISED_FUND_PRICE_SCANNERS,
    TokenisedFundPriceScanContext,
    TokenisedFundPriceScanResult,
    load_tokenised_fund_target_specs,
    resolve_target_start_blocks,
    run_tokenised_fund_price_scan,
    select_tokenised_fund_price_scanners,
)
from eth_defi.vault import scan_all_chains
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

EXPECTED_SCANNER_COUNT = 12
EXPECTED_PRICE_ROWS = 2
BCAP_REDSTONE_FIRST_BLOCK = 25_494_164
EXISTING_PRICE_BLOCK = 25_500_000
NEW_VAULT_FIRST_BLOCK = 50


def test_tokenised_fund_price_manifest_is_complete() -> None:
    """Every aggregate backfill selector has one reviewed price capability."""

    assert set(TOKENISED_FUND_PRICE_CAPABILITIES) == set(PROTOCOL_BACKFILLS)
    assert len(TOKENISED_FUND_PRICE_SCANNERS) == EXPECTED_SCANNER_COUNT
    assert [spec.dashboard_name for spec in TOKENISED_FUND_PRICE_SCANNERS] == [
        "Asseto",
        "Franklin",
        "Libeara",
        "Midas",
        "Ondo",
        "OpenEden",
        "Securitize",
        "Spiko",
        "Superstate",
        "Sygnum",
        "USYC",
        "WisdomTree",
    ]
    assert TOKENISED_FUND_PRICE_CAPABILITIES["kinexys"] != "scheduled"
    assert TOKENISED_FUND_PRICE_CAPABILITIES["centrifuge"] != "scheduled"


def test_tokenised_fund_selection_is_stable_and_validated() -> None:
    """A focused operator selection preserves dashboard ordering."""

    selected = select_tokenised_fund_price_scanners("securitize,asseto")

    assert [spec.selector for spec in selected] == ["asseto", "securitize"]
    with pytest.raises(ValueError, match="Unknown TOKENISED_FUND_PROTOCOLS selectors"):
        select_tokenised_fund_price_scanners("securitize,unknown")


def test_tokenised_fund_price_feeds_have_daily_default_cycles() -> None:
    """Dedicated NAV feeds remain daily when the generic cycle is shorter."""

    overrides = scan_all_chains.ensure_default_scan_cycles({})
    _, due_protocols = scan_all_chains.get_due_items(
        chain_configs=[],
        native_protocols=[spec.dashboard_name for spec in TOKENISED_FUND_PRICE_SCANNERS],
        cycle_overrides=overrides,
        default_cycle=datetime.timedelta(hours=1),
        state={spec.dashboard_name: (scan_all_chains.native_datetime_utc_now() - datetime.timedelta(hours=23)).isoformat() for spec in TOKENISED_FUND_PRICE_SCANNERS},
    )

    assert due_protocols == []


def test_missing_tokenised_fund_history_bootstraps_from_reviewed_deployment(tmp_path: Path) -> None:
    """A feed without raw price rows starts at its earliest vault block."""

    scanner = select_tokenised_fund_price_scanners("asseto")[0]
    target = VaultSpec(1, "0x1111111111111111111111111111111111111111")

    assert resolve_target_start_blocks(
        scanner,
        tmp_path / "missing.parquet",
        targets=[(target, BCAP_REDSTONE_FIRST_BLOCK)],
    ) == {target: BCAP_REDSTONE_FIRST_BLOCK}


def test_existing_tokenised_fund_rows_bound_start_block(tmp_path: Path) -> None:
    """Existing price history resumes from its latest priced block."""

    scanner = select_tokenised_fund_price_scanners("securitize")[0]
    target = VaultSpec(1, "0x1111111111111111111111111111111111111111")
    raw_price_path = tmp_path / "prices.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "chain": 1,
                    "address": target.vault_address,
                    "block_number": EXISTING_PRICE_BLOCK,
                    "timestamp": datetime.datetime.fromisoformat("2026-07-31T00:00:00"),
                    "share_price": 1.0,
                },
                {
                    "chain": 1,
                    "address": target.vault_address,
                    "block_number": EXISTING_PRICE_BLOCK + 1_000,
                    "timestamp": datetime.datetime.fromisoformat("2026-08-01T00:00:00"),
                    "share_price": float("nan"),
                },
            ]
        ),
        raw_price_path,
    )

    assert resolve_target_start_blocks(scanner, raw_price_path, targets=[(target, BCAP_REDSTONE_FIRST_BLOCK)]) == {target: EXISTING_PRICE_BLOCK}


def test_tokenised_fund_targets_keep_independent_start_blocks(tmp_path: Path) -> None:
    """A new product cannot widen another product's rewrite window."""

    scanner = select_tokenised_fund_price_scanners("securitize")[0]
    existing_target = VaultSpec(1, "0x1111111111111111111111111111111111111111")
    new_target = VaultSpec(1, "0x2222222222222222222222222222222222222222")
    raw_price_path = tmp_path / "prices.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "chain": 1,
                    "address": existing_target.vault_address,
                    "block_number": EXISTING_PRICE_BLOCK,
                    "timestamp": datetime.datetime.fromisoformat("2026-07-31T00:00:00"),
                    "share_price": 1.0,
                }
            ]
        ),
        raw_price_path,
    )

    starts = resolve_target_start_blocks(
        scanner,
        raw_price_path,
        [(existing_target, BCAP_REDSTONE_FIRST_BLOCK), (new_target, NEW_VAULT_FIRST_BLOCK)],
    )

    assert starts == {existing_target: EXISTING_PRICE_BLOCK, new_target: NEW_VAULT_FIRST_BLOCK}


def test_recurring_price_backfill_plan_uses_address_scoped_continuation(tmp_path: Path) -> None:
    """The manual entrypoint reports the recurring scanner's exact start block."""

    scanner = select_tokenised_fund_price_scanners("asseto")[0]
    target = VaultSpec(1, "0x1111111111111111111111111111111111111111")
    vault_db_path = tmp_path / "vaults.pickle"
    raw_price_path = tmp_path / "prices.parquet"
    VaultDatabase(
        rows={
            target: {"_detection_data": SimpleNamespace(features={scanner.feature}, first_seen_at_block=BCAP_REDSTONE_FIRST_BLOCK)},
        }
    ).write(vault_db_path)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "chain": target.chain_id,
                    "address": target.vault_address,
                    "block_number": EXISTING_PRICE_BLOCK,
                    "timestamp": datetime.datetime.fromisoformat("2026-07-31T00:00:00"),
                    "share_price": 1.0,
                }
            ]
        ),
        raw_price_path,
    )

    plan = build_price_backfill_plan(
        TokenisedFundPriceBackfillConfig(
            vault_db_path=vault_db_path,
            raw_price_path=raw_price_path,
            scanners=(scanner,),
            vault_addresses=frozenset({target.vault_address}),
            max_workers=1,
            hypersync_concurrency=1,
            dry_run=True,
        )
    )

    assert plan == [{"protocol": "Asseto", "target": target.as_string_id(), "start_block": EXISTING_PRICE_BLOCK}]


def test_recurring_price_backfill_passes_the_exact_address_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The manual price backfill delegates the selected target to the shared scanner."""

    scanner = select_tokenised_fund_price_scanners("asseto")[0]
    target = VaultSpec(1, "0x1111111111111111111111111111111111111111")
    vault_db_path = tmp_path / "vaults.pickle"
    VaultDatabase(
        rows={
            target: {"_detection_data": SimpleNamespace(features={scanner.feature}, first_seen_at_block=BCAP_REDSTONE_FIRST_BLOCK)},
        }
    ).write(vault_db_path)
    captured_contexts = []
    monkeypatch.setattr(price_backfill, "read_json_rpc_url", lambda _chain_id: "https://example.invalid")
    monkeypatch.setattr(
        price_backfill,
        "run_tokenised_fund_price_scan",
        lambda _scanner, context: captured_contexts.append(context) or TokenisedFundPriceScanResult(1, 1, None, BCAP_REDSTONE_FIRST_BLOCK, EXISTING_PRICE_BLOCK),
    )

    results = run_price_backfill(
        TokenisedFundPriceBackfillConfig(
            vault_db_path=vault_db_path,
            raw_price_path=tmp_path / "prices.parquet",
            scanners=(scanner,),
            vault_addresses=frozenset({target.vault_address}),
            max_workers=1,
            hypersync_concurrency=1,
            dry_run=False,
        )
    )

    assert results["Asseto"].price_rows == 1
    assert captured_contexts[0].vault_addresses == frozenset({target.vault_address})


def test_recurring_price_backfill_validates_product_addresses() -> None:
    """Malformed targeted-backfill address filters fail before any scan work."""

    assert parse_vault_addresses("0x1111111111111111111111111111111111111111") == frozenset({"0x1111111111111111111111111111111111111111"})
    with pytest.raises(ValueError, match="invalid EVM addresses"):
        parse_vault_addresses("not-an-address")


def test_only_ready_exact_products_leave_generic_scanner_ownership(tmp_path: Path) -> None:
    """Unselected protocol products are not excluded from the generic scan."""

    asseto = select_tokenised_fund_price_scanners("asseto")[0]
    franklin = select_tokenised_fund_price_scanners("franklin")[0]
    asseto_target = VaultSpec(1, "0x1111111111111111111111111111111111111111")
    franklin_target = VaultSpec(1, "0x2222222222222222222222222222222222222222")
    vault_db_path = tmp_path / "vaults.pickle"
    VaultDatabase(
        rows={
            asseto_target: {"_detection_data": SimpleNamespace(features={asseto.feature})},
            franklin_target: {"_detection_data": SimpleNamespace(features={franklin.feature})},
        }
    ).write(vault_db_path)

    assert load_tokenised_fund_target_specs(vault_db_path, (asseto,)) == {asseto_target}


def test_missing_registered_targets_are_a_scheduled_noop(tmp_path: Path) -> None:
    """Missing metadata targets return diagnostics instead of failing each tick."""

    scanner = select_tokenised_fund_price_scanners("securitize")[0]
    vault_db_path = tmp_path / "vaults.pickle"
    VaultDatabase().write(vault_db_path)

    result = run_tokenised_fund_price_scan(
        scanner,
        TokenisedFundPriceScanContext(
            vault_db_path=vault_db_path,
            raw_price_path=tmp_path / "prices.parquet",
            max_workers=1,
            enabled_chain_ids=frozenset({1}),
        ),
    )

    assert result.vault_count == 0
    assert result.price_rows == 0
    assert result.diagnostics == "no registered price-capable products"


def test_build_active_protocols_includes_selected_tokenised_fund_feeds() -> None:
    """The regular cycle scheduler gives every selected feed its own row."""

    selected = select_tokenised_fund_price_scanners("securitize,asseto")
    protocols = scan_all_chains.build_active_protocols(
        scan_hypercore=False,
        scan_grvt=False,
        scan_lighter=False,
        scan_hibachi=False,
        scan_apex=False,
        scan_core3=False,
        scan_currency_rates=False,
        tokenised_fund_scanners=selected,
    )

    assert protocols == ["Asseto", "Securitize"]


def test_tokenised_fund_tick_updates_only_successful_protocol_cycle_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One successful protocol feed advances only its own cycle-state key."""

    successful_items: list[str] = []
    scanner = select_tokenised_fund_price_scanners("securitize")[0]
    monkeypatch.setattr(
        scan_all_chains,
        "run_tokenised_fund_price_scan",
        lambda *_args, **_kwargs: TokenisedFundPriceScanResult(
            vault_count=1,
            price_rows=2,
            latest_data_timestamp="2026-08-01T00:00:00",
            start_block=25_494_164,
            end_block=25_500_000,
        ),
    )
    monkeypatch.setattr(scan_all_chains, "print_dashboard", lambda *_args, **_kwargs: None)

    results = scan_all_chains.run_scan_tick(
        chains=[],
        active_protocols=["Securitize"],
        scan_prices=False,
        scan_hypercore=False,
        scan_grvt=False,
        scan_lighter=False,
        scan_hibachi=False,
        scan_apex=False,
        scan_core3=False,
        scan_currency_rates=False,
        max_workers=1,
        core3_max_workers=1,
        currency_api_max_workers=1,
        frequency="1h",
        retry_count=0,
        skip_post_processing=True,
        skip_cleaning=True,
        skip_top_vaults=True,
        skip_sparklines=True,
        skip_metadata=True,
        skip_data=True,
        skip_samples=True,
        vault_db_path=tmp_path / "vault-metadata-db.pickle",
        uncleaned_price_path=tmp_path / "vault-prices-1h.parquet",
        reader_state_path=tmp_path / "vault-reader-state-1h.pickle",
        hyperliquid_db_path=tmp_path / "hyperliquid-vaults.duckdb",
        hyperliquid_hf_db_path=tmp_path / "hyperliquid-vaults-hf.duckdb",
        grvt_db_path=tmp_path / "grvt-vaults.duckdb",
        lighter_db_path=tmp_path / "lighter-pools.duckdb",
        hibachi_db_path=tmp_path / "hibachi-vaults.duckdb",
        apex_db_path=tmp_path / "apex-vaults.duckdb",
        bkp_files=[],
        bkp_dir=tmp_path / "backups",
        tokenised_fund_scanners=(scanner,),
        tokenised_fund_scheduling_enabled=True,
        on_item_success=successful_items.append,
    )

    assert results["Securitize"].status == "success"
    assert results["Securitize"].price_rows == EXPECTED_PRICE_ROWS
    assert successful_items == ["Securitize"]
