"""Verify the Nest audit table uses the same export fields as the vault feed."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.fixture
def list_module() -> ModuleType:
    """Load the hyphenated audit script.

    :return: Imported script module.
    """
    path = Path(__file__).resolve().parents[2] / "scripts" / "nest" / "list-vaults.py"
    spec = importlib.util.spec_from_file_location("list_nest_vaults", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_nest_export_audit_uses_net_monthly_cagr(list_module: ModuleType) -> None:
    """Read route TVL and net one-month CAGR without using product-wide values."""
    address = "0x0342ee795e7864319fb8d48651b47febf1163c34"
    key = f"480:{address}"
    route = {
        "chain_id": 480,
        "vault_address": address,
        "status": "active",
        "name": "Nest Alpha Vault",
        "display_name": "Nest Institutional Alpha Vault",
        "asset_symbol": "USDC",
        "tvl_usd": 1_000_000,
        "reported_apy": 0.08,
    }
    vault_db = VaultDatabase(rows={VaultSpec(480, address): {"Name": route["display_name"], "_curator_slug": "nest-dao"}})
    exported = {
        key: {
            "name": route["display_name"],
            "chain_id": 480,
            "curator_slug": "nest-dao",
            "curator_name": "Nest DAO LLC",
            "current_nav": 12_345,
            "last_updated_at": "2026-09-22T08:13:59",
            "one_month_cagr": 0.09,
            "one_month_cagr_net": 0.025,
            "cagr": 0.11,
            "cagr_net": 0.08,
        }
    }

    [row] = list_module.build_rows({key: route}, vault_db, exported)

    assert row["Name"] == "Nest Institutional Alpha Vault"
    assert row["Chain"] == "worldchain"
    assert row["Curator"] == "Nest DAO LLC"
    assert row["TVL"] == "$12,345"
    assert row["1M CAGR"] == "2.50%"
    assert row["CAGR basis"] == "net"
    assert row["All-time CAGR"] == "8.00%"
    assert row["All-time basis"] == "net"
    assert row["Price as-of"] == "2026-09-22"
    assert (row["Name match"], row["Chain match"], row["Curator match"]) == ("yes", "yes", "yes")

    exported[key]["one_month_cagr_net"] = None
    exported[key]["cagr_net"] = None
    [gross_row] = list_module.build_rows({key: route}, vault_db, exported)
    assert gross_row["1M CAGR"] == "9.00%"
    assert gross_row["CAGR basis"] == "gross"
    assert gross_row["All-time CAGR"] == "11.00%"
    assert gross_row["All-time basis"] == "gross"


def test_cagr_zero_negative_and_missing_values(list_module: ModuleType) -> None:
    """Preserve zero and negative returns, and never present missing yield as zero."""
    assert list_module.select_cagr({"cagr_net": 0, "cagr": 0.1}, "cagr") == (0, "net")
    assert list_module.select_cagr({"cagr_net": -0.5, "cagr": 0.1}, "cagr") == (-0.5, "net")
    assert list_module.select_cagr({}, "cagr") == (None, "-")
    assert list_module.format_percent(None) == "-"
