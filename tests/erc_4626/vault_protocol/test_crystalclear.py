"""Test CrystalClear vault metadata.

CrystalClear deploys ERC-4626 algorithmic trading vaults on HyperEVM
that trade perpetuals on HyperCore.

- Anvil fork is not used because CrystalClear's ``totalAssets()`` reads
  from HyperCore precompiles that Anvil cannot fork at historical blocks.
- Tests run against live HyperEVM RPC instead.
"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.crystalclear.vault import CrystalClearVault
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_HYPERLIQUID = os.environ.get("JSON_RPC_HYPERLIQUID")

pytestmark = pytest.mark.skipif(
    JSON_RPC_HYPERLIQUID is None,
    reason="JSON_RPC_HYPERLIQUID needed to run these tests",
)


@pytest.fixture(scope="module")
def web3() -> Web3:
    """Connect directly to HyperEVM RPC.

    Anvil fork cannot be used because CrystalClear vaults read
    HyperCore precompile state that is not available at historical blocks.
    """
    return create_multi_provider_web3(JSON_RPC_HYPERLIQUID)


@flaky.flaky
def test_crystalclear_onyx(web3: Web3, tmp_path: Path):
    """Read CrystalClear Onyx vault metadata and verify autodetection.

    1. Autodetect the vault protocol from on-chain probes
    2. Verify it resolves to CrystalClearVault with the correct feature flag
    3. Check protocol name, fees, and basic vault data
    """

    # 1. Autodetect vault
    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x231f66c336512e897855420a2788B83e164C6Adf",
    )

    # 2. Verify type and features
    assert isinstance(vault, CrystalClearVault)
    assert vault.features == {ERC4626Feature.crystalclear_like}
    assert vault.get_protocol_name() == "CrystalClear"

    # 3. Check fees — 0% management, 20% performance
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == pytest.approx(0.20)
    assert vault.has_custom_fees() is True

    # 4. Check basic vault data reads
    assert vault.denomination_token.symbol == "USDC"
    assert vault.denomination_token.decimals == 6
