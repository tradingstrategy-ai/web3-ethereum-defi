"""Scan Euler vault metadata"""

import os
from pathlib import Path

import pytest

from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.euler.vault import EulerVault
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
    """Read Euler vault metadata offchain"""

    euler_prime_susds = create_vault_instance_autodetect(
        web3,
        vault_address="0x1e548CfcE5FCF17247E024eF06d32A01841fF404",
    )

    assert isinstance(euler_prime_susds, EulerVault)
    assert euler_prime_susds.name == "Euler Prime sUSDS"
    assert euler_prime_susds.description == "A conservative sUSDS vault collateralized by other vaults in the Euler Prime cluster and escrow vaults."
    assert euler_prime_susds.entity == "euler-dao"
    assert euler_prime_susds.denomination_token.symbol == "sUSDS"
