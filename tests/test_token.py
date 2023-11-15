"""Mock token deployment."""

import pytest

from eth_tester.exceptions import TransactionFailed
from web3 import Web3, EthereumTesterProvider

from eth_defi.deploy import deploy_contract, get_registered_contract
from eth_defi.token import create_token, fetch_erc20_details, TokenDetailError, TokenDetails, DEFAULT_TOKEN_CACHE, reset_default_token_cache


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

    # This test does not work with token cache
    reset_default_token_cache()

    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return Web3(tester_provider)


@pytest.fixture()
def deployer(web3) -> str:
    """Deploy account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[0]


@pytest.fixture()
def user_1(web3) -> str:
    """User account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[1]


@pytest.fixture()
def user_2(web3) -> str:
    """User account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[2]


def test_deploy_token(web3: Web3, deployer: str):
    """Deploy mock ERC-20."""
    token = create_token(web3, deployer, "Hentai books token", "HENTAI", 100_000 * 10**18, 6)
    # https://web3py.readthedocs.io/en/stable/contracts.html#contract-deployment-example
    assert token.functions.name().call() == "Hentai books token"
    assert token.functions.symbol().call() == "HENTAI"
    assert token.functions.totalSupply().call() == 100_000 * 10**18
    assert token.functions.decimals().call() == 6

    registered_contract = get_registered_contract(web3, token.address)
    assert registered_contract.name == "ERC20MockDecimals"


def test_tranfer_tokens_between_users(web3: Web3, deployer: str, user_1, user_2):
    """Transfer tokens between users."""
    token = create_token(web3, deployer, "Telos EVM rocks", "TELOS", 100_000 * 10**18)

    # Move 10 tokens from deployer to user1
    token.functions.transfer(user_1, 10 * 10**18).transact({"from": deployer})
    assert token.functions.balanceOf(user_1).call() == 10 * 10**18

    # Move 10 tokens from deployer to user1
    token.functions.transfer(user_2, 6 * 10**18).transact({"from": user_1})
    assert token.functions.balanceOf(user_1).call() == 4 * 10**18
    assert token.functions.balanceOf(user_2).call() == 6 * 10**18


def test_tranfer_too_much(web3: Web3, deployer: str, user_1, user_2):
    """Attempt to transfer more tokens than an account has."""
    token = create_token(web3, deployer, "Telos EVM rocks", "TELOS", 100_000 * 10**18)

    # Move 10 tokens from deployer to user1
    token.functions.transfer(user_1, 10 * 10**18).transact({"from": deployer})
    assert token.functions.balanceOf(user_1).call() == 10 * 10**18

    # Attempt to move 11 tokens from deployer to user1
    with pytest.raises(TransactionFailed) as excinfo:
        token.functions.transfer(user_1, 11 * 10**18).transact({"from": user_1})
    assert str(excinfo.value) == "execution reverted: ERC20: transfer amount exceeds balance"


def test_fetch_token_details(web3: Web3, deployer: str):
    """Get details of a token."""
    token = create_token(web3, deployer, "Hentai books token", "HENTAI", 100_000 * 10**18, 6)
    details = fetch_erc20_details(web3, token.address)
    assert details.name == "Hentai books token"
    assert details.decimals == 6


def test_fetch_token_details_broken_silent(web3: Web3, deployer: str):
    """Get details of a token that does not conform ERC-20 guidelines."""
    malformed_token = deploy_contract(web3, "MalformedERC20.json", deployer)
    details = fetch_erc20_details(web3, malformed_token.address, raise_on_error=False)
    assert details.symbol == ""
    assert details.decimals == 0
    assert details.total_supply is None


def test_fetch_token_details_broken_load(web3: Web3, deployer: str):
    """Get an error if trying to read malformed token."""
    malformed_token = deploy_contract(web3, "MalformedERC20.json", deployer)
    with pytest.raises(TokenDetailError):
        fetch_erc20_details(web3, malformed_token.address, cache=None)


def test_compare_token(web3: Web3, deployer: str):
    """token __eq__ works by chain id and address"""
    token_1_contract = create_token(web3, deployer, "Hentai books token", "HENTAI", 100_000 * 10**18, 6)
    token_2_contract = create_token(web3, deployer, "Animu token", "ANIMU", 100_000 * 10**18, 6)
    token_1 = fetch_erc20_details(web3, token_1_contract.address)
    token_2 = fetch_erc20_details(web3, token_2_contract.address)
    token_1_again = fetch_erc20_details(web3, token_1.address)
    assert token_1 == token_1_again
    assert token_2 != token_1
    assert hash(token_1) == hash(token_1_again)


def test_cache_erc_20_details(web3: Web3, deployer: str):
    """Token details are cached."""

    token = create_token(web3, deployer, "Hentai books token", "HENTAI", 100_000 * 10**18, 6)
    fetch_erc20_details(web3, token.address)

    cache_key = TokenDetails.generate_cache_key(web3.eth.chain_id, token.address)
    assert cache_key in DEFAULT_TOKEN_CACHE


def test_cache_reset_erc_20_details(web3: Web3, deployer: str):
    """Token cache can be reset."""

    token = create_token(web3, deployer, "Hentai books token", "HENTAI", 100_000 * 10**18, 6)
    fetch_erc20_details(web3, token.address)
    assert len(DEFAULT_TOKEN_CACHE) == 1
    reset_default_token_cache()
    assert len(DEFAULT_TOKEN_CACHE) == 0
    fetch_erc20_details(web3, token.address)
    assert len(DEFAULT_TOKEN_CACHE) == 1
