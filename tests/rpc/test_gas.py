"""Gas helpers."""

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount

from eth_typing import HexAddress
from web3 import Web3, EthereumTesterProvider
from web3._utils.transactions import fill_nonce

from eth_defi.gas import estimate_gas_fees, GasPriceMethod, apply_gas, GasPriceSuggestion, node_default_gas_price_strategy, GAS_PRICE_BUFFER_MULTIPLIER
from eth_defi.hotwallet import HotWallet
from eth_defi.token import create_token
from eth_defi.tx import get_tx_broadcast_data


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
def hot_wallet_account(web3) -> LocalAccount:
    """User account.

    Do some account allocation for tests.
    """
    return Account.create()


def test_gas_fees_london(web3: Web3, deployer: str):
    """Estimate gas fees on London hard-fork compatible blockchain.

    Note: We cannot test for non-London EVMs, as EthereumTester does not support them.

    https://github.com/ethereum/eth-tester/issues/233
    """
    fees = estimate_gas_fees(web3)
    assert fees.method == GasPriceMethod.london
    assert fees.base_fee == 1000000000
    # max_fee_per_gas = (priority_fee + 2 * base_fee) * buffer = (1G + 2G) * 1.12 = 3.36G
    assert fees.max_fee_per_gas == int(3000000000 * GAS_PRICE_BUFFER_MULTIPLIER)
    assert fees.max_priority_fee_per_gas == 1000000000


def test_gas_fees_london_no_buffer(web3: Web3, deployer: str):
    """Gas estimation with buffer disabled gives the raw value."""
    fees = estimate_gas_fees(web3, gas_price_buffer_multiplier=1.0)
    assert fees.method == GasPriceMethod.london
    assert fees.base_fee == 1000000000
    assert fees.max_fee_per_gas == 3000000000
    assert fees.max_priority_fee_per_gas == 1000000000


def test_raw_transaction_with_gas(web3: Web3, eth_tester, deployer: HexAddress, hot_wallet_account: LocalAccount):
    """Create a raw transaction with gas information."""

    gas_fees = estimate_gas_fees(web3)

    """Transfer tokens between users."""
    token = create_token(web3, deployer, "Cow token", "COWS", 100_000 * 10**18)

    # Drop some ETH and token to the hot wallet
    web3.eth.send_transaction({"from": deployer, "to": hot_wallet_account.address, "value": 1 * 10**18})
    token.functions.transfer(hot_wallet_account.address, 50_000 * 10**18).transact({"from": deployer})

    # Create a raw transaction
    # Move 10 tokens from deployer to user1
    # https://web3py.readthedocs.io/en/stable/contracts.html?highlight=buildTransaction#web3.contract.ContractFunction.build_transaction
    tx = token.functions.transfer(hot_wallet_account.address, 10 * 10**18).build_transaction(
        {
            "from": hot_wallet_account.address,
            "chainId": web3.eth.chain_id,
            "gas": 150_000,  # 150k gas should be more than enough for ERC20.transfer()
        }
    )

    tx = fill_nonce(web3, tx)
    apply_gas(tx, gas_fees)

    signed = hot_wallet_account.sign_transaction(tx)
    raw_bytes = get_tx_broadcast_data(signed)
    tx_hash = web3.eth.send_raw_transaction(raw_bytes)
    receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert receipt.status == 1  # 1=success and mined


def test_build_transaction_legacy(web3: Web3, deployer: str, hot_wallet_account):
    """We can apply gas fees to the transactions signed with HotWallet."""

    # Unless we override the default gas price strategy,
    # web3.py is going to default to Ethereum mainnet London style transactions
    # that do not understand about "gasPrice" parameter
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)

    # 99 GWei
    gas_fees = GasPriceSuggestion(method=GasPriceMethod.legacy, legacy_gas_price=99 * 10**9)

    token = create_token(web3, deployer, "Cow token", "COWS", 100_000 * 10**18)

    # Drop some ETH and token to the hot wallet
    web3.eth.send_transaction({"from": deployer, "to": hot_wallet_account.address, "value": 1 * 10**18})

    # Build a transaction using gas hints and signed locally
    # before broadcasting
    hot_wallet = HotWallet(hot_wallet_account)
    hot_wallet.sync_nonce(web3)

    tx = token.functions.approve(deployer, 100).build_transaction({"from": hot_wallet.address})

    assert "gas" in tx
    apply_gas(tx, gas_fees)

    signed_tx = hot_wallet.sign_transaction_with_new_nonce(tx)
    signed_bytes = get_tx_broadcast_data(signed_tx)
    assert len(signed_bytes) > 0

    tx_hash = web3.eth.send_raw_transaction(signed_bytes)
    receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert receipt.status == 1
