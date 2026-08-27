"""Unit tests for the isolated crypto-vaults export primitives."""

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from eth_defi.research.wrangle_vault_prices import (
    filter_vaults_by_denomination_families,
    filter_vaults_by_stablecoin,
    materialise_daily_crypto_prices,
)
from eth_defi.vault import crypto_vault_export
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.crypto_vault_export import build_crypto_vault_manifest, publish_crypto_vault_bundle
from eth_defi.vault.crypto_vaults import (
    build_crypto_vault_record,
    resolve_crypto_vault_paths,
)
from eth_defi.vault.denomination import (
    DENOMINATION_SYMBOLS,
    DenominationFamily,
    classify_denomination,
    convert_usd_threshold_to_denomination,
    normalise_denomination_symbol,
)

DENOMINATION_DECIMALS = 18


def _vault_row(chain_id: int, address: str, denomination: str) -> dict:
    """Create the small vault-row subset used by denomination selection tests.

    :param chain_id:
        EVM chain identifier.
    :param address:
        Lowercase vault address.
    :param denomination:
        Vault denomination symbol.
    :return:
        Metadata row compatible with selection helpers.
    """
    return {
        "Denomination": denomination,
        "_detection_data": SimpleNamespace(chain=chain_id, address=address),
        "_denomination_token": {"address": "0xdenomination", "decimals": DENOMINATION_DECIMALS},
    }


def test_denomination_classifier_and_fixed_thresholds() -> None:
    """Reviewed wrappers classify by symbol and use fixed-rate guidelines."""
    assert all(symbol == normalise_denomination_symbol(symbol) for symbol in DENOMINATION_SYMBOLS)
    assert all(classify_denomination(symbol) is family and wrapper_kind != "" for symbol, (family, wrapper_kind) in DENOMINATION_SYMBOLS.items())
    assert classify_denomination("USDC") is DenominationFamily.stablecoin
    assert classify_denomination("sUSDe") is DenominationFamily.stablecoin
    assert classify_denomination(" wstETH ") is DenominationFamily.eth
    assert classify_denomination("cbBTC") is DenominationFamily.btc
    assert classify_denomination("ETH/BTC LP") is DenominationFamily.unsupported
    assert convert_usd_threshold_to_denomination(Decimal("5000"), "USDC") == Decimal("5000")
    assert convert_usd_threshold_to_denomination(Decimal("5000"), "wstETH") == Decimal("2.5")
    assert convert_usd_threshold_to_denomination(Decimal("5000"), "cbBTC") == Decimal("0.08333333333333333333333333333")


def test_crypto_selection_retains_cleaned_price_schema() -> None:
    """Crypto denomination selection does not add metadata columns."""
    stable_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    eth_spec = VaultSpec(1, "0x0000000000000000000000000000000000000002")
    btc_spec = VaultSpec(1, "0x0000000000000000000000000000000000000003")
    rows = {
        stable_spec: _vault_row(1, stable_spec.vault_address, "USDC"),
        eth_spec: _vault_row(1, eth_spec.vault_address, "wstETH"),
        btc_spec: _vault_row(1, btc_spec.vault_address, "cbBTC"),
    }
    prices = pd.DataFrame({"id": [stable_spec.as_string_id(), eth_spec.as_string_id(), btc_spec.as_string_id()], "share_price": [1.0, 1.0, 1.0]})

    stable = filter_vaults_by_stablecoin(rows, prices, logger=lambda _message: None)
    crypto = filter_vaults_by_denomination_families(
        rows,
        prices,
        {DenominationFamily.stablecoin, DenominationFamily.eth, DenominationFamily.btc},
        logger=lambda _message: None,
    )

    assert stable.columns.tolist() == prices.columns.tolist()
    assert stable["id"].tolist() == [stable_spec.as_string_id()]
    assert crypto.columns.tolist() == prices.columns.tolist()
    assert crypto["id"].tolist() == prices["id"].tolist()


def test_daily_materialisation_preserves_last_observation_and_schema() -> None:
    """Daily output keeps actual last rows without adding columns or dates."""
    prices = pd.DataFrame(
        {
            "id": ["1-0xvault", "1-0xvault", "1-0xvault", "1-0xvault"],
            "timestamp": pd.to_datetime(
                [
                    "2026-01-01 10:00:00",
                    "2026-01-01 15:00:00",
                    "2026-01-01 15:00:00",
                    "2026-01-03 09:00:00",
                ]
            ),
            "block_number": [10, 11, 12, 13],
            "share_price": [1.0, 1.1, 1.2, 1.5],
        }
    )

    result = materialise_daily_crypto_prices(prices)

    assert result.index.tolist() == [pd.Timestamp("2026-01-01 15:00:00"), pd.Timestamp("2026-01-03 09:00:00")]
    assert result["block_number"].tolist() == [12, 13]
    assert result.columns.tolist() == ["id", "block_number", "share_price"]


def test_crypto_metadata_identifies_underlying_and_stablecoinish_history() -> None:
    """Vault metadata distinguishes WBTC's BTC mapping and crypto history."""
    common_record = {"current_nav": 1.0, "peak_nav": 2.0}

    wbtc_record, _ = build_crypto_vault_record(common_record, _vault_row(1, "0xbtc", "WBTC"), Decimal("5000"))
    stablecoin_record, _ = build_crypto_vault_record(common_record, _vault_row(1, "0xstable", "USDC"), Decimal("5000"))
    mixed_case_stablecoin_record, _ = build_crypto_vault_record(common_record, _vault_row(1, "0xmixed", "sUSDe"), Decimal("5000"))

    assert wbtc_record["denomination"] == "WBTC"
    assert wbtc_record["denomination_token_address"] == "0xdenomination"
    assert wbtc_record["denomination_decimals"] == DENOMINATION_DECIMALS
    assert wbtc_record["canonical_underlying"] == "BTC"
    assert wbtc_record["stablecoinish"] is False
    assert "denomination_token_symbol" not in wbtc_record
    assert "denomination_token_decimals" not in wbtc_record
    assert "total_assets_unit" not in wbtc_record
    assert stablecoin_record["canonical_underlying"] == "USD"
    assert stablecoin_record["stablecoinish"] is True
    assert mixed_case_stablecoin_record["denomination"] == "sUSDe"
    assert mixed_case_stablecoin_record["stablecoinish"] is True


def test_manifest_describes_flat_crypto_payloads(tmp_path: Path) -> None:
    """The root manifest records checksums, daily rows and observed range."""
    paths = resolve_crypto_vault_paths(tmp_path)
    paths.directory.mkdir()
    pd.DataFrame(
        {
            "id": ["1-0xstable", "1-0xeth", "1-0xbtc"],
            "timestamp": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
        }
    ).set_index("timestamp").to_parquet(paths.cleaned_price_path)
    paths.metadata_path.write_text("{}", encoding="utf-8")
    paths.compressed_metadata_path.write_bytes(b"compressed")
    paths.sticky_state_path.write_text("{}", encoding="utf-8")
    metadata = {
        "generated_at": "2026-01-03T00:00:00",
        "metadata": {"version": {"commit_hash": "abc"}},
        "vaults": [
            {"id": "1-0xstable", "denomination_family": "stablecoin"},
            {"id": "1-0xeth", "denomination_family": "eth"},
            {"id": "1-0xbtc", "denomination_family": "btc"},
        ],
        "threshold_usd_guideline": 5000.0,
        "fixed_usd_rates": {"ETH": 2000, "BTC": 60000},
    }

    manifest = build_crypto_vault_manifest(paths, metadata)

    assert "object_keys" not in manifest
    assert manifest["price_row_count_total"] == len(metadata["vaults"])
    assert "price_row_counts" not in manifest
    assert manifest["price_observation_range"]["min_timestamp"] == "2026-01-01T00:00:00"
    assert set(manifest["files"]) == {
        "crypto-cleaned-vault-prices-1d.parquet",
        "crypto-vault-metadata.json",
        "crypto-vault-metadata.json.br",
        "crypto-vault-export-state.json",
    }
    assert manifest["files"]["crypto-vault-metadata.json"]["sha256"]


def test_publish_crypto_bundle_uses_flat_prefixed_root_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Private publication keeps crypto objects at the alternative bucket root.

    The object names intentionally mirror the public stablecoin export layout,
    but the ``crypto-`` prefix prevents collisions with the public artefacts.

    :param tmp_path:
        Isolated local bundle directory.
    :param monkeypatch:
        Pytest environment and integration patch helper.
    """
    paths = resolve_crypto_vault_paths(tmp_path)
    paths.directory.mkdir()
    pd.DataFrame(
        {
            "id": ["1-0xstable"],
            "timestamp": pd.to_datetime(["2026-01-01"]),
        }
    ).set_index("timestamp").to_parquet(paths.cleaned_price_path)
    paths.metadata_path.write_text("{}", encoding="utf-8")
    paths.sticky_state_path.write_text("{}", encoding="utf-8")
    metadata = {
        "generated_at": "2026-01-03T00:00:00",
        "metadata": {"version": {"commit_hash": "abc"}},
        "vaults": [{"id": "1-0xstable", "denomination_family": "stablecoin"}],
        "threshold_usd_guideline": 5000.0,
        "fixed_usd_rates": {"ETH": 2000, "BTC": 60000},
    }
    upload_keys: list[str] = []
    backup_keys: list[str] = []

    def fake_upload_file_to_r2(*, object_name: str, **_: object) -> bool:
        upload_keys.append(object_name)
        return True

    def fake_copy_r2_object_daily_backup(_client: object, _bucket_name: str, object_name: str) -> None:
        backup_keys.append(object_name)

    monkeypatch.setattr(crypto_vault_export, "create_r2_client", lambda **_: object())
    monkeypatch.setattr(crypto_vault_export, "upload_file_to_r2", fake_upload_file_to_r2)
    monkeypatch.setattr(crypto_vault_export, "copy_r2_object_daily_backup", fake_copy_r2_object_daily_backup)
    monkeypatch.setenv("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME", "private-bucket")
    monkeypatch.setenv("R2_DATA_ENDPOINT_URL", "https://example.invalid")
    monkeypatch.setenv("R2_DATA_ACCESS_KEY_ID", "access-key")
    monkeypatch.setenv("R2_DATA_SECRET_ACCESS_KEY", "secret-key")
    monkeypatch.setenv("UPLOAD_PREFIX", "test-")

    assert publish_crypto_vault_bundle(paths, metadata) is True
    assert upload_keys == [
        "test-crypto-cleaned-vault-prices-1d.parquet",
        "test-crypto-vault-metadata.json",
        "test-crypto-vault-metadata.json.br",
        "test-crypto-vault-export-state.json",
        "test-crypto-vault-manifest.json",
    ]
    assert backup_keys == upload_keys
