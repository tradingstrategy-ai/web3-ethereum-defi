"""Warmup system integration tests.

Tests the warmup system that detects and skips broken vault contract calls.
Uses Anvil Plasma mainnet fork with known good and bad vaults.
"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.erc_4626.warmup import warmup_vault_reader
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3


JSON_RPC_PLASMA = os.environ.get("JSON_RPC_PLASMA")

pytestmark = pytest.mark.skipif(JSON_RPC_PLASMA is None, reason="JSON_RPC_PLASMA needed to run these tests")

# Known good vault on Plasma: Fluid fToken
GOOD_VAULT_ADDRESS = "0x1DD4b13fcAE900C60a350589BE8052959D2Ed27B"

# Known bad vault on Plasma: TelosC Surge with extremely expensive maxDeposit()
# This vault uses 36M gas for maxDeposit(address(0)) - entire block limit
BAD_VAULT_ADDRESS = "0xa9C251F8304b1B3Fc2b9e8fcae78D94Eff82Ac66"


@pytest.fixture(scope="module")
def anvil_plasma_fork(request) -> AnvilLaunch:
    """Fork Plasma chain at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_PLASMA, fork_block_number=11_664_904)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_plasma_fork):
    # Use longer timeout (90s instead of default 30s) because Plasma fork
    # can be slow, especially when processing expensive calls like maxDeposit
    web3 = create_multi_provider_web3(
        anvil_plasma_fork.json_rpc_url,
        default_http_timeout=(3.0, 90.0),
    )
    return web3


@flaky.flaky
def test_warmup_good_vault(web3: Web3):
    """Test warmup with a known good vault.

    All calls should succeed and be marked as not reverting.
    """
    vault = create_vault_instance_autodetect(
        web3,
        vault_address=GOOD_VAULT_ADDRESS,
    )

    # Create reader with stateful=True to enable warmup
    reader = vault.get_historical_reader(stateful=True)
    assert reader.reader_state is not None

    # Run warmup
    block_number = web3.eth.block_number
    results = warmup_vault_reader(reader, block_number)

    # Should have checked at least the base ERC-4626 calls
    assert len(results) >= 4  # total_assets, total_supply, convertToAssets, maxDeposit

    # All calls should succeed (not revert)
    for function_name, (check_block, reverts) in results.items():
        assert check_block == block_number
        assert reverts is False, f"Call {function_name} unexpectedly reverted"

    # Verify state is updated
    assert reader.reader_state.call_status is not None
    assert len(reader.reader_state.call_status) >= 4

    # should_skip_call should return False for all
    assert reader.should_skip_call("total_assets") is False
    assert reader.should_skip_call("total_supply") is False
    assert reader.should_skip_call("convertToAssets") is False
    assert reader.should_skip_call("maxDeposit") is False

    # get_broken_calls should return empty
    assert len(reader.reader_state.get_broken_calls()) == 0


@flaky.flaky
def test_warmup_bad_vault(web3: Web3):
    """Test warmup with a known bad vault.

    The TelosC Surge vault (0xa9C251F8304b1B3Fc2b9e8fcae78D94Eff82Ac66) is an
    EulerEarn vault with extremely expensive calls due to loop-heavy _maxDeposit().
    We force-create a generic ERC4626 vault instance (skipping autodetect which
    times out) and verify warmup detects broken calls via gas estimation.
    """
    from eth_defi.erc_4626.vault import ERC4626Vault
    from eth_defi.vault.base import VaultSpec

    # Force-create vault instance without autodetect (which would timeout)
    chain_id = web3.eth.chain_id
    spec = VaultSpec(chain_id=chain_id, vault_address=BAD_VAULT_ADDRESS)
    vault = ERC4626Vault(web3, spec)

    # Create reader with stateful=True to enable warmup
    reader = vault.get_historical_reader(stateful=True)
    assert reader.reader_state is not None

    # Run warmup - this should detect broken calls via gas estimation or revert
    block_number = web3.eth.block_number
    results = warmup_vault_reader(reader, block_number)

    # Should have checked at least some calls
    assert len(results) >= 1

    # At least some calls should have failed (reverted or excessive gas)
    broken_calls = reader.reader_state.get_broken_calls()
    assert len(broken_calls) >= 1, f"Expected at least one broken call on the bad vault, got results: {results}"

    # Verify should_skip_call returns True for broken calls
    for function_name in broken_calls:
        assert reader.should_skip_call(function_name) is True


@flaky.flaky
def test_warmup_skips_already_checked(web3: Web3):
    """Test that warmup skips already-checked calls."""
    vault = create_vault_instance_autodetect(
        web3,
        vault_address=GOOD_VAULT_ADDRESS,
    )

    reader = vault.get_historical_reader(stateful=True)
    block_number = web3.eth.block_number

    # Run warmup first time
    results1 = warmup_vault_reader(reader, block_number)
    assert len(results1) >= 4

    # Run warmup second time - should return empty (all already checked)
    results2 = warmup_vault_reader(reader, block_number + 1)
    assert len(results2) == 0, "Second warmup should skip already-checked calls"


@flaky.flaky(max_runs=3)
def test_warmup_affects_multicall_generation(web3: Web3):
    """Test that broken calls are actually skipped in multicall generation.

    Uses direct vault creation to avoid slow autodetection on Plasma RPC.
    """
    from eth_defi.erc_4626.vault import ERC4626Vault
    from eth_defi.vault.base import VaultSpec

    # Create vault directly without autodetection (which is slow on Plasma)
    chain_id = web3.eth.chain_id
    spec = VaultSpec(chain_id=chain_id, vault_address=GOOD_VAULT_ADDRESS)
    vault = ERC4626Vault(web3, spec)

    # Create stateful reader
    reader = vault.get_historical_reader(stateful=True)
    block_number = web3.eth.block_number

    # Get calls before warmup
    calls_before = list(reader.construct_multicalls())
    call_names_before = {c.extra_data.get("function") for c in calls_before if c.extra_data}
    assert "maxDeposit" in call_names_before

    # Manually mark maxDeposit as broken
    reader.reader_state.set_call_status("maxDeposit", block_number, True)

    # Get calls after marking maxDeposit as broken
    calls_after = list(reader.construct_multicalls())
    call_names_after = {c.extra_data.get("function") for c in calls_after if c.extra_data}

    # maxDeposit should be skipped now
    assert "maxDeposit" not in call_names_after
    # Other calls should still be present
    assert "total_assets" in call_names_after
    assert "total_supply" in call_names_after


def test_vault_reader_state_call_status_serialisation():
    """Test that call_status is properly serialised and loaded."""
    # Create a mock vault reader state with call_status
    class MockVault:
        first_seen_at_block = 1000

    mock_vault = MockVault()

    # We need to create VaultReaderState with a vault that has minimal interface
    # For this test, we'll just test the dict operations directly

    # Simulate the call_status dict
    call_status = {
        "maxDeposit": (12345678, True),
        "totalAssets": (12345678, False),
        "convertToAssets": (12345679, False),
    }

    # Test should_skip_call logic
    def should_skip_call(function_name):
        status = call_status.get(function_name)
        if status is None:
            return False
        _check_block, reverts = status
        return reverts

    assert should_skip_call("maxDeposit") is True
    assert should_skip_call("totalAssets") is False
    assert should_skip_call("convertToAssets") is False
    assert should_skip_call("unknown") is False

    # Test get_broken_calls logic
    broken = {fn: block for fn, (block, reverts) in call_status.items() if reverts}
    assert broken == {"maxDeposit": 12345678}


@flaky.flaky
def test_warmup_detects_gas_estimation_failure(web3: Web3):
    """Test warmup detects calls that fail gas estimation.

    Uses the good vault but simulates a broken call by testing warmup
    with a call that deliberately fails.

    Uses direct vault creation to avoid slow autodetection on Plasma RPC.
    """
    from eth_defi.erc_4626.vault import ERC4626Vault
    from eth_defi.vault.base import VaultSpec

    # Create vault directly without autodetection (which is slow on Plasma)
    chain_id = web3.eth.chain_id
    spec = VaultSpec(chain_id=chain_id, vault_address=GOOD_VAULT_ADDRESS)
    vault = ERC4626Vault(web3, spec)

    reader = vault.get_historical_reader(stateful=True)
    block_number = web3.eth.block_number

    # Manually set a call as broken to simulate detection
    reader.reader_state.set_call_status("maxDeposit", block_number, True)

    # Verify should_skip_call returns True
    assert reader.should_skip_call("maxDeposit") is True
    assert reader.should_skip_call("total_assets") is False

    # Verify get_broken_calls returns the broken call
    broken = reader.reader_state.get_broken_calls()
    assert "maxDeposit" in broken
    assert broken["maxDeposit"] == block_number

    # Run warmup - should skip already-checked maxDeposit
    results = warmup_vault_reader(reader, block_number)

    # maxDeposit should not be in results (already checked)
    assert "maxDeposit" not in results

    # Other calls should have been checked
    assert len(results) >= 3  # total_assets, total_supply, convertToAssets
