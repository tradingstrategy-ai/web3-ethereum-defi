"""Test Orderly registration"""

import os

import pytest
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.hotwallet import HotWallet
from eth_defi.orderly.api import OrderlyApiClient
from eth_defi.orderly.vault import OrderlyVault
from eth_defi.trace import assert_transaction_success_with_explanation

pytestmark = pytest.mark.skipif(
    not any(
        [
            os.environ.get("JSON_RPC_ARBITRUM_SEPOLIA"),
            os.environ.get("HOT_WALLET_PRIVATE_KEY"),
        ]
    ),
    reason="No JSON_RPC_ARBITRUM_SEPOLIA or HOT_WALLET_PRIVATE_KEY environment variable",
)

SEND_REAL_REQUESTS = os.environ.get("SEND_REAL_REQUESTS") == "true"


@pytest.fixture()
def simple_vault(
    web3: Web3,
    usdc: Contract,
    deployer: str,
    owner: str,
    asset_manager: str,
) -> Contract:
    """Mock vault."""
    vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)

    assert vault.functions.owner().call() == deployer
    vault.functions.initialiseOwnership(owner).transact({"from": deployer})
    assert vault.functions.owner().call() == owner
    assert vault.functions.assetManager().call() == asset_manager

    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())
    assert guard.functions.owner().call() == owner

    guard.functions.whitelistToken(usdc.address, "Allow USDC").transact({"from": owner})

    assert guard.functions.callSiteCount().call() == 2

    return vault


def test_orderly_delegate_signer(
    mocker,
    web3: Web3,
    hot_wallet: HotWallet,
    broker_id: str,
    simple_vault: Contract,
    deployer: str,
    orderly_vault: OrderlyVault,
):
    if not SEND_REAL_REQUESTS:
        url_responses = {
            "https://testnet-api.orderly.org/v1/registration_nonce": {"success": True, "data": {"registration_nonce": "123456"}},
            "https://testnet-api.orderly.org/v1/delegate_signer": {"success": True, "data": {"user_id": 123456, "account_id": "ed25519:123456", "valid_signer": "0x0000000000000000000000000000000000000000"}},
        }

        def mock_by_url(url, *args, **kwargs):
            response_data = url_responses.get(url, {"success": False})
            return mocker.Mock(json=mocker.Mock(return_value=response_data))

        mocker.patch(
            "eth_defi.orderly.api.requests.get",
            side_effect=mock_by_url,
        )
        mocker.patch(
            "eth_defi.orderly.api.requests.post",
            side_effect=mock_by_url,
        )

    broker_hash = web3.keccak(text=broker_id)
    delegate_call = orderly_vault.contract.functions.delegateSigner((broker_hash, hot_wallet.address))

    # TODO: this should be fixed later so the delegate_call is invoked from SimpleVault contract
    tx = delegate_call.transact({"from": deployer, "gas": 500_000})
    assert_transaction_success_with_explanation(web3, tx)

    resp = OrderlyApiClient(
        account=hot_wallet.account,
        broker_id=broker_id,
        chain_id=web3.eth.chain_id,
        is_testnet=True,
    ).delegate_signer(
        delegate_contract=simple_vault.address,
        delegate_tx_hash=tx.hex(),
    )
    assert resp["success"]
    assert resp["data"]["account_id"]
    assert resp["data"]["valid_signer"]

    if not SEND_REAL_REQUESTS:
        assert resp["data"]["account_id"] == "ed25519:123456"
