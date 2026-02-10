"""Scan Euler vault metadata"""

import os
from decimal import Decimal
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import is_lending_protocol
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

    # Test lending protocol identification
    assert is_lending_protocol(euler_prime_susds.features) is True

    # Test utilisation API
    available_liquidity = euler_prime_susds.fetch_available_liquidity()
    assert available_liquidity is not None
    assert available_liquidity >= Decimal(0)

    utilisation = euler_prime_susds.fetch_utilisation_percent()
    assert utilisation is not None
    assert 0.0 <= utilisation <= 1.0

    # Test historical reader
    reader = euler_prime_susds.get_historical_reader(stateful=False)
    assert isinstance(reader, EulerVaultHistoricalReader)
    calls = list(reader.construct_multicalls())
    call_names = [c.extra_data.get("function") for c in calls if c.extra_data]
    assert "cash" in call_names or "totalBorrows" in call_names
