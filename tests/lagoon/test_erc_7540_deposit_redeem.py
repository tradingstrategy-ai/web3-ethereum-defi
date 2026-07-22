"""ERC-7540 deposit/redeem tests."""

import datetime
import os
from decimal import Decimal
from typing import cast

import flaky
import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem import ERC7540DepositManager, ERC7540DepositRequest, ERC7540DepositTicket, ERC7540RedemptionTicket
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_WHALE, TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus, DepositRedeemEventAnalysis

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

CI = os.environ.get("CI", None) is not None


pytestmark = pytest.mark.skipif(not JSON_RPC_BASE, reason="No JSON_RPC_BASE environment variable")


@pytest.fixture()
def anvil_base_fork(request) -> AnvilLaunch:
    """Create a testable fork of Base.

    :return: JSON-RPC URL for Web3
    """
    assert JSON_RPC_BASE, "JSON_RPC_BASE not set"
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        fork_block_number=35_094_246,
        unlocked_addresses=[USDC_WHALE[8453]],
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def web3(anvil_base_fork) -> Web3:
    """Create a web3 connector.

    - By default use Anvil forked Base

    - Eanble Tenderly testnet with `JSON_RPC_TENDERLY` to debug
      otherwise impossible to debug Gnosis Safe transactions
    """

    tenderly_fork_rpc = os.environ.get("JSON_RPC_TENDERLY", None)

    if tenderly_fork_rpc:
        web3 = create_multi_provider_web3(tenderly_fork_rpc)
    else:
        web3 = create_multi_provider_web3(
            anvil_base_fork.json_rpc_url,
            retries=1,
            default_http_timeout=(3, 250.0),
        )
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture()
def usdc(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    )


@pytest.fixture()
def vault(web3) -> LagoonVault:
    """722Capital-USDC on Base.

    https://app.lagoon.finance/vault/8453/0xb09f761cb13baca8ec087ac476647361b6314f98
    """
    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xb09f761cb13baca8ec087ac476647361b6314f98",
    )
    lagoon_vault = cast(LagoonVault, vault)
    assert lagoon_vault.version == LagoonVersion.legacy
    return lagoon_vault


@pytest.fixture()
def test_user(web3, usdc):
    account = web3.eth.accounts[0]
    tx_hash = usdc.transfer(account, Decimal(10_000)).transact({"from": USDC_WHALE[web3.eth.chain_id]})
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert web3.eth.get_balance(account) > 10**18
    return account


def test_erc_7540_deposit_722_capital(
    vault: ERC4626Vault,
    test_user: HexAddress,
    usdc: TokenDetails,
):
    """Use DepositManager interface to deposit into ERC-7540 vault on Lagoon run by 722 Capital"""
    deposit_manager = vault.get_deposit_manager()
    assert isinstance(deposit_manager, ERC7540DepositManager)
    assert not deposit_manager.has_synchronous_deposit()
    assert not deposit_manager.is_deposit_in_progress(test_user)

    # Approve
    amount = Decimal(1_000)

    estimated = deposit_manager.estimate_deposit(test_user, amount)
    assert estimated == pytest.approx(Decimal("960.645332554006509231"))

    tx_hash = usdc.approve(
        vault.address,
        amount,
    ).transact({"from": test_user})

    # Deposit
    assert_transaction_success_with_explanation(vault.web3, tx_hash)
    request = deposit_manager.create_deposit_request(
        test_user,
        amount=amount,
    )
    assert isinstance(request, ERC7540DepositRequest)
    deposit_ticket = request.broadcast()
    assert isinstance(deposit_ticket, ERC7540DepositTicket)
    assert deposit_ticket.request_id == 33
    assert vault.share_token.fetch_balance_of(test_user) == 0
    assert deposit_manager.is_deposit_in_progress(test_user)
    assert not deposit_manager.can_finish_deposit(deposit_ticket)

    # Settle
    settlement = deposit_manager.force_settle(deposit_ticket)
    assert settlement.settlement_required is True
    assert settlement.ticket is deposit_ticket
    assert settlement.status_before is AsyncVaultRequestStatus.pending
    assert settlement.status_after is AsyncVaultRequestStatus.claimable
    assert settlement.transaction_hashes

    # Claim
    assert deposit_manager.can_finish_deposit(deposit_ticket)
    assert not deposit_manager.is_deposit_in_progress(test_user)
    func = deposit_manager.finish_deposit(deposit_ticket)
    tx_hash = func.transact({"from": test_user, "gas": 1_000_000})
    assert_transaction_success_with_explanation(vault.web3, tx_hash)

    # All clear
    assert vault.share_token.fetch_balance_of(test_user) > 0
    assert not deposit_manager.can_finish_deposit(deposit_ticket)
    assert not deposit_manager.is_deposit_in_progress(test_user)

    # Parse result
    deposit_result = deposit_manager.analyse_deposit(
        tx_hash,
        deposit_ticket=deposit_ticket,
    )

    assert isinstance(deposit_result, DepositRedeemEventAnalysis)
    assert deposit_result.from_ == test_user
    assert deposit_result.to == test_user
    assert deposit_result.tx_hash == tx_hash
    assert deposit_result.block_number == 35094252
    assert isinstance(deposit_result.block_timestamp, datetime.datetime)
    assert deposit_result.share_count == pytest.approx(Decimal("960.645517122092231912"))
    assert deposit_result.denomination_amount == pytest.approx(Decimal("1000"))
    assert deposit_result.get_share_price() == pytest.approx(Decimal("1.040966706424453185124846464"))


@flaky.flaky
def test_erc_7540_redeem_722_capital(
    vault: ERC4626Vault,
    test_user: HexAddress,
    usdc: TokenDetails,
):
    """Use DepositManager interface to redeem into ERC-7540 vault on Lagoon run by 722 Capital"""
    deposit_manager = vault.get_deposit_manager()
    assert isinstance(deposit_manager, ERC7540DepositManager)
    assert not deposit_manager.has_synchronous_redemption()
    assert not deposit_manager.is_deposit_in_progress(test_user)
    assert not deposit_manager.is_redemption_in_progress(test_user)

    # Approve
    amount = Decimal(1_000)
    tx_hash = usdc.approve(
        vault.address,
        amount,
    ).transact({"from": test_user})
    assert_transaction_success_with_explanation(vault.web3, tx_hash)

    # Deposit
    request = deposit_manager.create_deposit_request(
        test_user,
        amount=amount,
    )
    deposit_ticket = request.broadcast()

    # Settle
    deposit_settlement = deposit_manager.force_settle(deposit_ticket)
    assert deposit_settlement.settlement_required is True
    assert deposit_settlement.ticket is deposit_ticket
    assert deposit_settlement.status_before is AsyncVaultRequestStatus.pending
    assert deposit_settlement.status_after is AsyncVaultRequestStatus.claimable
    assert deposit_settlement.transaction_hashes

    # Claim
    func = deposit_manager.finish_deposit(deposit_ticket)
    tx_hash = func.transact({"from": test_user, "gas": 1_000_000})
    assert_transaction_success_with_explanation(vault.web3, tx_hash)

    # Got shares
    share_count = vault.share_token.fetch_balance_of(test_user)

    #
    # Redeem
    #

    # Approve
    tx_hash = vault.share_token.approve(
        vault.address,
        share_count,
    ).transact({"from": test_user})
    assert_transaction_success_with_explanation(vault.web3, tx_hash)

    estimated = deposit_manager.estimate_redeem(test_user, share_count)
    assert estimated == pytest.approx(Decimal("999.999657"))

    # Redeem
    request = deposit_manager.create_redemption_request(
        test_user,
        shares=share_count,
    )
    redeem_ticket = request.broadcast()
    assert isinstance(redeem_ticket, ERC7540RedemptionTicket)
    assert redeem_ticket.request_id == 6
    assert deposit_manager.is_redemption_in_progress(test_user)
    assert not deposit_manager.can_finish_redeem(redeem_ticket)

    # Settle
    redemption_settlement = deposit_manager.force_settle(redeem_ticket)
    assert redemption_settlement.settlement_required is True
    assert redemption_settlement.ticket is redeem_ticket
    assert redemption_settlement.status_before is AsyncVaultRequestStatus.pending
    assert redemption_settlement.status_after is AsyncVaultRequestStatus.claimable
    assert redemption_settlement.transaction_hashes

    # Claim
    assert deposit_manager.can_finish_redeem(redeem_ticket)
    func = deposit_manager.finish_redemption(redeem_ticket)
    tx_hash = func.transact({"from": test_user, "gas": 1_000_000})
    assert_transaction_success_with_explanation(vault.web3, tx_hash)

    # Shares gone
    share_count = vault.share_token.fetch_balance_of(test_user)
    assert share_count == 0

    redeem_result = deposit_manager.analyse_redemption(
        tx_hash,
        redeem_ticket,
    )

    assert isinstance(redeem_result, DepositRedeemEventAnalysis)
    assert redeem_result.from_ == test_user
    assert redeem_result.to == test_user
    assert redeem_result.tx_hash == tx_hash
    assert redeem_result.block_number == 35094257
    assert isinstance(redeem_result.block_timestamp, datetime.datetime)
    assert redeem_result.share_count == pytest.approx(Decimal("960.645517122092231912"))
    assert redeem_result.denomination_amount == pytest.approx(Decimal("1000"))
