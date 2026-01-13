"""Test Brink vault scanning on Mantle using HyperSync.

Brink is a DeFi protocol providing yield-bearing vaults on Mantle and other chains.
This test verifies that Brink's specialised Deposited/Withdrawal events
are correctly picked up by the HyperSync vault scanner.

- Homepage: https://brink.money/
- App: https://brink.money/app
- Documentation: https://doc.brink.money/
"""

import os

import flaky
import hypersync
import pytest

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.hypersync_discovery import HypersyncVaultDiscover
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory

JSON_RPC_MANTLE = os.environ.get("JSON_RPC_MANTLE")
HYPERSYNC_API_KEY = os.environ.get("HYPERSYNC_API_KEY")

pytestmark = pytest.mark.skipif(JSON_RPC_MANTLE is None or HYPERSYNC_API_KEY is None, reason="JSON_RPC_MANTLE and HYPERSYNC_API_KEY needed to run these tests")

BRINK_VAULT_ADDRESS = "0xE12EED61E7cC36E4CF3304B8220b433f1fD6e254"
DEPOSIT_BLOCK = 89361462  # Block with known Deposited event


@flaky.flaky
def test_4626_scan_brink_mantle():
    """Test that Brink Deposited events are picked up on Mantle.

    Uses HyperSync to scan for vault events and verifies:
    - Brink vault is detected via its Deposited event
    - Vault is correctly classified as brink_like
    - Deposit count includes the known deposit

    Reference transaction:
    https://mantlescan.xyz/tx/0x24f657a17f3039d41141a4aa4cef01110112696fc1f88db56a3a113596482a48

    Note: Scanner requires both deposit and withdrawal events to fully classify
    a vault. Use a wider block range to capture both event types.
    """
    web3 = create_multi_provider_web3(JSON_RPC_MANTLE)
    web3factory = MultiProviderWeb3Factory(JSON_RPC_MANTLE)

    hypersync_url = get_hypersync_server(web3)
    client = hypersync.HypersyncClient(hypersync.ClientConfig(url=hypersync_url, bearer_token=HYPERSYNC_API_KEY))

    # Use wider block range to ensure we capture both deposit and withdrawal events
    # (scanner requires both to classify a vault as candidate)
    start_block = DEPOSIT_BLOCK - 100_000
    end_block = DEPOSIT_BLOCK + 100_000

    vault_discover = HypersyncVaultDiscover(
        web3,
        web3factory,
        client,
    )

    report = vault_discover.scan_vaults(start_block, end_block, display_progress=False)

    # Verify the Brink vault was detected
    brink_detection = report.detections.get(BRINK_VAULT_ADDRESS.lower())
    assert brink_detection is not None, f"Brink vault {BRINK_VAULT_ADDRESS} not found in detections"

    # Verify it has brink_like feature (meaning it was classified as Brink protocol)
    assert ERC4626Feature.brink_like in brink_detection.features

    # Verify deposit was counted (from Deposited event)
    assert brink_detection.deposit_count >= 1

    # Verify withdrawal was counted (from Withdrawal event)
    assert brink_detection.redeem_count >= 1
