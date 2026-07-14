"""Exercise Accountable deposits and redemption requests on a Monad Anvil fork."""

import logging
import os
from decimal import Decimal

import pytest
from eth_typing import HexAddress, HexStr
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.accountable.deposit_redeem import AccountableDepositManager, AccountableRedemptionTicket
from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

JSON_RPC_MONAD = os.environ.get("JSON_RPC_MONAD")
SUSN_VAULT = HexAddress(HexStr("0x58ba69b289De313E66A13B7D1F822Fc98b970554"))
# A liquid native-USDC holder, verified immediately before each transaction by
# the transfer itself. Anvil unlocks it only inside the isolated fork.
MONAD_USDC_WHALE = HexAddress(HexStr("0xf89d7b9c864f589bbF53a82105107622B35EaA40"))
DEPOSIT_AMOUNT = Decimal("1000")

pytestmark = pytest.mark.skipif(JSON_RPC_MONAD is None, reason="JSON_RPC_MONAD needed to run these tests")


@pytest.fixture(scope="module")
def anvil_monad_accountable_fork() -> AnvilLaunch:
    """Fork latest Monad state with an isolated USDC holder unlocked.

    :return:
        Running Anvil fork process.
    """
    launch = fork_network_anvil(JSON_RPC_MONAD, unlocked_addresses=[MONAD_USDC_WHALE])
    try:
        yield launch
    finally:
        launch.close(log_level=logging.INFO)


@pytest.fixture(scope="module")
def web3(anvil_monad_accountable_fork: AnvilLaunch) -> Web3:
    """Connect to the Accountable Monad Anvil fork.

    :param anvil_monad_accountable_fork:
        Running fork process.
    :return:
        Fork-backed Web3 connection.
    """
    web3 = create_multi_provider_web3(anvil_monad_accountable_fork.json_rpc_url, retries=2)
    web3.provider.make_request("anvil_setBalance", [MONAD_USDC_WHALE, hex(10**20)])
    return web3


@pytest.fixture(scope="module")
def vault(web3: Web3) -> AccountableVault:
    """Open Accountable's live sUSN vault on the fork.

    :param web3:
        Monad Anvil fork connection.
    :return:
        Accountable vault adapter.
    """
    vault = create_vault_instance_autodetect(web3, SUSN_VAULT)
    assert isinstance(vault, AccountableVault)
    return vault


def test_accountable_deposit_and_redemption_request_lifecycle(web3: Web3, vault: AccountableVault) -> None:
    """Fund, deposit, and create a real Accountable redemption request.

    :param web3:
        Monad Anvil fork connection.
    :param vault:
        Accountable sUSN vault adapter.
    """
    manager = vault.get_deposit_manager()
    assert isinstance(manager, AccountableDepositManager)
    assert manager.has_synchronous_deposit() is True
    assert manager.has_synchronous_redemption() is False

    owner = web3.eth.accounts[0]
    usdc: TokenDetails = vault.denomination_token
    funding_hash = usdc.transfer(owner, DEPOSIT_AMOUNT).transact({"from": MONAD_USDC_WHALE})
    assert_transaction_success_with_explanation(web3, funding_hash)
    approval_hash = usdc.approve(vault.address, DEPOSIT_AMOUNT).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, approval_hash)

    deposit_ticket = manager.create_deposit_request(owner=owner, amount=DEPOSIT_AMOUNT).broadcast(from_=owner)
    deposit_analysis = manager.analyse_deposit(deposit_ticket.tx_hash, deposit_ticket)
    assert deposit_analysis.denomination_amount == DEPOSIT_AMOUNT
    assert deposit_analysis.share_count > 0

    raw_shares = vault.share_token.fetch_raw_balance_of(owner)
    assert raw_shares > 0
    redemption_request = manager.create_redemption_request(owner=owner, raw_shares=raw_shares)
    assert len(redemption_request.funcs) == 1
    assert redemption_request.funcs[0].fn_name == "requestRedeem"
    assert redemption_request.funcs[0].args == (raw_shares, owner, owner)

    ticket = redemption_request.broadcast(from_=owner)
    assert isinstance(ticket, AccountableRedemptionTicket)
    assert ticket.owner == owner
    assert ticket.controller == owner
    assert ticket.to == owner
    assert ticket.raw_shares == raw_shares
    assert manager.reconstruct_redemption_ticket(manager.serialize_redemption_ticket(ticket)) == ticket
    assert manager.get_redemption_request_status(ticket) in {
        AsyncVaultRequestStatus.pending,
        AsyncVaultRequestStatus.claimable,
    }
