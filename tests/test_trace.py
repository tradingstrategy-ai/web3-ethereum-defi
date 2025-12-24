"""Solidity stack trace tests."""

import pytest
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import HTTPProvider, Web3

from eth_defi.provider.anvil import AnvilLaunch, make_anvil_custom_rpc_request, launch_anvil
from eth_defi.deploy import deploy_contract, get_or_create_contract_registry
from eth_defi.trace import trace_evm_transaction, print_symbolic_trace, assert_transaction_success_with_explanation, assert_call_success_with_explanation, TransactionAssertionError


@pytest.fixture(scope="session")
def anvil() -> AnvilLaunch:
    """Launch Anvil for the test backend.

    Launch Anvil only once per pytest run, call reset between.

    Limitations

    - `Does not support stack traces <https://github.com/foundry-rs/foundry/issues/3558>`__

    - Run tests as `pytest --log-cli-level=debug` to see Anvil console output created during the test

    """

    # London hardfork will enable EIP-1559 style gas fees
    anvil = launch_anvil(
        hardfork="london",
        gas_limit=15_000_000,  # Max 5M gas per block, or per transaction in test automining
        # Enable structured logs if debug_traceTransaction() is called
        steps_tracing=True,
    )
    try:
        # Make the initial snapshot ("zero state") to which we revert between tests
        web3 = Web3(HTTPProvider(anvil.json_rpc_url))
        snapshot_id = make_anvil_custom_rpc_request(web3, "evm_snapshot")
        assert snapshot_id == "0x0"
        yield anvil
    finally:
        anvil.close()


@pytest.fixture
def web3(anvil: AnvilLaunch) -> Web3:
    """Set up the Anvil Web3 connection.
    Also perform the Anvil state reset for each test.
    """
    web3 = Web3(HTTPProvider(anvil.json_rpc_url))
    snapshot_id = "0x0"
    make_anvil_custom_rpc_request(web3, "evm_revert", [snapshot_id])
    return web3


@pytest.fixture()
def deployer(web3) -> HexAddress:
    """Deployer account.

    - This account will deploy all smart contracts

    - Starts with 10,000 ETH
    """
    return web3.eth.accounts[0]


def test_trace_transaction_simple(web3, deployer):
    """Test EVM trace."""
    reverter = deploy_contract(web3, "RevertTest.json", deployer)

    tx_hash = reverter.functions.revert1().transact({"from": deployer, "gas": 500_000})
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 0  # Failed

    # Get the debug trace from the node and transform it to a list of call items
    trace_data = trace_evm_transaction(web3, tx_hash)

    # Single root element, no nesting
    assert len(trace_data.calls) == 0
    assert trace_data.calldata == HexBytes("0xb550276d")  # revert1()

    # Transform the list of call items to a human-readable output,
    # use ABI data from deployed contracts to enrich the output
    trace_output = print_symbolic_trace(get_or_create_contract_registry(web3), trace_data)

    assert "revert1()" in trace_output


def test_trace_transaction_nested(web3, deployer):
    """Test EVM trace with nested contracts."""

    reverter = deploy_contract(web3, "RevertTest.json", deployer)
    reverter2 = deploy_contract(web3, "RevertTest2.json", deployer)

    tx_hash = reverter.functions.revert2(reverter2.address).transact({"from": deployer, "gas": 500_000})
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 0  # Failed

    # Get the debug trace from the node and transform it to a list of call items
    trace_data = trace_evm_transaction(web3, tx_hash)

    # Transform the list of call items to a human-readable output,
    # use ABI data from deployed contracts to enrich the output
    trace_output = print_symbolic_trace(get_or_create_contract_registry(web3), trace_data)

    assert "RevertTest2" in trace_output, f"Got output: {trace_output}"


def test_assert_tx_with_trace(web3, deployer):
    """Test transaction success assert."""

    reverter = deploy_contract(web3, "RevertTest.json", deployer)
    reverter2 = deploy_contract(web3, "RevertTest2.json", deployer)

    tx_hash = reverter.functions.revert2(reverter2.address).transact({"from": deployer, "gas": 500_000})
    with pytest.raises(TransactionAssertionError):
        assert_transaction_success_with_explanation(web3, tx_hash)


def test_assert_call_with_trace(web3, deployer):
    """Test transaction success assert."""

    reverter = deploy_contract(web3, "RevertTest.json", deployer)
    reverter2 = deploy_contract(web3, "RevertTest2.json", deployer)

    call = reverter.functions.revert2(reverter2.address)
    with pytest.raises(TransactionAssertionError):
        assert_call_success_with_explanation(call, {"from": deployer})
