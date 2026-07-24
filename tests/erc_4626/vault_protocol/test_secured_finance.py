"""Test Secured Finance vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.secured_finance.vault import (
    SECURED_FINANCE_JPYC_LENDER_VAULT_ADDRESS,
    SecuredFinanceVault,
)

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    # Shared with the other Ethereum midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Ethereum fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)


@flaky.flaky
def test_secured_finance_jpyc_lender_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Read Secured Finance JPYC lender vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=SECURED_FINANCE_JPYC_LENDER_VAULT_ADDRESS,
    )

    assert isinstance(vault, SecuredFinanceVault)
    assert vault.get_protocol_name() == "Secured Finance"
    assert vault.features == {ERC4626Feature.secured_finance_like}

    assert vault.name
    assert vault.symbol
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.get_link() == "https://vaults.secured.finance/"
