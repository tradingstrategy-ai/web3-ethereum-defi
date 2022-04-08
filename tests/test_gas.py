"""Gas helpers."""
import secrets

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3, EthereumTesterProvider
from web3._utils.transactions import fill_nonce

from eth_defi.gas import estimate_gas_fees, GasPriceMethod, apply_gas
from eth_defi.token import create_token


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
def hot_wallet_private_key(web3) -> HexBytes:
    """Generate a private key"""
    return HexBytes(secrets.token_bytes(32))


@pytest.fixture()
def hot_wallet(web3, hot_wallet_private_key) -> LocalAccount:
    """User account.

    Do some account allocation for tests.
    """
    return Account.from_key(hot_wallet_private_key)


def test_gas_fees_london(web3: Web3, deployer: str):
    """Estimate gas fees on London hard-fork compatible blockchain.

    Note: We cannot test for non-London EVMs, as EthereumTester does not support them.

    https://github.com/ethereum/eth-tester/issues/233
    """
    fees = estimate_gas_fees(web3)
    assert fees.method == GasPriceMethod.london
    assert fees.base_fee == 1000000000
    assert fees.max_fee_per_gas == 3000000000
    assert fees.max_priority_fee_per_gas == 1000000000


def test_raw_transaction_with_gas(web3: Web3, eth_tester, deployer: HexAddress, hot_wallet: LocalAccount):
    """Create a raw transaction with gas information."""

    gas_fees = estimate_gas_fees(web3)

    """Transfer tokens between users."""
    token = create_token(web3, deployer, "Cow token", "COWS", 100_000 * 10**18)

    # Drop some ETH and token to the hot wallet
    web3.eth.send_transaction({"from": deployer, "to": hot_wallet.address, "value": 1 * 10**18})
    token.functions.transfer(hot_wallet.address, 50_000 * 10**18).transact({"from": deployer})

    # Create a raw transaction
    # Move 10 tokens from deployer to user1
    # https://web3py.readthedocs.io/en/stable/contracts.html?highlight=buildTransaction#web3.contract.ContractFunction.buildTransaction
    tx = token.functions.transfer(hot_wallet.address, 10 * 10**18).buildTransaction(
        {
            "from": hot_wallet.address,
            "chainId": web3.eth.chain_id,
            "gas": 150_000,  # 150k gas should be more than enough for ERC20.transfer()
        }
    )

    tx = fill_nonce(web3, tx)
    apply_gas(tx, gas_fees)

    signed = hot_wallet.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert receipt.status == 1  # 1=success and mined
