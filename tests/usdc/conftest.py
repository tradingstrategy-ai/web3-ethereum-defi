"""USDC fixtures."""

import pytest
from eth_typing import ChecksumAddress
from web3 import EthereumTesterProvider, Web3, HTTPProvider

from eth_defi.anvil import AnvilLaunch, launch_anvil
from eth_defi.chain import install_chain_middleware
from eth_defi.token import TokenDetails
from eth_defi.usdc.deployment import deploy_fiat_token


# @pytest.fixture
# def tester_provider():
#     # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
#     return EthereumTesterProvider()
#
#
# @pytest.fixture
# def eth_tester(tester_provider):
#     # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
#     return tester_provider.ethereum_tester
#
#
# @pytest.fixture
# def web3(tester_provider):
#     """Set up a local unit testing blockchain."""
#     # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
#     return Web3(tester_provider)


@pytest.fixture()
def anvil() -> AnvilLaunch:
    """Launch Anvil for the test backend.

    Run tests as `pytest --log-cli-level=info` to see Anvil console output created during the test,
    to debug any issues with Anvil itself.

    By default, Anvil is in `automining mode <https://book.getfoundry.sh/reference/anvil/>`__
    and creates a new block for each new transaction.

    .. note ::

        It could be possible to have a persitent Anvil instance over different tests with
        `fixture(scope="module")`. However we have spotted some hangs in Anvil
        (HTTP read timeout) and this is currently cured by letting Anvil reset itself.
    """

    # London hardfork will enable EIP-1559 style gas fees
    anvil = launch_anvil(steps_tracing=True)
    try:
        # Make the initial snapshot ("zero state") to which we revert between tests
        # web3 = Web3(HTTPProvider(anvil.json_rpc_url))
        # snapshot_id = make_anvil_custom_rpc_request(web3, "evm_snapshot")
        # assert snapshot_id == "0x0"
        yield anvil
    finally:
        anvil.close()


@pytest.fixture()
def web3(anvil: AnvilLaunch) -> Web3:
    """Set up the Anvil Web3 connection.

    Also perform the Anvil state reset for each test.
    """
    web3 = Web3(HTTPProvider(anvil.json_rpc_url, request_kwargs={"timeout": 2}))

    # Get rid of attributeddict slow down
    web3.middleware_onion.clear()

    install_chain_middleware(web3)

    return web3


@pytest.fixture()
def deployer(web3) -> ChecksumAddress:
    """Deploy account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[0]


@pytest.fixture()
def usdc(web3, deployer: ChecksumAddress) -> TokenDetails:
    """Centre fiat token.

    Deploy real USDC code.
    """
    return deploy_fiat_token(web3, deployer)
