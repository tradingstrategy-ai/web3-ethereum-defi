"""Test Orderly registration"""

import os

import pytest
from web3 import Web3

from eth_defi.hotwallet import HotWallet
from eth_defi.orderly.api import OrderlyApiClient

pytestmark = pytest.mark.skip(
    reason="Orderly tests need an archive node for Arbitrum Sepolia — the public RPC does not provide archive state for the hardcoded fork block",
)

SEND_REAL_REQUESTS = os.environ.get("SEND_REAL_REQUESTS") == "true"


def test_orderly_register_account(
    mocker,
    web3: Web3,
    hot_wallet: HotWallet,
    broker_id: str,
):
    if not SEND_REAL_REQUESTS:
        url_responses = {
            "https://testnet-api.orderly.org/v1/registration_nonce": {"success": True, "data": {"registration_nonce": "123456"}},
            "https://testnet-api.orderly.org/v1/register_account": {"success": True, "data": {"account_id": "ed25519:123456"}},
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

    orderly_account_id = OrderlyApiClient(
        account=hot_wallet.account,
        broker_id=broker_id,
        chain_id=web3.eth.chain_id,
        is_testnet=True,
    ).register_account()

    assert orderly_account_id

    if not SEND_REAL_REQUESTS:
        assert orderly_account_id == "ed25519:123456"


def test_orderly_register_key(
    mocker,
    web3: Web3,
    hot_wallet: HotWallet,
    broker_id: str,
):
    if not SEND_REAL_REQUESTS:
        mocker.patch(
            "eth_defi.orderly.api.requests.post",
            return_value=mocker.Mock(
                json=mocker.Mock(
                    return_value={
                        "success": True,
                        "data": {
                            "id": 123456,
                            "orderly_key": "ed25519:4dnDhYH4EVkCpnUs6qLpJA5gND3BD45P4z953wGWfqt2",
                        },
                        "timestamp": 1751727048899,
                    }
                )
            ),
        )

    resp = OrderlyApiClient(
        account=hot_wallet.account,
        broker_id=broker_id,
        chain_id=web3.eth.chain_id,
        is_testnet=True,
    ).register_key()
    assert resp["success"]
    assert resp["data"]["orderly_key"]

    if not SEND_REAL_REQUESTS:
        assert resp["data"]["orderly_key"] == "ed25519:4dnDhYH4EVkCpnUs6qLpJA5gND3BD45P4z953wGWfqt2"
