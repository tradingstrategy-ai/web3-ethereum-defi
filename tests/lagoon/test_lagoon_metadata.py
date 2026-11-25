"""Test Lagoon vault offchain metadata fetching.

- Lagoon stores vault descriptions in their web app API, not on-chain
- We reverse-engineered the API endpoints from the Lagoon Next.js JavaScript bundles
- These tests are skipped on CI because the Lagoon API at app.lagoon.finance
  returns HTTP 500 from GitHub Actions datacenter IPs (WAF/bot protection),
  while working fine from residential/office IPs
"""

import datetime
import os

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import detect_vault_features
from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
CI = os.environ.get("CI") == "true"

#: RockSolid rETH Vault on Ethereum - known to have descriptions in Lagoon's API
ROCKSOLID_VAULT_ADDRESS = "0x936facdf10c8c36294e7b9d28345255539d81bc7"

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    pytest.mark.skipif(CI, reason="Lagoon API returns HTTP 500 from GitHub Actions datacenter IPs"),
]


@pytest.fixture(scope="module")
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    return web3


@flaky.flaky
def test_lagoon_scan_record_has_descriptions(web3: Web3):
    """Verify that descriptions flow through create_vault_scan_record() for Lagoon vaults."""

    vault_address = Web3.to_checksum_address(ROCKSOLID_VAULT_ADDRESS)
    features = detect_vault_features(web3, vault_address, verbose=False)

    detection = ERC4262VaultDetection(
        chain=1,
        address=vault_address,
        first_seen_at_block=0,
        first_seen_at=datetime.datetime(2024, 1, 1),  # noqa: DTZ001
        features=features,
        updated_at=datetime.datetime(2024, 1, 1),  # noqa: DTZ001
        deposit_count=0,
        redeem_count=0,
    )

    token_cache = TokenDiskCache()
    block_number = web3.eth.block_number

    record = create_vault_scan_record(
        web3,
        detection,
        block_number,
        token_cache=token_cache,
    )

    assert record["Protocol"] == "Lagoon Finance"
    assert "rETH" in record["_description"]
    assert record["_short_description"]
    assert record["_manager_name"] == "Tulipa Capital"
