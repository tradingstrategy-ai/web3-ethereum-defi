"""Test Aave (v4) Tokenization Spoke vault detection and metadata.

This test:

1. Forks Ethereum mainnet at a fixed block
2. Autodetects the Aave v4 ``CORE_USDC`` Tokenization Spoke as an :py:class:`AaveVault`
3. Asserts protocol classification, share/denomination tokens and ERC-4626 read values
"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.aave.vault import AaveVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")

#: Aave v4 CORE_USDC Tokenization Spoke on Ethereum mainnet
#: https://etherscan.io/address/0x531E90a2376902DE8915789Fcc1075e3B0c153E7
AAVE_CORE_USDC_SPOKE = "0x531E90a2376902DE8915789Fcc1075e3B0c153E7"


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork mainnet at a fixed block for reproducible values."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=25_354_000)
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_aave(
    web3: Web3,
    tmp_path: Path,
):
    """Read Aave (v4) Tokenization Spoke vault metadata.

    1. Autodetect the CORE_USDC spoke
    2. Check it classifies as Aave
    3. Check share/denomination tokens and fixed-block ERC-4626 reads
    """

    # 1. Autodetect the CORE_USDC spoke
    vault = create_vault_instance_autodetect(
        web3,
        vault_address=AAVE_CORE_USDC_SPOKE,
    )

    # 2. Check it classifies as Aave
    assert isinstance(vault, AaveVault)
    assert vault.features == {ERC4626Feature.aave_like}
    assert vault.get_protocol_name() == "Aave"
    assert vault.name == "Wrapped Aave Core USDC"

    # Share token is the wa{Hub}{Asset} ERC-20, denomination is the underlying USDC
    assert vault.share_token.symbol == "waCoreUSDC"
    assert vault.share_token.decimals == 6
    assert vault.denomination_token.symbol == "USDC"
    assert vault.denomination_token.address == "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

    # No explicit spoke-level fees; yield is internalised in the Hub-derived share price.
    # These are a known 0% (not unknown/None) at the Tokenization Spoke level.
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0

    # 3. Fixed-block ERC-4626 reads (absolute values at the pinned fork block)
    assert float(vault.fetch_share_price(25_354_000)) == pytest.approx(1.0074095943073379)
    assert float(vault.fetch_total_assets(25_354_000)) == pytest.approx(68.088438)
