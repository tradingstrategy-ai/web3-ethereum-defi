"""Ganache mainnet fork test examples.

To run tests in this module:

.. code-block:: shell

    export JSON_RPC_BINANCE="https://bsc-dataseed.binance.org/"
    pytest -k test_ganache

"""

import logging
import os
import shutil

import flaky
import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr
from web3 import HTTPProvider, Web3
# from web3.middleware import buffered_gas_estimate_middleware
# Should be migrated to
# from web3.middleware import BufferedGasEstimateMiddleware

from eth_defi.chain import install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.provider.anvil import fork_network_anvil, is_anvil
from eth_defi.revert_reason import TransactionReverted
from eth_defi.token import fetch_erc20_details

# https://docs.pytest.org/en/latest/how-to/skipping.html#skip-all-test-functions-of-a-class-or-module
pytestmark = pytest.mark.skipif(
    (os.environ.get("JSON_RPC_BINANCE") is None) or (shutil.which("anvil") is None),
    reason="Set JSON_RPC_BINANCE env install anvil command to run these tests",
)


@pytest.fixture()
def large_busd_holder() -> HexAddress:
    """A random account picked from BNB Smart chain that holds a lot of BUSD.

    This account is unlocked on Ganache, so you have access to good BUSD stash.

    `To find large holder accounts, use bscscan <https://bscscan.com/token/0xe9e7cea3dedca5984780bafc599bd69add087d56#balances>`_.
    """
    # Binance Hot Wallet 6
    return HexAddress(HexStr("0x8894E0a0c962CB723c1976a4421c95949bE2D4E3"))


@pytest.fixture()
def user_1() -> LocalAccount:
    """Create a test account."""
    return Account.create()


@pytest.fixture()
def user_2() -> LocalAccount:
    """User account.

    Do some account allocation for tests.
    """
    return Account.create()


@pytest.fixture()
def anvil_bnb_chain_fork(request, large_busd_holder, user_1, user_2) -> str:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    mainnet_rpc = os.environ["JSON_RPC_BINANCE"]
    launch = fork_network_anvil(mainnet_rpc, unlocked_addresses=[large_busd_holder])
    try:
        yield launch.json_rpc_url
    finally:
        # Wind down Anvil process after the test is complete
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3(anvil_bnb_chain_fork: str):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    web3 = Web3(HTTPProvider(anvil_bnb_chain_fork))
    # Anvil needs POA middlware if parent chain needs POA middleware
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    return web3


def test_anvil_output():
    """Read anvil output from stdout."""
    # mainnet_rpc = os.environ["JSON_RPC_BINANCE"]
    # process, cmd = _launch("anvil")

    mainnet_rpc = os.environ["JSON_RPC_BINANCE"]
    launch = fork_network_anvil(mainnet_rpc)
    try:
        stdout, stderr = launch.close()
        assert b"https://github.com/foundry-rs/foundry" in stdout, f"Did not see the market string in stdout: {stdout}"
    finally:
        launch.close()


def test_anvil_forked_chain_id(web3: Web3):
    """Anvil pipes through the forked chain id."""
    assert web3.eth.chain_id == 56
    assert is_anvil(web3)


# Flaky because uses live node
@flaky.flaky()
def test_anvil_fork_busd_details(web3: Web3, large_busd_holder: HexAddress, user_1):
    """Checks BUSD deployment on BNB chain."""
    busd = fetch_erc20_details(web3, "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56")
    assert busd.symbol == "BUSD"
    assert (busd.total_supply / (10**18)) > 10_000_000, "More than $10m BUSD minted"


# Flaky because uses live node
@flaky.flaky()
def test_anvil_fork_transfer_busd(web3: Web3, large_busd_holder: HexAddress, user_1):
    """Forks the BNB chain mainnet and transfers from USDC to the user."""

    # BUSD deployment on BNB chain
    # https://bscscan.com/token/0xe9e7cea3dedca5984780bafc599bd69add087d56
    busd_details = fetch_erc20_details(web3, "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56")
    busd = busd_details.contract

    # Transfer 500 BUSD to the user 1
    tx_hash = busd.functions.transfer(user_1.address, 500 * 10**18).transact({"from": large_busd_holder})

    # Because Ganache has instamine turned on by default, we do not need to wait for the transaction
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt.status == 1, "BUSD transfer reverted"

    assert busd.functions.balanceOf(user_1.address).call() == 500 * 10**18


def test_anvil_latest_block(web3: Web3, large_busd_holder: HexAddress, user_1):
    """Fetch latest block using Anvil."""
    # Fails randomly see https://github.com/foundry-rs/foundry/issues/4666
    latest_block = web3.eth.get_block("latest")


@pytest.mark.skip(reason="Too flaky - depends on public Polygon RPC availability and response format")
@pytest.mark.skipif(shutil.which("anvil") is None, reason="Install anvil to run this test")
@flaky.flaky(max_runs=3)
def test_archive_node_required_exception():
    """Test that ArchiveNodeRequired is raised when forking with non-archive RPC.

    Uses the public Polygon RPC (https://polygon-rpc.com/) which is NOT an archive
    node. When we try to fork at a historical block, it should fail with
    ArchiveNodeRequired exception containing the HTTP response headers.

    .. note::

        This test uses a public RPC that may rate limit requests.
        The @flaky decorator handles intermittent failures.
    """
    from eth_defi.provider.anvil import fork_network_anvil, ArchiveNodeRequired

    # Public Polygon RPC - known to NOT be an archive node
    public_polygon_rpc = "https://polygon-rpc.com/"

    # Try to fork at a very old block that the non-archive node won't have
    # Block 1,000,000 is from early 2021
    old_block = 1_000_000

    with pytest.raises(ArchiveNodeRequired) as exc_info:
        fork_network_anvil(
            public_polygon_rpc,
            fork_block_number=old_block,
            archive=True,
        )

    # Verify the exception contains useful debugging information
    exc = exc_info.value
    assert exc.rpc_url == public_polygon_rpc
    assert exc.requested_block == old_block
    assert exc.available_block is not None  # Should have current block
    assert exc.response_headers is not None  # Should have HTTP headers
    assert len(exc.response_headers) > 0, "Exception should include HTTP response headers for debugging"

    # Verify the error message is informative
    error_message = str(exc)
    assert "polygon-rpc.com" in error_message
    assert str(old_block) in error_message or "1,000,000" in error_message


# def test_revert_reason_middleware(web3: Web3, large_busd_holder: HexAddress, user_1: LocalAccount, user_2: LocalAccount):

#     """Revert reason will be shown in Python tracebacks.
#
#     We test this by sending BUSD with insufficient token balance.
#     """
#
#     # web3.middleware_onion.inject(revert_reason_aware_buffered_gas_estimate_middleware, layer=0)
#     web3.middleware_onion.replace("gas_estimate", revert_reason_aware_buffered_gas_estimate_middleware)
#
#     # BUSD deployment on BNB chain
#     # https://bscscan.com/token/0xe9e7cea3dedca5984780bafc599bd69add087d56
#     busd_details = fetch_erc20_details(web3, "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56")
#     busd = busd_details.contract
#
#     # Make sure user_1 has some BNB
#     web3.eth.send_transaction({"from": large_busd_holder, "to": user_1.address, "value": 10**18})
#
#     # user_1 doess not have enough BUSD so this tx will fail
#     # and BUSD ERC-20 contract should give the revert reason
#     with pytest.raises(TransactionReverted) as exc_info:
#         tx_hash = busd.functions.transfer(user_2.address, 500 * 10**18).transact({"from": user_1.address})
#
#     # assert reason == "execution reverted: BEP20: transfer amount exceeds balance"
