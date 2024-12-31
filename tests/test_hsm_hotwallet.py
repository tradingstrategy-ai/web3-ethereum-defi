"""Test HSM wallet implementation on Ethereum mainnet fork.

To run tests in this module:

.. code-block:: shell

    export ETH_NODE_URI="https://eth-mainnet.alchemyapi.io/v2/YOUR-API-KEY"
    export GOOGLE_CLOUD_PROJECT="your-project"
    export GOOGLE_CLOUD_REGION="us-east1"
    export KEY_RING="eth-keys"
    export KEY_NAME="signing-key"
    export GOOGLE_APPLICATION_CREDENTIALS="/path/to/credentials.json"
    pytest -k test_hsm_wallet
"""

import os
import logging
import shutil

from cchecksum import to_checksum_address
import pytest
from eth_typing import ChecksumAddress, HexAddress
from web3 import Web3

from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.tx import decode_signed_transaction
from eth_defi.hsm_hotwallet import HSMWallet


# Skip tests if required env vars are not set
pytestmark = pytest.mark.skipif(not all([os.environ.get("ETH_NODE_URI"), os.environ.get("GOOGLE_CLOUD_PROJECT"), os.environ.get("GOOGLE_CLOUD_REGION"), os.environ.get("KEY_RING"), os.environ.get("KEY_NAME"), shutil.which("anvil")]), reason="Set ETH_NODE_URI and Google Cloud env vars, and install anvil to run these tests")

# Set up logging
logger = logging.getLogger(__name__)


def setup_module(module):
    """Set up logging for the test module."""
    # Get log level from environment, default to WARNING
    log_level = os.environ.get("LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(level=log_level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


@pytest.fixture()
def weth_holder() -> ChecksumAddress:
    """Account that holds a lot of WETH on mainnet."""
    # Binance 8
    return to_checksum_address("0xf977814e90da44bfa03b6295a0616a897441acec")


@pytest.fixture()
def anvil_eth_fork(request, weth_holder) -> str:
    """Create a testable fork of Ethereum mainnet."""
    mainnet_rpc = os.environ["ETH_NODE_URI"]
    launch = fork_network_anvil(mainnet_rpc, unlocked_addresses=[weth_holder], fork_block_number=17_500_000)  # Pick a stable recent block
    try:
        yield launch.json_rpc_url
    finally:
        # Wind down Anvil process after the test is complete
        launch.close(log_level=logging.ERROR)


@pytest.fixture
def web3(anvil_eth_fork: str):
    """Set up a local unit testing blockchain."""
    web3 = create_multi_provider_web3(anvil_eth_fork)
    return web3


@pytest.fixture()
def deployer(web3) -> str:
    """Deploy account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[0]


@pytest.fixture
def hsm_wallet(web3: Web3) -> HSMWallet:
    """HSM wallet implementation using env vars."""
    wallet = HSMWallet()
    wallet.sync_nonce(web3)
    return wallet


def test_eth_native_transfer(web3: Web3, deployer: str, hsm_wallet: HSMWallet):
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


def test_dai_approval(web3: Web3, deployer: str, hsm_wallet: HSMWallet):
    """Test DAI approve function with HSM wallet (DAI is simpler than USDC)."""

    # Get DAI contract instead of USDC
    # DAI on Ethereum mainnet
    dai_details = fetch_erc20_details(web3, "0x6B175474E89094C44Da98b954EedeAC495271d0F")
    dai = dai_details.contract

    # Fund HSM wallet with ETH for gas
    fund_tx = {"from": deployer, "to": hsm_wallet.address, "value": web3.to_wei(1, "ether")}
    fund_tx_hash = web3.eth.send_transaction(fund_tx)
    fund_receipt = web3.eth.wait_for_transaction_receipt(fund_tx_hash)
    assert fund_receipt["status"] == 1
    logger.debug(f"HSM Wallet funded with {web3.from_wei(web3.eth.get_balance(hsm_wallet.address), 'ether')} ETH")

    # Prepare approval
    spender = "0x0000000000000000000000000000000000000000"
    approve_amount = 1000 * 10**18  # 1000 DAI (18 decimals)

    initial_allowance = dai.functions.allowance(hsm_wallet.address, spender).call()
    logger.debug(f"Initial DAI allowance: {initial_allowance}")

    # Create approval transaction
    approve_tx = dai.functions.approve(spender, approve_amount).build_transaction({"from": hsm_wallet.address, "gas": 100000, "gasPrice": web3.eth.gas_price, "chainId": web3.eth.chain_id})

    logger.debug(f"Approval transaction: {approve_tx}")

    # Sign and send approval
    signed_tx = hsm_wallet.sign_transaction_with_new_nonce(approve_tx)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    logger.debug(f"Approval transaction receipt: {receipt}")
    assert receipt["status"] == 1

    # Verify allowance
    new_allowance = dai.functions.allowance(hsm_wallet.address, spender).call()
    logger.debug(f"New DAI allowance: {new_allowance}")
    assert new_allowance == approve_amount


def test_eth_mainnet_hsm_tx_setup(web3: Web3, deployer: str):
    """Test to logger.debug useful debugging information about the test environment."""

    # logger.debug deployer info
    deployer_balance = web3.eth.get_balance(deployer)
    logger.debug(f"\nDeployer address: {deployer}")
    logger.debug(f"Deployer balance: {web3.from_wei(deployer_balance, 'ether')} ETH")

    # Get USDC info
    usdc_details = fetch_erc20_details(web3, "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
    usdc = usdc_details.contract
    deployer_usdc_balance = usdc.functions.balanceOf(deployer).call()
    logger.debug(f"Deployer USDC balance: {deployer_usdc_balance / 10**6} USDC")

    # logger.debug chain info
    logger.debug(f"Chain ID: {web3.eth.chain_id}")
    logger.debug(f"Block number: {web3.eth.block_number}")
    logger.debug(f"Gas price: {web3.from_wei(web3.eth.gas_price, 'gwei')} gwei")


def test_eth_erc20_approval(web3: Web3, weth_holder: HexAddress, hsm_wallet: HSMWallet):
    """Test ERC-20 approve function with WETH."""

    # Get WETH contract (Wrapped ETH on Ethereum mainnet)
    weth_details = fetch_erc20_details(web3, "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
    weth = weth_details.contract
    logger.debug("\nWETH contract details:")
    logger.debug(f"Name: {weth_details.name}")
    logger.debug(f"Symbol: {weth_details.symbol}")
    logger.debug(f"Decimals: {weth_details.decimals}")

    # Fund wallet with ETH for gas
    fund_tx = {"from": weth_holder, "to": hsm_wallet.address, "value": web3.to_wei(1, "ether")}
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


# Hsm client doesn't support EIP-1559 yet
# def test_eth_eip1559_gas(web3: Web3, large_usdc_holder: HexAddress, hsm_wallet: HSMWallet):
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
