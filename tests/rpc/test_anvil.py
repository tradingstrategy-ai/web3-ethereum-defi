"""Anvil mainnet fork test examples.

To run tests in this module:

.. code-block:: shell

    source .local-test.env && poetry run pytest tests/rpc/test_anvil.py

"""

import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

import pytest
from eth_typing import HexAddress, HexStr
from web3 import Web3

from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.provider.anvil import is_anvil, launch_anvil
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import BINANCE_MIDNIGHT_BLOCK
from eth_defi.token import fetch_erc20_details

JSON_RPC_BINANCE = os.environ.get("JSON_RPC_BINANCE")

#: BNB Smart Chain's EVM chain identifier.
BINANCE_CHAIN_ID: int = 56

#: BUSD deployment used by the fixed-block fork tests.
BUSD_ADDRESS: HexAddress = HexAddress(HexStr("0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56"))

#: Binance Hot Wallet 6, unlocked as the historical BUSD holder.
#: See `BscScan holder balances <https://bscscan.com/token/0xe9e7cea3dedca5984780bafc599bd69add087d56#balances>`__.
BUSD_HOLDER: HexAddress = HexAddress(HexStr("0x8894E0a0c962CB723c1976a4421c95949bE2D4E3"))

#: Deterministic recipient whose BUSD balance slot is stored in the RPC cache seed.
BUSD_RECIPIENT: HexAddress = HexAddress(HexStr("0x1111111111111111111111111111111111111111"))

#: BUSD total supply at :data:`BINANCE_MIDNIGHT_BLOCK`.
BUSD_TOTAL_SUPPLY: int = 283_188_732_471_960_898_956_126_663

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


@pytest.fixture()
def web3(anvil_fork_pool: AnvilForkPool) -> Iterator[Web3]:
    """Connect to the isolated shared canonical BNB fork."""
    assert JSON_RPC_BINANCE is not None
    web3, launch = anvil_fork_pool.get_web3_with_launch(
        JSON_RPC_BINANCE,
        BINANCE_MIDNIGHT_BLOCK,
        unlocked_addresses=[BUSD_HOLDER],
    )
    # ``AnvilForkPool.get_web3()`` creates its multi-provider client with the
    # BNB proof-of-authority middleware already installed.  Injecting it here
    # again raises Web3's duplicate-middleware error on every BNB fork test.
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    # The pool can recycle a wedged fork at any lookup.  Taking the snapshot
    # from the exact launch returned with this Web3 prevents an old module-level
    # launch from being reverted after it has been replaced on another port.
    with contextmanager(evm_snapshot_revert)(launch):
        yield web3


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
    assert web3.eth.chain_id == BINANCE_CHAIN_ID
    assert is_anvil(web3)


@requires_bnb_rpc
def test_anvil_fork_busd_details(web3: Web3) -> None:
    """Checks BUSD deployment on BNB chain."""
    # Token metadata cache keys contain only chain id and address, whereas this
    # assertion deliberately verifies BUSD supply at one historical block.
    # Disable the process-wide cache so a moving-tip BNB fork in another test
    # cannot provide its current supply and make this fixed-block check flaky.
    busd = fetch_erc20_details(web3, BUSD_ADDRESS, cache=None)
    assert busd.symbol == "BUSD"
    assert busd.total_supply == BUSD_TOTAL_SUPPLY


@requires_bnb_rpc
def test_anvil_fork_transfer_busd(web3: Web3) -> None:
    """Transfer a fixed BUSD amount from a historical holder to a test user."""

    # BUSD deployment on BNB chain
    # https://bscscan.com/token/0xe9e7cea3dedca5984780bafc599bd69add087d56
    busd_details = fetch_erc20_details(web3, BUSD_ADDRESS)
    amount = Decimal("500")

    # Transfer 500 BUSD to the test user.
    tx_hash = busd_details.transfer(BUSD_RECIPIENT, amount).transact({"from": BUSD_HOLDER})

    # Anvil mines instantly by default, but wait explicitly for a receipt so
    # this continues to validate the transfer if that local default changes.
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1, "BUSD transfer reverted"

    assert busd_details.fetch_raw_balance_of(BUSD_RECIPIENT) == busd_details.convert_to_raw(amount)


@requires_bnb_rpc
def test_anvil_latest_block(web3: Web3) -> None:
    """Fetch latest block using Anvil."""
    assert web3.eth.get_block("latest")["number"] == BINANCE_MIDNIGHT_BLOCK
