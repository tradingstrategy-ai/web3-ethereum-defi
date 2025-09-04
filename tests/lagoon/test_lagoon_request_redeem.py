"""Check Lagoon / ERC-7545 redemption cycle."""
import os
from decimal import Decimal

import pytest

from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, USDC_NATIVE_TOKEN
from eth_defi.trace import assert_transaction_success_with_explanation

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

CI = os.environ.get("CI", "true") == "true"

pytestmark = pytest.mark.skipif(not JSON_RPC_BASE, reason="No JSON_RPC_BASE environment variable")


@pytest.fixture()
def anvil_base_fork(request, vault_owner, usdc_holder, asset_manager, valuation_manager, test_block_number) -> AnvilLaunch:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    assert JSON_RPC_BASE, "JSON_RPC_BASE not set"
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        unlocked_addresses=[vault_owner, usdc_holder, asset_manager, valuation_manager],
        fork_block_number=test_block_number,
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
            default_http_timeout=(3, 250.0),  # multicall slow, so allow improved timeout
            retries=1,
        )
    assert web3.eth.chain_id == 8453
    return web3

@pytest.fixture()
def usdc(web3) -> TokenDetails:
    usdc = fetch_erc20_details(
        web3,
        USDC_NATIVE_TOKEN[8453],
    )
    return usdc


@pytest.fixture()
def test_user(web3, usdc):
    account = web3.eth.accounts[0]
    tx_hash = usdc.transfer(account, Decimal(10_000)).transact({"from": USDC_WHALE[8453]})
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert web3.eth.get_balance(account) > 10**18
    return account


def test_lagoon_deposit_redeem(
    web3: Web3,
    test_user,
    usdc: TokenDetails,
):
    """Do deposit/redeem cycle on Lagoon vault.

    - Use unified create_redemption_request() interface
    """
    web3 = web3
    # https://app.lagoon.finance/vault/1/0x03d1ec0d01b659b89a87eabb56e4af5cb6e14bfc
    vault: LagoonVault = create_vault_instance_autodetect(web3, "0x03d1ec0d01b659b89a87eabb56e4af5cb6e14bfc")

    amount = Decimal(100)

    tx_hash = usdc.approve(
        vault.address,
        amount,
    ).transact({"from": test_user})
    assert_transaction_success_with_explanation(web3, tx_hash)

    bound_func = deposit_4626(
        vault,
        test_user,
        amount,
    )
    tx_hash = bound_func.transact({"from": test_user})
    assert_transaction_success_with_explanation(web3, tx_hash)

    share_token = vault.share_token
    shares = share_token.fetch_balance_of(test_user)
    assert shares == pytest.approx(Decimal("91.061642"))

    # Withdrawals can be only executed on the first two days of an epoch.
    # We start in a state that is outside of this window, so we need to move to the next epoch first.
    assert vault.open_pnl_contract.functions.nextEpochValuesRequestCount().call() == 0
    assert vault.can_create_redemption_request(test_user) is True

    # 1. Create a redemption request
    assert vault.open_pnl_contract.functions.nextEpochValuesRequestCount().call() == 0
    assert vault.can_create_redemption_request(test_user) is True, f"We have {vault.open_pnl_contract.functions.nextEpochValuesRequestCount().call()}"
    redemption_request = vault.create_redemption_request(
        owner=test_user,
        shares=shares,
    )
    assert isinstance(redemption_request, ERCRedemptionRequest)
    assert redemption_request.owner == test_user
    assert redemption_request.to == test_user
    assert redemption_request.shares == shares

    # 2.a) Broadcast and parse redemption request tx
    assert vault.open_pnl_contract.functions.nextEpochValuesRequestCount().call() == 0
    tx_hashes = []
    funcs = redemption_request.funcs
    tx_hash = funcs[0].transact({"from": test_user, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hashes.append(tx_hash)

    # 2.b) Parse result
    redemption_ticket = redemption_request.parse_redeem_transaction(tx_hashes)
    assert redemption_ticket.raw_shares == pytest.approx(91.061642 * 10**6)
    assert redemption_ticket.owner == test_user
    assert redemption_ticket.to == test_user
    assert redemption_ticket.current_epoch == 122
    assert redemption_ticket.unlock_epoch == 125

    # Cannot redeem yet, need to wait for the next epoch
    assert vault.can_finish_redeem(redemption_ticket) is False

    # 3. Move forward few epochs where our request unlocks
    for i in range(0, 3):
        force_next_gains_epoch(
            vault,
            test_user,
        )

    assert vault.fetch_current_epoch() >= 125

    # Cannot redeem yet, need to wait for the next epoch
    assert vault.can_finish_redeem(redemption_ticket) is True

    # 4. Settle our redemption
    func = vault.settle_redemption(redemption_ticket)
    tx_hash = func.transact({"from": test_user})
    assert_transaction_success_with_explanation(web3, tx_hash)

    shares = share_token.fetch_balance_of(test_user)
    assert shares == 0
