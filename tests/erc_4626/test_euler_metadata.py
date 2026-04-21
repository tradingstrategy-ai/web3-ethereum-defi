"""Euler vault offchain metadata tests.

Tests cover:
- Backward-compatible name/description/entity properties on EulerVault.
- New product-level fields exposed by the products.json migration.
- Lending-protocol detection and utilisation API.
- Historical multicall reader construction.
"""

import os
from decimal import Decimal
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import is_lending_protocol
from eth_defi.erc_4626.vault_protocol.euler.offchain_metadata import fetch_euler_vault_metadata
from eth_defi.erc_4626.vault_protocol.euler.vault import EulerVault, EulerVaultHistoricalReader
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    return web3


@flaky.flaky
def test_euler_metadata(
    web3: Web3,
    tmp_path: Path,
):
    """Read Euler vault metadata and confirm backward-compatible API still works.

    Vault 0x1e548... is not listed in the current products.json, so metadata
    resolves to None and the vault falls back to its on-chain EVK name.

    Steps:

    1. Auto-detect vault instance from its on-chain address.
    2. Assert the vault is an EulerVault.
    3. Check backward-compatible properties (name, description, entity).
    4. Verify lending-protocol detection.
    5. Verify utilisation API.
    6. Verify historical multicall reader is usable.
    """
    # 1. Auto-detect vault instance
    euler_prime_susds = create_vault_instance_autodetect(
        web3,
        vault_address="0x1e548CfcE5FCF17247E024eF06d32A01841fF404",
    )

    # 2. Must be an EulerVault
    assert isinstance(euler_prime_susds, EulerVault)

    # 3. Vault is not in products.json, so metadata is None and we get on-chain name fallback.
    # Accept the on-chain EVK name shape.
    assert "EVK Vault" in euler_prime_susds.name or "Euler Prime" in euler_prime_susds.name
    assert euler_prime_susds.description is None
    assert euler_prime_susds.entity is None
    assert euler_prime_susds.denomination_token.symbol == "sUSDS"

    # 4. Lending protocol identification
    assert is_lending_protocol(euler_prime_susds.features) is True

    # 5. Utilisation API
    available_liquidity = euler_prime_susds.fetch_available_liquidity()
    assert available_liquidity is not None
    assert available_liquidity >= Decimal(0)

    utilisation = euler_prime_susds.fetch_utilisation_percent()
    assert utilisation is not None
    assert 0.0 <= utilisation <= 1.0

    # 6. Historical reader
    reader = euler_prime_susds.get_historical_reader(stateful=False)
    assert isinstance(reader, EulerVaultHistoricalReader)
    calls = list(reader.construct_multicalls())
    call_names = [c.extra_data.get("function") for c in calls if c.extra_data]
    assert "cash" in call_names or "totalBorrows" in call_names


@flaky.flaky
def test_euler_metadata_products_json(
    web3: Web3,
    tmp_path: Path,
):
    """Verify the new products.json metadata fields for a vault that IS listed.

    Euler Prime WETH (0xD8b2...) is part of the "euler-prime" product.
    The product has two entity curators: euler-dao and gauntlet.

    Steps:

    1. Fetch metadata directly via fetch_euler_vault_metadata.
    2. Check backward-compatible fields (name, description, entity).
    3. Check new product-level fields (entities, product, product_name, deprecated).
    """
    # 1. Fetch metadata directly
    meta = fetch_euler_vault_metadata(web3, "0xD8b27CF359b7D15710a5BE299AF6e7Bf904984C2")

    assert meta is not None, "Euler Prime WETH should be present in products.json"

    # 2. Backward-compatible fields.
    # No per-vault name override in products.json, so name falls back to the product name.
    assert meta["name"] == "Euler Prime"
    assert meta["description"] is not None
    assert "blue chip" in meta["description"]
    # entity is the first element of the product's entity list (backward compat)
    assert meta["entity"] == "euler-dao"

    # 3. New fields from products.json
    assert meta["entities"] == ["euler-dao", "gauntlet"]
    assert meta["product"] == "euler-prime"
    assert meta["product_name"] == "Euler Prime"
    assert meta["deprecated"] is False
    assert meta["deprecation_reason"] is None
