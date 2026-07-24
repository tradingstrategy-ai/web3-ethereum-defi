"""Superform protocol tests"""

import os
from pathlib import Path

import pytest

from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.superform.vault import SuperformVault


from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    # Shared with the other Arbitrum midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Arbitrum fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@flaky.flaky
def test_superform_protocol(
    web3: Web3,
    tmp_path: Path,
):
    """Superform vault https://app.superform.xyz/vault/1_0x0655977feb2f289a4ab78af67bab0d17aab84367

    https://arbiscan.io/address/0xa7781f1d982eb9000bc1733e29ff5ba2824cdbe5#readContract
    """

    # TODO: Both Goat and Superform use the exactly same vault contract Multistrategy
    # https://github.com/goatfi/contracts/blob/main/src/infra/multistrategy/Multistrategy.sol

    # vault = create_vault_instance_autodetect(
    #     web3,
    #     vault_address="0xa7781f1d982eb9000bc1733e29ff5ba2824cdbe5",
    # )
    #
    # assert vault.features == {ERC4626Feature.superform_like}
    # assert isinstance(vault, SuperformVault), f"Got: {type(vault)}: {vault}"
    # assert vault.get_protocol_name() == "Superform"
    # assert vault.name == "Yield Chasing crvUSD"
    # assert vault.denomination_token.symbol == "crvUSD"
