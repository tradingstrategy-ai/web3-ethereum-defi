"""Test ForgeYields vault metadata.

ForgeYields is a cross-chain, non-custodial yield aggregator deploying into
frontier DeFi strategies underwritten by the Hallmark risk methodology.

1. Fork Ethereum at a known block
2. Auto-detect the fyUSDC vault via hardcoded address classification
3. Verify protocol name, features, fees, NAV, and vault link
"""

import json
import os
from functools import partial
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.forgeyields.offchain_metadata import fetch_forgeyields_strategies
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
def test_forgeyields(web3: Web3, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Read ForgeYields fyUSDC vault metadata.

    On-chain TVL is not available — the TokenGateway only holds a residual.
    fetch_total_assets() returns None. Offchain fetching is disabled, so
    fetch_nav() reads a retained snapshot in denomination token units.

    1. Auto-detect the vault via hardcoded address in HARDCODED_PROTOCOLS
    2. Verify it is identified as ForgeYieldsVault
    3. Check protocol name, features
    4. Verify fee data (20% performance, 0% management)
    5. Verify fetch_total_assets returns None (on-chain TVL not supported)
    6. Verify unavailable NAV without a cache and retained NAV with a snapshot
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

    # The fork validates contracts, not the retired API. Exercise the actual
    # read-only metadata loader against an isolated snapshot so CI needs no
    # live ForgeYields response and cannot overwrite an operator's saved copy.
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.forgeyields.vault.fetch_forgeyields_strategies", partial(fetch_forgeyields_strategies, cache_path=tmp_path))
    assert vault.fetch_nav() is None
    cached_tvl = "1069435.712178"
    snapshot = {FYUSDC_ADDRESS.lower(): {"name": "ForgeYields USDC", "symbol": "fyUSDC", "tvl_usd": "1085984.11", "tvl": cached_tvl, "apy": 25.07, "ethereum_gateway": FYUSDC_ADDRESS}}
    (tmp_path / "forgeyields_strategies.json").write_text(json.dumps(snapshot))
    nav = vault.fetch_nav()
    assert str(nav) == cached_tvl

    # 7. Verify vault link
    assert vault.get_link() == "https://app.forgeyields.com/"
