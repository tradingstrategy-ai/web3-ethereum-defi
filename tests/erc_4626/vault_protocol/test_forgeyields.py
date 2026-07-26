"""Test ForgeYields vault metadata.

ForgeYields is a cross-chain, non-custodial yield aggregator deploying into
frontier DeFi strategies underwritten by the Hallmark risk methodology.

1. Fork Ethereum at a known block
2. Auto-detect the fyUSDC vault via hardcoded address classification
3. Verify protocol name, features, fees, NAV, and vault link
"""

import os

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.forgeyields.vault import ForgeYieldsVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    pytest.mark.xdist_group("fork:ethereum:25171000"),
]

#: fyUSDC vault on Ethereum
FYUSDC_ADDRESS = "0x943109DC7C950da4592d85ebd4Cfed007Af64670"


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Share the read-only ForgeYields fork and its warmed RPC cache."""
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, 25_171_000, web3_retries=2)


@flaky.flaky
def test_forgeyields(web3: Web3):
    """Read ForgeYields fyUSDC vault metadata.

    On-chain TVL is not available — the TokenGateway only holds a residual.
    fetch_total_assets() returns None. fetch_nav() returns the canonical
    TVL from the ForgeYields API in denomination token units.

    1. Auto-detect the vault via hardcoded address in HARDCODED_PROTOCOLS
    2. Verify it is identified as ForgeYieldsVault
    3. Check protocol name, features
    4. Verify fee data (20% performance, 0% management)
    5. Verify fetch_total_assets returns None (on-chain TVL not supported)
    6. Verify fetch_nav returns denomination-token TVL from API
    7. Verify vault link
    """
    # 1. Auto-detect the vault
    vault = create_vault_instance_autodetect(
        web3,
        vault_address=FYUSDC_ADDRESS,
    )

    # 2. Verify vault type
    assert isinstance(vault, ForgeYieldsVault)

    # 3. Check protocol name and features
    assert vault.get_protocol_name() == "ForgeYields"
    assert ERC4626Feature.forgeyields_like in vault.features

    # 4. Verify fee data
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == pytest.approx(0.20)

    # 5. Verify fetch_total_assets returns None (on-chain TVL not supported)
    assert vault.fetch_total_assets("latest") is None

    # 6. Verify fetch_nav returns denomination-token TVL from API (USDC)
    nav = vault.fetch_nav()
    assert nav is not None
    assert nav > 10_000  # Should be ~1M USDC

    # 7. Verify vault link
    assert vault.get_link() == "https://app.forgeyields.com/"
