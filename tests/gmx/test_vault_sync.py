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
from eth_defi.vault.flag import GMX_SINGLE_SIDED_USDC_NOTE
from eth_defi.vault.vaultdb import VaultDatabase

GM_TOKEN = to_checksum_address("0x1000000000000000000000000000000000000001")
INDEX_TOKEN = to_checksum_address("0x2000000000000000000000000000000000000001")
LONG_TOKEN = to_checksum_address("0x2000000000000000000000000000000000000002")
SHORT_TOKEN = to_checksum_address("0x2000000000000000000000000000000000000003")
FIRST_CATALOGUE_BLOCK = 123


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
    monkeypatch.setattr(
        vault_sync,
        "_fetch_token_symbol",
        lambda _web3, _chain_id, address, _token_cache: {
            INDEX_TOKEN: "DOGE",
            LONG_TOKEN: "WBTC",
            SHORT_TOKEN: "USDC",
        }[address],
    )
    monkeypatch.setattr(vault_sync, "get_tokens_metadata_dict", lambda _chain: {INDEX_TOKEN: {"symbol": "DOGE"}})
    monkeypatch.setattr(
        vault_sync,
        "create_vault_scan_record",
        lambda _web3, detection, _block_number, _token_cache: {
            "_detection_data": detection,
            "Name": "GMX market",
            "Protocol": "GMX",
            "Denomination": "USD",
        },
    )
    assert (
        vault_sync._format_gmx_product_name(
            replace(product, product_type="glv"),
            long_token_symbol="WBTC",
            short_token_symbol="USDC",
            market_labels={GM_TOKEN.lower(): "DOGE/USD"},
        )
        == "GLV [WBTC-USDC]"
    )
    vault_db = VaultDatabase()

    first = fetch_and_sync_gmx_vault_catalogue(web3=web3, vault_db=vault_db, token_cache={}, block_number=FIRST_CATALOGUE_BLOCK)
    vault_db.rows[VaultSpec(42161, GM_TOKEN)]["_manual_enrichment"] = "keep me"
    vault_db.rows[VaultSpec(42161, GM_TOKEN)]["_short_description"] = "Remove me"
    vault_db.rows[VaultSpec(42161, GM_TOKEN)]["_notes"] = "Replace me"
    vault_db.rows[VaultSpec(42161, GM_TOKEN)]["_deposits_open"] = False
    monkeypatch.setattr(
        vault_sync,
        "create_vault_scan_record",
        lambda _web3, detection, _block_number, _token_cache: {
            "_detection_data": detection,
            "Name": "<broken: temporary RPC failure>",
            "Protocol": "<unknown>",
            "Denomination": None,
        },
    )
    second = fetch_and_sync_gmx_vault_catalogue(web3=web3, vault_db=vault_db, token_cache={}, block_number=456)
    row_after_failed_refresh = vault_db.rows[VaultSpec(42161, GM_TOKEN)]
    assert row_after_failed_refresh["Name"] == "GM DOGE [WBTC-USDC]"
    assert row_after_failed_refresh["Protocol"] == "GMX"
    assert row_after_failed_refresh["Denomination"] == "USDC"
    assert row_after_failed_refresh["Link"] == f"https://app.gmx.io/#/pools/details?market={GM_TOKEN.lower()}&operation=Deposit&chainId=42161"
    assert row_after_failed_refresh["_notes"] == GMX_SINGLE_SIDED_USDC_NOTE
    assert row_after_failed_refresh["_short_description"] is None
    expected_description = "\n".join(
        (
            "Liquidity providers supply WBTC and USDC to provide liquidity for GMX perpetual trading and swaps. They earn a share of trading, borrowing, liquidation and swap fees, and benefit when traders make net losses. They bear net trader profits and changes in the value of the backing tokens.",
            "",
            "- **Index market:** DOGE/USD — the price market for which traders take long and short positions.",
            "- **Long backing token:** WBTC — backs and settles profitable long positions.",
            "- **Short backing token:** USDC — backs and settles profitable short positions.",
        )
    )
    assert row_after_failed_refresh["_description"] == expected_description
    assert row_after_failed_refresh["_deposits_open"] is None
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
    assert row["Name"] == "GM DOGE [WBTC-USDC]"
    assert row["Link"] == f"https://app.gmx.io/#/pools/details?market={GM_TOKEN.lower()}&operation=Deposit&chainId=42161"
    assert row["_manual_enrichment"] == "keep me"
    assert detection.first_seen_at_block == FIRST_CATALOGUE_BLOCK
    assert detection.address == GM_TOKEN.lower()
    assert detection.features == {ERC4626Feature.gmx_gm, ERC4626Feature.share_price_equivalence}
    assert row["_gmx_component_addresses"] == tuple(address.lower() for address in product.component_addresses)
    assert row["_gmx_enabled"] is False
    assert row["_deposit_closed_reason"] == "GMX product disabled"
    assert row["_deposits_open"] is False

    healthy_name = row["Name"]
    healthy_description = row["_description"]
    monkeypatch.setattr(vault_sync, "get_tokens_metadata_dict", lambda _chain: (_ for _ in ()).throw(ValueError("temporary GMX API outage")))
    fourth = fetch_and_sync_gmx_vault_catalogue(web3=web3, vault_db=vault_db, token_cache={}, block_number=790)
    assert fourth.updated == 1
    assert row["Name"] == healthy_name
    assert row["_description"] == healthy_description

    monkeypatch.setattr(vault_sync, "get_tokens_metadata_dict", lambda _chain: {})
    fifth = fetch_and_sync_gmx_vault_catalogue(web3=web3, vault_db=vault_db, token_cache={}, block_number=791)
    assert fifth.updated == 1
    assert row["Name"] == healthy_name
    assert row["_description"] == healthy_description


def test_disambiguate_gmx_product_names_adds_short_stable_suffix() -> None:
    """Duplicate index-plus-backing-pair labels remain unique and compact."""

    second_market = to_checksum_address("0x1000000000000000000000000000000000000002")
    names = vault_sync._disambiguate_gmx_product_names(
        {
            GM_TOKEN.lower(): "GM DOGE [WETH-USDC]",
            second_market.lower(): "GM DOGE [WETH-USDC]",
        }
    )

    assert names == {
        GM_TOKEN.lower(): "GM DOGE [WETH-USDC] · 0001",
        second_market.lower(): "GM DOGE [WETH-USDC] · 0002",
    }


def test_format_gmx_glv_description_explains_all_supported_markets() -> None:
    """GLV descriptions identify the aggregate's markets and both backing tokens."""

    second_market = to_checksum_address("0x1000000000000000000000000000000000000002")
    product = GMXVaultProduct(
        chain_id=42161,
        token_address=GM_TOKEN,
        product_type="glv",
        symbol="GLV",
        name="GMX liquidity vault",
        decimals=18,
        component_addresses=(GM_TOKEN, LONG_TOKEN, SHORT_TOKEN, GM_TOKEN, second_market),
        accepted_deposit_tokens=(LONG_TOKEN, SHORT_TOKEN),
        is_enabled=True,
    )

    description = vault_sync._format_gmx_product_description(
        product,
        long_token_symbol="WETH",
        short_token_symbol="USDC",
        market_labels={GM_TOKEN.lower(): "ETH/USD", second_market.lower(): "DOGE/USD"},
    )

    assert "Liquidity providers supply WETH and USDC" in description
    assert "allocates the supplied liquidity across its compatible GM markets" in description
    assert "- **Supported index markets:** ETH/USD, DOGE/USD" in description
    assert "- **Long backing token:** WETH — backs and settles profitable long positions." in description
    assert "- **Short backing token:** USDC — backs and settles profitable short positions." in description


def test_format_gmx_tradfi_description_explains_synthetic_exposure() -> None:
    """TradFi pools make clear that they do not custody the referenced asset."""

    product = GMXVaultProduct(
        chain_id=42161,
        token_address=GM_TOKEN,
        product_type="gm",
        symbol="GM",
        name="GMX gold market",
        decimals=18,
        component_addresses=(GM_TOKEN, INDEX_TOKEN, LONG_TOKEN, SHORT_TOKEN),
        accepted_deposit_tokens=(LONG_TOKEN, SHORT_TOKEN),
        is_enabled=True,
    )

    description = vault_sync._format_gmx_product_description(
        product,
        long_token_symbol="WETH",
        short_token_symbol="USDC",
        market_labels={GM_TOKEN.lower(): "GOLD/USD"},
    )

    assert "synthetic exposure to the GOLD/USD reference price" in description
    assert "does not hold the underlying real-world asset or financial instrument" in description


def test_format_gmx_single_token_description_explains_shared_backing_token() -> None:
    """Single-token pools do not misleadingly describe the same token twice."""

    product = GMXVaultProduct(
        chain_id=42161,
        token_address=GM_TOKEN,
        product_type="gm",
        symbol="GM",
        name="GMX ETH market",
        decimals=18,
        component_addresses=(GM_TOKEN, INDEX_TOKEN, LONG_TOKEN, LONG_TOKEN),
        accepted_deposit_tokens=(LONG_TOKEN, LONG_TOKEN),
        is_enabled=True,
    )

    description = vault_sync._format_gmx_product_description(
        product,
        long_token_symbol="WETH",
        short_token_symbol="WETH",
        market_labels={GM_TOKEN.lower(): "ETH/USD"},
    )

    assert "Liquidity providers supply WETH to provide liquidity" in description
    assert "- **Long and short backing token:** WETH" in description
    assert "Long backing token" not in description
    assert (
        vault_sync._format_gmx_product_name(
            product,
            long_token_symbol="WETH",
            short_token_symbol="WETH",
            market_labels={GM_TOKEN.lower(): "ETH/USD"},
        )
        == "GM ETH [WETH-WETH]"
    )


def test_format_gmx_swap_pool_has_no_fabricated_index_market() -> None:
    """Swap-only GM pools use token roles rather than perpetual-market roles."""

    product = GMXVaultProduct(
        chain_id=42161,
        token_address=GM_TOKEN,
        product_type="gm",
        symbol="GM",
        name="GMX swap pool",
        decimals=18,
        component_addresses=(GM_TOKEN, vault_sync.GMX_ZERO_ADDRESS, LONG_TOKEN, SHORT_TOKEN),
        accepted_deposit_tokens=(LONG_TOKEN, SHORT_TOKEN),
        is_enabled=True,
    )

    description = vault_sync._format_gmx_product_description(
        product,
        long_token_symbol="USDC",
        short_token_symbol="USDT",
        market_labels={},
    )

    assert description.startswith("Liquidity providers supply USDC and USDT to provide liquidity for GMX token swaps.")
    assert "- **Activity:** Swap-only market — this pool has no index market and does not back perpetual positions." in description
    assert "- **First pool token:** USDC" in description
    assert "- **Second pool token:** USDT" in description
    assert "Index market" not in description
    assert (
        vault_sync._format_gmx_product_name(
            product,
            long_token_symbol="USDC",
            short_token_symbol="USDT",
            market_labels={},
        )
        == "GM swap [USDC-USDT]"
    )


def test_build_market_labels_uses_onchain_index_symbols_without_sdk_suffix() -> None:
    """Single-token GM pools retain their actual index symbol in catalogue names."""

    product = GMXVaultProduct(
        chain_id=42161,
        token_address=GM_TOKEN,
        product_type="gm",
        symbol="GM",
        name="GMX single-token ETH market",
        decimals=18,
        component_addresses=(GM_TOKEN, INDEX_TOKEN, LONG_TOKEN, LONG_TOKEN),
        accepted_deposit_tokens=(LONG_TOKEN, LONG_TOKEN),
        is_enabled=True,
    )

    assert vault_sync._build_market_labels((product,), {INDEX_TOKEN.lower(): {"symbol": "ETH"}}) == {GM_TOKEN.lower(): "ETH/USD"}


def test_build_market_labels_preserves_wsteth_market_identity() -> None:
    """The wstETH/USDe GM market is not published as an ETH market."""

    product = GMXVaultProduct(
        chain_id=42161,
        token_address="0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5",
        product_type="gm",
        symbol="GM",
        name="GMX wstETH market",
        decimals=18,
        component_addresses=("0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5", INDEX_TOKEN, LONG_TOKEN, SHORT_TOKEN),
        accepted_deposit_tokens=(LONG_TOKEN, SHORT_TOKEN),
        is_enabled=True,
    )

    assert vault_sync._build_market_labels((product,), {INDEX_TOKEN.lower(): {"symbol": "ETH"}}) == {product.token_address.lower(): "wstETH/USD"}


def test_unresolved_index_label_preserves_existing_perpetual_metadata() -> None:
    """A partial registry result does not make a perpetual GM pool look like a swap pool."""

    perpetual = GMXVaultProduct(
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
    swap = replace(perpetual, component_addresses=(GM_TOKEN, vault_sync.GMX_ZERO_ADDRESS, LONG_TOKEN, SHORT_TOKEN))

    assert vault_sync._has_unresolved_index_label(perpetual, {GM_TOKEN.lower(): None})
    assert not vault_sync._has_unresolved_index_label(swap, {})
