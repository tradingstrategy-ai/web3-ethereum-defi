"""Test vault share-price source classification."""

import datetime
from dataclasses import replace

from web3 import Web3

from eth_defi.asseto.constants import ASSETO_AOABT_HASHKEY
from eth_defi.asseto.vault import AssetoVault
from eth_defi.erc_4626.classification import ODA_FACT_JLTXX_ADDRESS
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.grvt.vault_data_export import create_grvt_vault_row
from eth_defi.hibachi.vault_data_export import create_hibachi_vault_row
from eth_defi.hyperliquid.vault_data_export import create_hyperliquid_vault_row
from eth_defi.lighter.vault_data_export import create_lighter_pool_row
from eth_defi.oda_fact.vault import OdaFactVault
from eth_defi.securitize.description import ACRED_ETHEREUM, ARCOIN_ETHEREUM, BUIDL_ETHEREUM
from eth_defi.securitize.vault import SecuritizeVault
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.price_source import PriceSource


def test_price_source_public_values() -> None:
    """Price-source enum values are stable public export strings."""

    assert {source.value for source in PriceSource} == {
        "smart-contract-state",
        "api",
        "approximation",
        "fixed-price",
        "redstone",
        "chronicle",
    }


def test_vault_adapter_price_sources() -> None:
    """Contract, API, oracle and estimated adapters report their real source."""

    web3 = Web3()
    erc4626 = ERC4626Vault(
        web3,
        VaultSpec(chain_id=1, vault_address="0x0000000000000000000000000000000000000001"),
        features={ERC4626Feature.morpho_like},
    )
    assert VaultBase.get_share_price_source(object()) is None
    assert erc4626.get_share_price_source() is PriceSource.smart_contract_state

    asseto = AssetoVault(web3, VaultSpec(ASSETO_AOABT_HASHKEY.chain_id, ASSETO_AOABT_HASHKEY.token))
    assert asseto.get_share_price_source() is PriceSource.smart_contract_state
    asseto.product = replace(ASSETO_AOABT_HASHKEY, pricer=None, offchain_product_id=1)
    assert asseto.get_share_price_source() is PriceSource.api

    buidl = SecuritizeVault(web3, VaultSpec(BUIDL_ETHEREUM.chain_id, BUIDL_ETHEREUM.token))
    acred = SecuritizeVault(web3, VaultSpec(ACRED_ETHEREUM.chain_id, ACRED_ETHEREUM.token))
    arcoin = SecuritizeVault(web3, VaultSpec(ARCOIN_ETHEREUM.chain_id, ARCOIN_ETHEREUM.token))
    jltxx = OdaFactVault(web3, VaultSpec(chain_id=1, vault_address=ODA_FACT_JLTXX_ADDRESS))
    assert buidl.get_share_price_source() is PriceSource.fixed_price
    assert acred.get_share_price_source() is PriceSource.redstone
    assert arcoin.get_share_price_source() is None
    assert jltxx.get_share_price_source() is PriceSource.fixed_price


def test_native_vault_row_price_sources() -> None:
    """Native protocol importers persist API and approximation classifications."""

    timestamp = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC).replace(tzinfo=None)
    _, lighter = create_lighter_pool_row(1, "Lighter", None, 1_000, timestamp)
    _, grvt = create_grvt_vault_row("VLT:test", 1, "GRVT", None, 1_000)
    _, hibachi = create_hibachi_vault_row(1, "HIB", "Hibachi", None, 1_000)
    _, hyperliquid = create_hyperliquid_vault_row(
        "0x0000000000000000000000000000000000000001",
        "Hyperliquid",
        None,
        1_000,
        timestamp,
    )

    assert lighter["_share_price_source"] is PriceSource.api
    assert grvt["_share_price_source"] is PriceSource.api
    assert hibachi["_share_price_source"] is PriceSource.api
    assert hyperliquid["_share_price_source"] is PriceSource.approximation
