"""Check Safe TradingStrategyModuleV0 against Orderly methods.

- Check we can perform delegate functions through TradingStrategyModuleV0
"""

import os

import pytest
from eth_typing import HexAddress
from web3 import HTTPProvider, Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract, get_function_selector
from eth_defi.deploy import deploy_contract
from eth_defi.hotwallet import HotWallet
from eth_defi.orderly.api import OrderlyApiClient
from eth_defi.orderly.vault import OrderlyVault, deposit
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


@pytest.fixture()
def orderly_account_id() -> HexAddress:
    return "0xca47e3fb4339d0e30c639bb30cf8c2d18cbb8687a27bc39249287232f86f8d00"


@pytest.fixture()
def safe(
    web3: Web3,
    usdc: Contract,
    deployer: str,
    owner: str,
    asset_manager: str,
) -> Contract:
    """Deploy MockSafe.

    - Has ``enableModule`` and ``module`` functions
    """
    safe = deploy_contract(web3, "safe-integration/MockSafe.json", deployer)

    # The module has ten variables that must be set:
    #
    #     Owner: Address that can call setter functions
    #     Avatar: Address of the DAO (e.g a Gnosis Safe)
    #     Target: Address on which the module will call execModuleTransaction()
    guard = deploy_contract(
        web3,
        "safe-integration/TradingStrategyModuleV0.json",
        owner,
        owner,
        safe.address,
    )

    assert guard.functions.owner().call() == owner
    assert guard.functions.avatar().call() == safe.address
    assert guard.functions.target().call() == safe.address

    # Enable Safe module
    tx_hash = safe.functions.enableModule(guard.address).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Enable asset_manager as the whitelisted trade-executor
    tx_hash = guard.functions.allowSender(asset_manager, "Whitelist trade-executor").transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Enable safe as the receiver of tokens
    tx_hash = guard.functions.allowReceiver(safe.address, "Whitelist Safe as trade receiver").transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)

    guard.functions.whitelistToken(usdc.address, "Allow USDC").transact({"from": owner})
    assert guard.functions.callSiteCount().call() == 2
    return safe


@pytest.fixture()
def guard(web3: Web3, safe: Contract) -> Contract:
    guard = get_deployed_contract(web3, "safe-integration/TradingStrategyModuleV0.json", safe.functions.module().call())
    return guard


def test_safe_module_initialised(
    owner: str,
    asset_manager: str,
    safe: Contract,
    guard: Contract,
    usdc: Contract,
):
    """Vault and guard are initialised for the owner."""
    assert guard.functions.owner().call() == owner
    assert guard.functions.isAllowedSender(asset_manager).call() is True
    assert guard.functions.isAllowedSender(safe.address).call() is False

    # We have accessed needed for a swap
    assert guard.functions.callSiteCount().call() == 2
    assert guard.functions.isAllowedCallSite(usdc.address, get_function_selector(usdc.functions.approve)).call()
    assert guard.functions.isAllowedCallSite(usdc.address, get_function_selector(usdc.functions.transfer)).call()
    assert guard.functions.isAllowedAsset(usdc.address).call()


def test_safe_module_delegate(
    mocker,
    web3: Web3,
    broker_id: str,
    guard: Contract,
    orderly_vault: OrderlyVault,
    hot_wallet: HotWallet,
    owner: str,
):
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
    tx = guard.functions.orderlyDelegateSigner(
        orderly_vault.address,
        (broker_hash, hot_wallet.address),
    ).transact({"from": owner, "gas": 500_000})
    assert_transaction_success_with_explanation(web3, tx)

    resp = OrderlyApiClient(
        account=hot_wallet.account,
        broker_id=broker_id,
        chain_id=web3.eth.chain_id,
        is_testnet=True,
    ).delegate_signer(
        delegate_contract=guard.address,
        delegate_tx_hash=tx.hex(),
    )
    assert resp["success"]
    assert resp["data"]["account_id"]
    assert resp["data"]["valid_signer"]
    assert resp["data"]["account_id"] == "ed25519:123456"
