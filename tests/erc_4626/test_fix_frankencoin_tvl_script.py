"""Tests for the Frankencoin TVL repair script."""

import datetime
import importlib.util
from pathlib import Path

import pandas as pd

from eth_defi.erc_4626.vault_protocol.frankencoin.vault import FRANKENCOIN_PRODUCT_TVL_VAULTS_BY_CHAIN
from eth_defi.vault.base import VaultHistoricalRead

FRANKENCOIN_CORRECTED_TVL = 11_666_191.0
FRANKENCOIN_OLD_TVL = 3.4
FRANKENCOIN_REPAIRED_ROWS = sum(len(vaults) for vaults in FRANKENCOIN_PRODUCT_TVL_VAULTS_BY_CHAIN.values())
FRANKENCOIN_SHARE_PRICE = 1.01
UNRELATED_TVL = 100.0


def load_fix_module():
    """Load the Frankencoin TVL repair script as a module.

    The script filename contains dashes, so it cannot be imported through the
    normal package path.

    :return:
        Loaded repair script module.
    """
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "fix-frankencoin-tvl.py"
    spec = importlib.util.spec_from_file_location("fix_frankencoin_tvl", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_price_rows() -> pd.DataFrame:
    """Create a minimal canonical price DataFrame for repair tests.

    :return:
        Price rows containing Frankencoin rows and one unrelated row.
    """
    timestamp = datetime.datetime(2026, 7, 10, 12, 0, tzinfo=datetime.UTC).replace(tzinfo=None)
    frankencoin_rows = [
        {
            "chain": chain_id,
            "address": vault_address,
            "block_number": 25_000_000,
            "timestamp": timestamp,
            "share_price": FRANKENCOIN_SHARE_PRICE,
            "total_assets": FRANKENCOIN_OLD_TVL,
            "total_supply": 3.3,
            "performance_fee": None,
            "management_fee": None,
            "errors": "",
            "vault_poll_frequency": "large_tvl",
            "max_deposit": None,
            "max_redeem": None,
            "deposits_open": "",
            "redemption_open": "",
            "trading": "",
            "available_liquidity": None,
            "utilisation": None,
            "written_at": None,
        }
        for chain_id, vault_addresses in FRANKENCOIN_PRODUCT_TVL_VAULTS_BY_CHAIN.items()
        for vault_address in vault_addresses
    ]
    return pd.DataFrame(
        [
            *frankencoin_rows,
            {
                "chain": 1,
                "address": "0x0000000000000000000000000000000000000001",
                "block_number": 25_000_000,
                "timestamp": timestamp,
                "share_price": 1.0,
                "total_assets": UNRELATED_TVL,
                "total_supply": 100.0,
                "performance_fee": None,
                "management_fee": None,
                "errors": "",
                "vault_poll_frequency": "small_tvl",
                "max_deposit": None,
                "max_redeem": None,
                "deposits_open": "",
                "redemption_open": "",
                "trading": "",
                "available_liquidity": None,
                "utilisation": None,
                "written_at": None,
            },
        ]
    )


def test_repair_frankencoin_rows_updates_only_frankencoin() -> None:
    """Pure row repair updates only hardcoded Frankencoin vault rows."""
    module = load_fix_module()
    df = create_price_rows()

    def fetch_total_assets(_chain_id: int, _block_number: int, _address: str) -> float:
        """Return deterministic corrected TVL for tests."""
        return FRANKENCOIN_CORRECTED_TVL

    updated_df, result = module.repair_frankencoin_rows(
        df,
        fetch_total_assets=fetch_total_assets,
        max_workers=1,
    )

    assert result.matched_rows == FRANKENCOIN_REPAIRED_ROWS
    assert result.updated_rows == FRANKENCOIN_REPAIRED_ROWS
    assert result.skipped_rows == 0
    assert updated_df.loc[0, "share_price"] == FRANKENCOIN_SHARE_PRICE
    assert all(updated_df.loc[0 : FRANKENCOIN_REPAIRED_ROWS - 1, "total_assets"] == FRANKENCOIN_CORRECTED_TVL)
    assert updated_df.loc[FRANKENCOIN_REPAIRED_ROWS, "total_assets"] == UNRELATED_TVL


def test_repair_frankencoin_tvl_parquet_writes_backup(monkeypatch, tmp_path: Path) -> None:
    """Parquet repair writes a backup and preserves unrelated rows."""
    module = load_fix_module()
    parquet_path = tmp_path / "vault-prices-1h.parquet"
    VaultHistoricalRead.write_uncleaned_parquet(create_price_rows(), parquet_path)

    monkeypatch.setenv("JSON_RPC_ETHEREUM", "https://example.invalid")
    monkeypatch.setenv("JSON_RPC_BASE", "https://example.invalid")
    monkeypatch.setenv("JSON_RPC_GNOSIS", "https://example.invalid")
    monkeypatch.setattr(module, "create_multi_provider_web3", lambda _rpc_url: object())
    monkeypatch.setattr(module, "fetch_frankencoin_vault_contracts", lambda _web3, spec: spec)
    monkeypatch.setattr(module, "fetch_frankencoin_total_assets_raw", lambda _contracts, _block_number: int(FRANKENCOIN_CORRECTED_TVL * 10**18))

    result = module.repair_frankencoin_tvl_parquet(
        parquet_path,
        dry_run=False,
        start_block=None,
        end_block=None,
        max_workers=1,
    )

    repaired_df = pd.read_parquet(parquet_path)

    assert result.matched_rows == FRANKENCOIN_REPAIRED_ROWS
    assert result.updated_rows == FRANKENCOIN_REPAIRED_ROWS
    assert result.skipped_rows == 0
    assert (tmp_path / "vault-prices-1h.parquet.bak-frankencoin-tvl").exists()
    assert all(repaired_df.loc[0 : FRANKENCOIN_REPAIRED_ROWS - 1, "total_assets"] == FRANKENCOIN_CORRECTED_TVL)
    assert repaired_df.loc[FRANKENCOIN_REPAIRED_ROWS, "total_assets"] == UNRELATED_TVL
