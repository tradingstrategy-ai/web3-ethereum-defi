"""Mainnet fork test for Safe module integration."""
import os

import pytest

from eth_typing import HexAddress
from safe_eth.safe import Safe
from safe_eth.safe.safe import SafeV141
from web3 import Web3
from web3.middleware import construct_sign_and_send_raw_middleware

from eth_defi.deploy import deploy_contract
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.safe.safe_compat import create_safe_ethereum_client
from eth_defi.trace import assert_transaction_success_with_explanation

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

CI = os.environ.get("CI", None) is not None

pytestmark = pytest.mark.skipif(not JSON_RPC_BASE, reason="No JSON_RPC_BASE environment variable")


@pytest.fixture()
def deployer(web3) -> HexAddress:
    return web3.eth.accounts[0]


@pytest.fixture()
def safe_deployer_hot_wallet(web3) -> HotWallet:
    """Safe Python library only takes LocalAccount as the input for Safe.create()"""
    hot_wallet = HotWallet.create_for_testing(web3)
    web3.middleware_onion.add(construct_sign_and_send_raw_middleware(hot_wallet.account))
    return hot_wallet


@pytest.fixture()
def usdc_holder() -> HexAddress:
    # https://basescan.org/token/0x833589fcd6edb6e08f4c7c32d4f71b54bda02913#balances
    return "0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A"


@pytest.fixture()
def anvil_base_fork(request, usdc_holder) -> AnvilLaunch:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    assert JSON_RPC_BASE, "JSON_RPC_BASE not set"
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        unlocked_addresses=[usdc_holder],
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
        )
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture()
def safe(web3, deployer, safe_deployer_hot_wallet) -> Safe:
    """Deploy a Safe on the forked chain.

    - Use Safe version v1.4.1

    - 1 of 1 multisig

    - safe_deployer set as the sole owner
    """
    ethereum_client = create_safe_ethereum_client(web3)
    owners = [safe_deployer_hot_wallet.address]
    threshold = 1

    # Safe 1.4.1
    # https://help.safe.global/en/articles/40834-verify-safe-creation
    # https://basescan.org/address/0x41675C099F32341bf84BFc5382aF534df5C7461a
    master_copy_address = "0x41675C099F32341bf84BFc5382aF534df5C7461a"

    safe_tx = SafeV141.create(
        ethereum_client,
        safe_deployer_hot_wallet.account,
        master_copy_address,
        owners,
        threshold,
    )
    contract_address = safe_tx.contract_address
    safe = SafeV141(contract_address, ethereum_client)
    retrieved_owners = safe.retrieve_owners()
    assert retrieved_owners == owners
    return safe


def test_enable_safe_module(
    web3: Web3,
    safe: Safe,
    safe_deployer_hot_wallet: HotWallet,
    deployer: HexAddress,
):
    """Enable guard module on safe."""

    safe_contract = safe.contract

    # Deploy guard module
    module = deploy_contract(
        web3,
        "safe-integration/TradingStrategyModuleV0.json",
        deployer,
        safe.address,
        safe.address,
    )

    # Enable module
    tx = safe_contract.functions.enableModule(module.address).build_transaction(
        {"from": safe_deployer_hot_wallet.address, "gas": 0, "gasPrice": 0}
    )
    safe_tx = safe.build_multisig_tx(safe.address, 0, tx["data"])
    safe_tx.sign(safe_deployer_hot_wallet.private_key.hex())
    tx_hash, tx = safe_tx.execute(
        tx_sender_private_key=safe_deployer_hot_wallet.private_key.hex(),
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

