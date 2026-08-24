"""Tests for GMX catalogue reconciliation into the common metadata database."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from eth_utils import to_checksum_address

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.gmx import vault_sync
from eth_defi.gmx.vault_catalog import GMXVaultProduct
from eth_defi.gmx.vault_sync import fetch_and_sync_gmx_vault_catalogue
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

GM_TOKEN = to_checksum_address("0x1000000000000000000000000000000000000001")
INDEX_TOKEN = to_checksum_address("0x2000000000000000000000000000000000000001")
LONG_TOKEN = to_checksum_address("0x2000000000000000000000000000000000000002")
SHORT_TOKEN = to_checksum_address("0x2000000000000000000000000000000000000003")
FIRST_CATALOGUE_BLOCK = 123
EXISTING_NAV = 42
EXISTING_SHARES = 7


def test_fetch_and_sync_gmx_vault_catalogue_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated catalogue refreshes preserve the original first-seen block."""

    product = GMXVaultProduct(
        chain_id=42161,
        token_address=GM_TOKEN,
        product_type="gm",
        symbol="GM",
        name="GMX market",
        decimals=18,
        component_addresses=(GM_TOKEN, INDEX_TOKEN, LONG_TOKEN, SHORT_TOKEN),
        accepted_deposit_tokens=(LONG_TOKEN, SHORT_TOKEN),
        is_enabled=True,
    )
    web3 = SimpleNamespace(
        eth=SimpleNamespace(
            chain_id=42161,
            block_number=456,
            get_block=lambda block_number: {"timestamp": 1_700_000_000 + block_number},
        ),
    )
    monkeypatch.setattr(vault_sync, "fetch_gmx_v2_vault_products", lambda *_args, **_kwargs: iter((product,)))
    monkeypatch.setattr(vault_sync, "_fetch_token_symbol", lambda _web3, _chain_id, address, _token_cache: "WBTC" if address == LONG_TOKEN else "USDC")
    monkeypatch.setattr(
        vault_sync,
        "create_vault_scan_record",
        lambda _web3, detection, _block_number, _token_cache: {
            "_detection_data": detection,
            "Name": "GMX market",
            "Protocol": "GMX",
            "Denomination": "USD",
            "NAV": EXISTING_NAV,
            "Shares": EXISTING_SHARES,
            "_denomination_token": {"address": "0x1000000000000000000000000000000000000000"},
        },
    )
    vault_db = VaultDatabase()

    first = fetch_and_sync_gmx_vault_catalogue(web3=web3, vault_db=vault_db, token_cache={}, block_number=FIRST_CATALOGUE_BLOCK)
    vault_db.rows[VaultSpec(42161, GM_TOKEN)]["_manual_enrichment"] = "keep me"
    monkeypatch.setattr(
        vault_sync,
        "create_vault_scan_record",
        lambda _web3, detection, _block_number, _token_cache: {
            "_detection_data": detection,
            "Name": "<broken: temporary RPC failure>",
            "Protocol": "<unknown>",
            "Denomination": None,
            "NAV": 0,
            "Shares": 0,
            "_denomination_token": None,
        },
    )
    second = fetch_and_sync_gmx_vault_catalogue(web3=web3, vault_db=vault_db, token_cache={}, block_number=456)
    row_after_failed_refresh = vault_db.rows[VaultSpec(42161, GM_TOKEN)]
    assert row_after_failed_refresh["Name"] == f"GMX Market [WBTC-USDC] (Arbitrum, {GM_TOKEN})"
    assert row_after_failed_refresh["Protocol"] == "GMX"
    assert row_after_failed_refresh["Denomination"] == "USDC"
    assert row_after_failed_refresh["NAV"] == EXISTING_NAV
    assert row_after_failed_refresh["Shares"] == EXISTING_SHARES
    assert row_after_failed_refresh["_denomination_token"] == {"address": "0x1000000000000000000000000000000000000000"}
    assert row_after_failed_refresh["_manual_enrichment"] == "keep me"

    monkeypatch.setattr(vault_sync, "fetch_gmx_v2_vault_products", lambda *_args, **_kwargs: iter((replace(product, is_enabled=False),)))
    monkeypatch.setattr(
        vault_sync,
        "create_vault_scan_record",
        lambda _web3, detection, _block_number, _token_cache: {
            "_detection_data": detection,
            "Name": "GMX market refreshed",
            "Protocol": "GMX",
            "Denomination": "USD",
        },
    )
    third = fetch_and_sync_gmx_vault_catalogue(web3=web3, vault_db=vault_db, token_cache={}, block_number=789)

    row = vault_db.rows[VaultSpec(42161, GM_TOKEN)]
    detection = row["_detection_data"]
    assert first.inserted == 1
    assert second.inserted == 0
    assert second.updated == 1
    assert third.inserted == 0
    assert third.updated == 1
    assert row["Name"] == f"GMX Market [WBTC-USDC] (Arbitrum, {GM_TOKEN})"
    assert row["_manual_enrichment"] == "keep me"
    assert detection.first_seen_at_block == FIRST_CATALOGUE_BLOCK
    assert detection.address == GM_TOKEN.lower()
    assert detection.features == {ERC4626Feature.gmx_gm}
    assert row["_gmx_component_addresses"] == tuple(address.lower() for address in product.component_addresses)
    assert row["_synthetic_usd_denomination"] is False
    assert row["_gmx_enabled"] is False
    assert row["_deposit_closed_reason"] == "GMX product disabled"
    assert row["_deposits_open"] is False


def test_gmx_names_are_unique_for_products_with_the_same_backing_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """The full chain-local token address prevents duplicate product names."""

    first_product = GMXVaultProduct(
        chain_id=42161,
        token_address=GM_TOKEN,
        product_type="gm",
        symbol="GM",
        name="GMX market",
        decimals=18,
        component_addresses=(GM_TOKEN, INDEX_TOKEN, LONG_TOKEN, SHORT_TOKEN),
        accepted_deposit_tokens=(LONG_TOKEN, SHORT_TOKEN),
        is_enabled=True,
    )
    second_product = GMXVaultProduct(
        chain_id=42161,
        token_address=to_checksum_address("0x1000000000000000000000000000000000000002"),
        product_type="gm",
        symbol="GM",
        name="GMX market",
        decimals=18,
        component_addresses=(GM_TOKEN, INDEX_TOKEN, LONG_TOKEN, SHORT_TOKEN),
        accepted_deposit_tokens=(LONG_TOKEN, SHORT_TOKEN),
        is_enabled=True,
    )
    monkeypatch.setattr(vault_sync, "_fetch_token_symbol", lambda *_args: "USDC")

    first_name = vault_sync._format_gmx_product_name(SimpleNamespace(), first_product, {})
    second_name = vault_sync._format_gmx_product_name(SimpleNamespace(), second_product, {})

    assert first_name != second_name
    assert first_name.endswith(f"{GM_TOKEN})")
    assert second_name.endswith(f"{second_product.token_address})")
