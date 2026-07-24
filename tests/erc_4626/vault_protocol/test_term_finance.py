"""Test Term Finance vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.term_finance.vault import TermFinanceVault
from eth_defi.vault.risk import VaultTechnicalRisk

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
def test_term_finance_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Read Term Finance vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xa10c40f9e318b0ed67ecc3499d702d8db9437228",
    )

    assert isinstance(vault, TermFinanceVault)
    assert vault.get_protocol_name() == "Term Finance"
    assert ERC4626Feature.term_finance_like in vault.features

    # Term Finance has internalised fees
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://app.term.finance/vaults/0xa10c40f9e318b0ed67ecc3499d702d8db9437228/1"

    # Risk level is None (to be assessed later)
    assert vault.get_risk() is VaultTechnicalRisk.low

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Term Finance doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
