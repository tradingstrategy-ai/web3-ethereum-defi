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
from eth_defi.vault.crypto_vault_export import build_crypto_vault_manifest
from eth_defi.vault.crypto_vaults import resolve_crypto_vault_paths
from eth_defi.vault.denomination import (
    DenominationFamily,
    classify_denomination,
    convert_usd_threshold_to_denomination,
)


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
        "_denomination_token": {"address": "0xdenomination", "decimals": 18},
    }


def test_denomination_classifier_and_fixed_thresholds() -> None:
    """Reviewed wrappers classify by symbol and use fixed-rate guidelines."""
    assert classify_denomination("USDC") is DenominationFamily.stablecoin
    assert classify_denomination(" wstETH ") is DenominationFamily.eth
    assert classify_denomination("cbBTC") is DenominationFamily.btc
    assert classify_denomination("ETH/BTC LP") is DenominationFamily.unsupported
    assert convert_usd_threshold_to_denomination(Decimal("5000"), "USDC") == Decimal("5000")
    assert convert_usd_threshold_to_denomination(Decimal("5000"), "wstETH") == Decimal("2.5")
    assert convert_usd_threshold_to_denomination(Decimal("5000"), "cbBTC") == Decimal("0.08333333333333333333333333333")


def test_crypto_selection_enriches_only_private_schema() -> None:
    """Stablecoin compatibility selection retains its old column set."""
    rows = {
        "stable": _vault_row(1, "0xstable", "USDC"),
        "eth": _vault_row(1, "0xeth", "wstETH"),
        "btc": _vault_row(1, "0xbtc", "cbBTC"),
    }
    prices = pd.DataFrame({"id": ["1-0xstable", "1-0xeth", "1-0xbtc"], "share_price": [1.0, 1.0, 1.0]})

    stable = filter_vaults_by_stablecoin(rows, prices, logger=lambda _message: None)
    crypto = filter_vaults_by_denomination_families(
        rows,
        prices,
        {DenominationFamily.stablecoin, DenominationFamily.eth, DenominationFamily.btc},
        add_denomination_family=True,
        logger=lambda _message: None,
    )

    assert stable.columns.tolist() == prices.columns.tolist()
    assert stable["id"].tolist() == ["1-0xstable"]
    assert crypto["denomination_family"].tolist() == ["stablecoin", "eth", "btc"]
    assert crypto["canonical_underlying"].tolist() == ["USD", "ETH", "BTC"]
    assert crypto["denomination_token_symbol"].tolist() == ["USDC", "WSTETH", "CBBTC"]


def test_daily_materialisation_preserves_last_observation_and_sparse_return() -> None:
    """Daily output keeps actual last rows and does not fabricate missing dates."""
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
    assert result["returns_1d"].tolist() == [0.0, pytest.approx(0.25)]


def test_manifest_describes_flat_crypto_payloads(tmp_path: Path) -> None:
    """The root manifest records checksums, daily rows and observed range."""
    paths = resolve_crypto_vault_paths(tmp_path)
    paths.directory.mkdir()
    pd.DataFrame(
        {
            "denomination_family": ["stablecoin", "eth", "btc"],
            "timestamp": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
        }
    ).set_index("timestamp").to_parquet(paths.cleaned_price_path)
    paths.metadata_path.write_text("{}", encoding="utf-8")
    paths.metadata_path.with_suffix(".json.br").write_bytes(b"compressed")
    paths.sticky_state_path.write_text("{}", encoding="utf-8")
    metadata = {
        "generated_at": "2026-01-03T00:00:00",
        "metadata": {"version": {"commit_hash": "abc"}},
        "vaults": [
            {"denomination_family": "stablecoin"},
            {"denomination_family": "eth"},
            {"denomination_family": "btc"},
        ],
        "threshold_usd_guideline": 5000.0,
        "fixed_usd_rates": {"ETH": 2000, "BTC": 60000},
    }

    manifest = build_crypto_vault_manifest(paths, metadata)

    assert "object_keys" not in manifest
    assert manifest["price_row_counts"] == {"stablecoin": 1, "eth": 1, "btc": 1}
    assert manifest["price_observation_range"]["min_timestamp"] == "2026-01-01T00:00:00"
    assert manifest["files"]["vault-metadata.json"]["sha256"]
