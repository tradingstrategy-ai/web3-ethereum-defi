"""Anvil mainnet fork test examples.

To run tests in this module:

.. code-block:: shell

    export JSON_RPC_BINANCE="https://bsc-dataseed.binance.org/"
    pytest -k test_ganache

"""

import os
import shutil
from collections.abc import Iterator

import flaky
import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr
from web3 import Web3

from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.provider.anvil import AnvilLaunch, ArchiveNodeRequired, fork_network_anvil, is_anvil, launch_anvil
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import BINANCE_MIDNIGHT_BLOCK
from eth_defi.token import fetch_erc20_details

JSON_RPC_BINANCE = os.environ.get("JSON_RPC_BINANCE")

# https://docs.pytest.org/en/latest/how-to/skipping.html#skip-all-test-functions-of-a-class-or-module
pytestmark = [
    pytest.mark.skipif(shutil.which("anvil") is None, reason="Install anvil to run these tests"),
    # Keep all users of the canonical BNB fork on one worker so they share its
    # persisted Anvil RPC cache instead of repeatedly replaying archive state.
    pytest.mark.xdist_group("fork:binance:midnight"),
]

requires_bnb_rpc = pytest.mark.skipif(
    JSON_RPC_BINANCE is None,
    reason="Set JSON_RPC_BINANCE to run BNB fork tests",
)


@pytest.fixture(scope="module")
def large_busd_holder() -> HexAddress:
    """A random account picked from BNB Smart chain that holds a lot of BUSD.

    This account is unlocked on Anvil, so the fork can transfer its BUSD.

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


@pytest.fixture(scope="module")
def anvil_bnb_chain_fork(anvil_fork_pool: AnvilForkPool, large_busd_holder: HexAddress) -> AnvilLaunch:
    """Share the canonical cached BNB fork required by the BUSD tests.

    Pinning the fork to :data:`BINANCE_MIDNIGHT_BLOCK` makes its archive state
    reproducible and lets Anvil reuse the committed RPC cache across tests and
    CI runs.  The selected holder is part of the launch configuration because
    Anvil must unlock it before a test can submit a BUSD transfer from it.

    :return:
        Shared Anvil process for the fixed BNB block.
    """
    assert JSON_RPC_BINANCE is not None
    return anvil_fork_pool.get_launch(
        JSON_RPC_BINANCE,
        BINANCE_MIDNIGHT_BLOCK,
        unlocked_addresses=[large_busd_holder],
    )


@pytest.fixture()
def _evm_snapshot(anvil_bnb_chain_fork: AnvilLaunch) -> Iterator[None]:
    """Restore the shared BNB fork after each test that uses it."""
    yield from evm_snapshot_revert(anvil_bnb_chain_fork)


@pytest.fixture()
def web3(anvil_fork_pool: AnvilForkPool, _evm_snapshot: None, large_busd_holder: HexAddress) -> Web3:
    """Connect to the isolated shared canonical BNB fork."""
    assert JSON_RPC_BINANCE is not None
    web3 = anvil_fork_pool.get_web3(
        JSON_RPC_BINANCE,
        BINANCE_MIDNIGHT_BLOCK,
        unlocked_addresses=[large_busd_holder],
    )
    # ``AnvilForkPool.get_web3()`` creates its multi-provider client with the
    # BNB proof-of-authority middleware already installed.  Injecting it here
    # again raises Web3's duplicate-middleware error on every BNB fork test.
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    return web3


def test_anvil_output() -> None:
    """Read anvil output from stdout."""
    # This only verifies local Anvil process output.  Do not create an unrelated
    # BNB fork here: that used a moving chain tip and made a local smoke test
    # fail when the remote BNB provider rejected or timed out during genesis.
    launch = launch_anvil()
    try:
        stdout, _stderr = launch.close()
        assert b"https://github.com/foundry-rs/foundry" in stdout, f"Did not see the market string in stdout: {stdout}"
    finally:
        launch.close()


@requires_bnb_rpc
def test_anvil_forked_chain_id(web3: Web3) -> None:
    """Anvil pipes through the forked chain id."""
    assert web3.eth.chain_id == 56
    assert is_anvil(web3)


@requires_bnb_rpc
def test_anvil_fork_busd_details(web3: Web3) -> None:
    """Checks BUSD deployment on BNB chain."""
    busd = fetch_erc20_details(web3, "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56")
    assert busd.symbol == "BUSD"
    assert (busd.total_supply / (10**18)) > 10_000_000, "More than $10m BUSD minted"


@requires_bnb_rpc
def test_anvil_fork_transfer_busd(web3: Web3, large_busd_holder: HexAddress, user_1: LocalAccount) -> None:
    """Forks the BNB chain mainnet and transfers from USDC to the user."""

    # BUSD deployment on BNB chain
    # https://bscscan.com/token/0xe9e7cea3dedca5984780bafc599bd69add087d56
    busd_details = fetch_erc20_details(web3, "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56")
    busd = busd_details.contract

    # Transfer 500 BUSD to the user 1
    tx_hash = busd.functions.transfer(user_1.address, 500 * 10**18).transact({"from": large_busd_holder})

    # Anvil mines instantly by default, but wait explicitly for a receipt so
    # this continues to validate the transfer if that local default changes.
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1, "BUSD transfer reverted"

    assert busd.functions.balanceOf(user_1.address).call() == 500 * 10**18


@requires_bnb_rpc
def test_anvil_latest_block(web3: Web3) -> None:
    """Fetch latest block using Anvil."""
    # Fails randomly see https://github.com/foundry-rs/foundry/issues/4666
    web3.eth.get_block("latest")


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
