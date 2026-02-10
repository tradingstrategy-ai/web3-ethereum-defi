"""ERC-7540 deposit/redeem tests."""

import os
import datetime
from decimal import Decimal
from typing import cast

import pytest
from web3 import Web3

from eth_defi.compat import WEB3_PY_V7
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem import ERC7540DepositManager, ERC7540DepositRequest, ERC7540DepositTicket, ERC7540RedemptionTicket
from eth_defi.erc_4626.vault_protocol.lagoon.testing import force_lagoon_settle
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details, TokenDetails, USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_typing import HexAddress

from eth_defi.utils import addr
from eth_defi.vault.deposit_redeem import DepositRedeemEventAnalysis

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

CI = os.environ.get("CI", None) is not None


WEB3_PY_V6 = not WEB3_PY_V7


pytestmark = pytest.mark.skipif(not JSON_RPC_BASE, reason="No JSON_RPC_BASE environment variable")


@pytest.fixture()
def vault_manager() -> HexAddress:
    # https://app.lagoon.finance/vault/8453/0xb09f761cb13baca8ec087ac476647361b6314f98
    return addr("0x3B95C7cD4075B72ecbC4559AF99211C2B6591b2E")


@pytest.fixture()
def anvil_base_fork(request, vault_manager) -> AnvilLaunch:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    assert JSON_RPC_BASE, "JSON_RPC_BASE not set"
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        fork_block_number=41_950_000,
        unlocked_addresses=[USDC_WHALE[8453], vault_manager],
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


#  FAILED tests/lagoon/test_erc_7540_deposit_redeem.py::test_erc_7540_deposit_722_capital - AssertionError: Cannot find Referral event in logs:
@pytest.mark.skipif(WEB3_PY_V6, reason="Web3.py v6 event log parsing is broken?")
def test_erc_7540_deposit_722_capital(
    vault: ERC4626Vault,
    test_user: HexAddress,
    usdc: TokenDetails,
    vault_manager: HexAddress,
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
    force_lagoon_settle(
        vault,
        vault_manager,
    )

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
    assert deposit_result.block_number >= 35094253
    assert isinstance(deposit_result.block_timestamp, datetime.datetime)
    assert deposit_result.share_count == pytest.approx(Decimal("960.645517122092231912"))
    assert deposit_result.denomination_amount == pytest.approx(Decimal("1000"))
    assert deposit_result.get_share_price() == pytest.approx(Decimal("1.040966706424453185124846464"))


@pytest.mark.skipif(WEB3_PY_V6, reason="Web3.py v6 event log parsing is broken?")
def test_erc_7540_redeem_722_capital(
    vault: ERC4626Vault,
    test_user: HexAddress,
    usdc: TokenDetails,
    vault_manager: HexAddress,
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
    force_lagoon_settle(
        vault,
        vault_manager,
    )

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
    force_lagoon_settle(
        vault,
        vault_manager,
    )

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
    assert redeem_result.block_number >= 35094253
    assert isinstance(redeem_result.block_timestamp, datetime.datetime)
    assert redeem_result.share_count == pytest.approx(Decimal("960.645517122092231912"))
    assert redeem_result.denomination_amount == pytest.approx(Decimal("1000"))
