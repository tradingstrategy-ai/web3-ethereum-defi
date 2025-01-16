"""Test HSM wallet implementation on Ethereum mainnet fork.

To run tests in this module:

.. code-block:: shell
    export GOOGLE_CLOUD_PROJECT="your-project"
    export GOOGLE_CLOUD_REGION="us-east1"
    export KEY_RING="eth-keys"
    export KEY_NAME="signing-key"
    export GCP_ADC_CREDENTIALS_STRING='{"ADC_credentials": "values"}'
    pytest -k test_hsm_hotwallet
"""

import json
import os
import logging
import shutil

from eth_defi.gas import apply_gas, estimate_gas_fees
import pytest
from web3 import EthereumTesterProvider, Web3
from web3.contract import Contract
from web3_google_hsm.config import BaseConfig

from eth_defi.token import create_token
from eth_defi.tx import decode_signed_transaction
from eth_defi.gcloud_hsm_wallet import GCloudHSMWallet
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment, deploy_uniswap_v2_like


# Skip tests if required env vars are not set
pytestmark = pytest.mark.skipif(not all([os.environ.get("GOOGLE_CLOUD_PROJECT"), os.environ.get("GOOGLE_CLOUD_REGION"), os.environ.get("KEY_RING"), os.environ.get("KEY_NAME"), os.environ.get("GCP_ADC_CREDENTIALS_STRING"), shutil.which("anvil")]), reason="Set Google Cloud env vars, and install anvil to run these tests")

# Set up logging
logger = logging.getLogger(__name__)


@pytest.fixture
def tester_provider():
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return EthereumTesterProvider()


@pytest.fixture
def eth_tester(tester_provider):
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return tester_provider.ethereum_tester


@pytest.fixture
def web3(tester_provider):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return Web3(tester_provider)


@pytest.fixture()
def deployer(web3) -> str:
    """Deploy account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[0]


@pytest.fixture()
def usdc(web3, deployer) -> Contract:
    """Mock USDC token.

    Note that this token has 18 decimals instead of 6 of real USDC.
    """
    token = create_token(web3, deployer, "USD Coin", "USDC", 100_000_000 * 10**18)
    return token


@pytest.fixture()
def dai(web3, deployer) -> Contract:
    """Mock DAI token."""
    token = create_token(web3, deployer, "Dai Stablecoin", "DAI", 100_000_000 * 10**18)
    return token


@pytest.fixture()
def uniswap_v2(web3, deployer) -> UniswapV2Deployment:
    """Uniswap v2 deployment."""
    deployment = deploy_uniswap_v2_like(web3, deployer)
    return deployment


@pytest.fixture()
def weth(uniswap_v2) -> Contract:
    """Mock WETH token."""
    return uniswap_v2.weth


@pytest.fixture
def gcp_config() -> BaseConfig:
    """Create GCP config from environment variables."""
    return BaseConfig(project_id=os.environ["GOOGLE_CLOUD_PROJECT"], location_id=os.environ["GOOGLE_CLOUD_REGION"], key_ring_id=os.environ["KEY_RING"], key_id=os.environ["KEY_NAME"])


@pytest.fixture
def gcp_credentials() -> dict:
    """Load GCP credentials from environment variable."""
    return json.loads(os.environ["GCP_ADC_CREDENTIALS_STRING"])


@pytest.fixture
def hsm_wallet(web3: Web3, gcp_credentials: dict) -> GCloudHSMWallet:
    """HSM wallet implementation using loaded config and credentials."""
    wallet = GCloudHSMWallet(credentials=gcp_credentials)
    wallet.sync_nonce(web3)
    return wallet


def test_eth_native_transfer(web3: Web3, deployer: str, hsm_wallet: GCloudHSMWallet):
    """Test native ETH transfer using HSM wallet."""

    # Fund HSM wallet with ETH
    fund_tx = {"from": deployer, "to": hsm_wallet.address, "value": web3.to_wei(2, "ether")}
    fund_tx_hash = web3.eth.send_transaction(fund_tx)
    fund_receipt = web3.eth.wait_for_transaction_receipt(fund_tx_hash)
    assert fund_receipt["status"] == 1

    wallet_balance = web3.eth.get_balance(hsm_wallet.address)
    logger.debug(f"HSM Wallet balance: {web3.from_wei(wallet_balance, 'ether')} ETH")
    assert wallet_balance >= web3.to_wei(2, "ether")

    # Prepare ETH transfer
    recipient = "0x0000000000000000000000000000000000000000"
    tx = {"from": hsm_wallet.address, "to": recipient, "value": web3.to_wei(1, "ether"), "gas": 21000, "gasPrice": web3.eth.gas_price, "chainId": web3.eth.chain_id}

    # Sign and send
    signed_tx = hsm_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    logger.debug(f"Transaction receipt: {receipt}")
    assert receipt["status"] == 1

    # Verify balances
    final_recipient_balance = web3.eth.get_balance(recipient)
    assert final_recipient_balance >= web3.to_wei(1, "ether")

    final_wallet_balance = web3.eth.get_balance(hsm_wallet.address)
    assert final_wallet_balance < web3.to_wei(1, "ether")  # Less than 1 ETH due to gas costs


def test_dai_sign_bound_call(web3: Web3, dai: Contract, deployer: str, hsm_wallet: GCloudHSMWallet):
    """Test sign_bound_call_with_new_nonce with different parameter combinations."""

    # Fund HSM wallet with ETH for gas
    fund_tx = {"from": deployer, "to": hsm_wallet.address, "value": web3.to_wei(1, "ether")}
    fund_tx_hash = web3.eth.send_transaction(fund_tx)
    web3.eth.wait_for_transaction_receipt(fund_tx_hash)

    # Send some DAI to HSM wallet
    initial_dai = 2000 * 10**18
    dai.functions.transfer(hsm_wallet.address, initial_dai).transact({"from": deployer})

    # Test Case 1: With gas estimation
    hsm_wallet.sync_nonce(web3)
    spender1 = "0x0000000000000000000000000000000000000001"
    approve_func1 = dai.functions.approve(spender1, 100 * 10**18)
    gas_estimation = estimate_gas_fees(web3)
    tx_gas_parameters = apply_gas({"gas": 100_000}, gas_estimation)
    tx_gas_parameters["gasPrice"] = web3.eth.gas_price
    signed_tx1 = hsm_wallet.sign_bound_call_with_new_nonce(approve_func1, tx_gas_parameters)
    tx_hash1 = web3.eth.send_raw_transaction(signed_tx1.rawTransaction)
    receipt1 = web3.eth.wait_for_transaction_receipt(tx_hash1)
    assert receipt1["status"] == 1

    # Test Case 2: Direct gas parameters
    hsm_wallet.sync_nonce(web3)
    spender2 = "0x0000000000000000000000000000000000000002"
    approve_func2 = dai.functions.approve(spender2, 200 * 10**18)
    tx_params2 = {
        "gas": 100_000,
        "gasPrice": web3.eth.gas_price * 2,  # Higher gas price
    }
    signed_tx2 = hsm_wallet.sign_bound_call_with_new_nonce(approve_func2, tx_params2)
    tx_hash2 = web3.eth.send_raw_transaction(signed_tx2.rawTransaction)
    receipt2 = web3.eth.wait_for_transaction_receipt(tx_hash2)
    assert receipt2["status"] == 1

    # Test Case 3: Fill gas price automatically
    hsm_wallet.sync_nonce(web3)
    spender3 = "0x0000000000000000000000000000000000000003"
    approve_func3 = dai.functions.approve(spender3, 300 * 10**18)
    signed_tx3 = hsm_wallet.sign_bound_call_with_new_nonce(approve_func3, tx_params={"gas": 100_000, "gasPrice": web3.eth.gas_price * 2}, web3=web3, fill_gas_price=True)
    tx_hash3 = web3.eth.send_raw_transaction(signed_tx3.rawTransaction)
    receipt3 = web3.eth.wait_for_transaction_receipt(tx_hash3)
    assert receipt3["status"] == 1

    # Verify all allowances
    allowance1 = dai.functions.allowance(hsm_wallet.address, spender1).call()
    allowance2 = dai.functions.allowance(hsm_wallet.address, spender2).call()
    allowance3 = dai.functions.allowance(hsm_wallet.address, spender3).call()

    assert allowance1 == 100 * 10**18
    assert allowance2 == 200 * 10**18
    assert allowance3 == 300 * 10**18


def test_eth_mainnet_hsm_tx_setup(web3: Web3, dai, deployer: str):
    """Test to logger.debug useful debugging information about the test environment."""

    # logger.debug deployer info
    deployer_balance = web3.eth.get_balance(deployer)
    logger.debug(f"\nDeployer address: {deployer}")
    logger.debug(f"Deployer balance: {web3.from_wei(deployer_balance, 'ether')} ETH")

    deployer_dai_balance = dai.functions.balanceOf(deployer).call()
    logger.debug(f"Deployer DAI balance: {deployer_dai_balance / 10**6} DAI")

    # logger.debug chain info
    logger.debug(f"Chain ID: {web3.eth.chain_id}")
    logger.debug(f"Block number: {web3.eth.block_number}")
    logger.debug(f"Gas price: {web3.from_wei(web3.eth.gas_price, 'gwei')} gwei")


def test_eth_erc20_approval(web3: Web3, weth, deployer, hsm_wallet: GCloudHSMWallet):
    """Test ERC-20 approve function with WETH."""

    logger.debug("\nWETH contract details:")
    logger.debug(f"Name: {weth.name}")

    # Fund wallet with ETH for gas
    fund_tx = {"from": deployer, "to": hsm_wallet.address, "value": web3.to_wei(1, "ether")}
    fund_tx_hash = web3.eth.send_transaction(fund_tx)
    receipt = web3.eth.wait_for_transaction_receipt(fund_tx_hash)
    assert receipt["status"] == 1
    logger.debug(f"\nHSM wallet funded with {web3.from_wei(web3.eth.get_balance(hsm_wallet.address), 'ether')} ETH")

    # Test approval
    spender = "0x0000000000000000000000000000000000000000"
    approve_amount = 1000 * 10**18  # 1000 WETH (18 decimals)

    # Check initial allowance
    initial_allowance = weth.functions.allowance(hsm_wallet.address, spender).call()
    logger.debug(f"\nInitial WETH allowance: {initial_allowance}")

    # Use transact_with_contract for approval
    signed_tx = hsm_wallet.transact_with_contract(
        weth.functions.approve,
        spender,
        approve_amount,
        gasPrice=web3.eth.gas_price * 2,  # Higher gas price for faster inclusion
    )

    # Verify transaction data
    decoded_tx = decode_signed_transaction(signed_tx.rawTransaction)
    logger.debug(f"\nDecoded transaction: {decoded_tx}")
    assert decoded_tx["to"].hex().lower() == weth.address.lower()
    assert decoded_tx["data"].hex().startswith("0x095ea7b3")  # approve() selector

    # Send and verify transaction
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    logger.debug(f"\nApproval transaction receipt: {receipt}")
    assert receipt["status"] == 1

    # Verify allowance
    new_allowance = weth.functions.allowance(hsm_wallet.address, spender).call()
    logger.debug(f"\nNew WETH allowance: {new_allowance}")
    assert new_allowance == approve_amount


def test_create_for_testing_with_auth(web3: Web3, gcp_config: BaseConfig, gcp_credentials: dict):
    """Test creating a test wallet with explicit authentication."""
    wallet = GCloudHSMWallet.create_for_testing(web3=web3, config=gcp_config, credentials=gcp_credentials, eth_amount=1)

    # Verify wallet is funded and functional
    balance = web3.eth.get_balance(wallet.address)
    assert balance == web3.to_wei(1, "ether")

    # Test a simple transfer
    recipient = "0x0000000000000000000000000000000000000000"
    tx = {"from": wallet.address, "to": recipient, "value": web3.to_wei(0.1, "ether"), "gas": 21000, "gasPrice": web3.eth.gas_price, "chainId": web3.eth.chain_id}

    signed_tx = wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1


# Hsm client doesn't support EIP-1559 yet
# def test_eth_eip1559_gas(web3: Web3, large_usdc_holder: HexAddress, hsm_wallet: GCloudHSMWallet):
#     """Test EIP-1559 gas calculations with HSM wallet."""

#     # Fund wallet
#     fund_tx = {
#         "from": large_usdc_holder,
#         "to": hsm_wallet.address,
#         "value": web3.to_wei(1, "ether")
#     }
#     fund_tx_hash = web3.eth.send_transaction(fund_tx)
#     web3.eth.wait_for_transaction_receipt(fund_tx_hash)

#     # Prepare transaction
#     tx = {
#         "from": hsm_wallet.address,
#         "to": "0x0000000000000000000000000000000000000000",
#         "value": web3.to_wei(0.1, "ether"),
#         "gas": 21000,
#         "chainId": web3.eth.chain_id
#     }

#     # Fill in gas
#     filled_tx = hsm_wallet.fill_in_gas_price(web3, tx)

#     # Verify EIP-1559 fields
#     assert "maxFeePerGas" in filled_tx
#     assert "maxPriorityFeePerGas" in filled_tx
#     assert filled_tx["maxFeePerGas"] >= filled_tx["maxPriorityFeePerGas"]

#     # Test transaction with calculated gas values
#     signed_tx = hsm_wallet.sign_transaction_with_new_nonce(filled_tx)
#     tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
#     receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
#     assert receipt["status"] == 1
