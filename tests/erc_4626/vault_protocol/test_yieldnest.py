"""Test YieldNest vault metadata"""

import datetime
import os
from collections.abc import Iterator
from decimal import Decimal

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.yieldnest.vault import YNRWAX_VAULT_ADDRESS, YieldNestVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(not JSON_RPC_ETHEREUM, reason="JSON_RPC_ETHEREUM not set")


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> Iterator[AnvilLaunch]:
    """Fork at a specific block for reproducibility

    Contract created at block 22,674,309 in June 2024
    Latest block as of 2026-01-15: 24,239,327
    """
    launch = fork_network_anvil(
        JSON_RPC_ETHEREUM,
        fork_block_number=24_239_327,
        unlocked_addresses=[USDC_WHALE[1]],
    )
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_yieldnest_ynrwax(
    web3: Web3,
) -> None:
    """Read YieldNest ynRWAx vault metadata.

    This tests the hardcoded ynRWAx vault which is detected by address.
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=YNRWAX_VAULT_ADDRESS,
    )

    assert isinstance(vault, YieldNestVault)
    assert vault.get_protocol_name() == "YieldNest"
    assert vault.features == {ERC4626Feature.yieldnest_like}
    assert vault.get_deposit_manager_capability().as_dict() == {
        "can_deposit": True,
        "can_redeem": False,
        "deposit_flow": "synchronous",
        "redemption_unsupported_reason": "maturity_aware_redemption_flow_not_implemented",
    }
    assert vault.vault_contract.events.Deposit is not None
    assert vault.vault_contract.events.Withdraw is not None

    owner = web3.eth.accounts[0]
    deposit_amount = Decimal(10)
    usdc = vault.denomination_token
    funding_hash = usdc.transfer(owner, deposit_amount).transact({"from": USDC_WHALE[1]})
    assert_transaction_success_with_explanation(web3, funding_hash)
    approval_hash = usdc.approve(vault.address, deposit_amount).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, approval_hash)

    manager = vault.get_deposit_manager()
    deposit_ticket = manager.create_deposit_request(owner=owner, amount=deposit_amount).broadcast(from_=owner)
    analysis = manager.analyse_deposit(deposit_ticket.tx_hash, deposit_ticket)
    assert analysis.denomination_amount == deposit_amount
    assert analysis.share_count == Decimal("9.737608735845247309")
    assert vault.share_token.fetch_raw_balance_of(owner) == 9_737_608_735_845_247_309

    # Check that management and performance fees are zero
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0

    # Check risk level
    assert vault.get_risk() is VaultTechnicalRisk.low

    # Check lock-up period - ynRWAx has fixed maturity date of 15 Oct 2026
    lock_up = vault.get_estimated_lock_up()
    assert lock_up is not None
    assert isinstance(lock_up, datetime.timedelta)
    assert lock_up.days > 0  # Should be positive until maturity date

    # YieldNest doesn't support address(0) checks for maxDeposit/maxRedeem
    # (contract returns empty data)
    assert vault.can_check_deposit() is False
    assert vault.can_check_redeem() is False
