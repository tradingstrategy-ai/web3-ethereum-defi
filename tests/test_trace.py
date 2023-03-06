import pytest
from eth_typing import HexAddress
from web3 import HTTPProvider, Web3

from eth_defi.anvil import AnvilLaunch, make_anvil_custom_rpc_request
from eth_defi.deploy import deploy_contract


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


def test_trace_call(web3):
    """Test EVM trace."""
    reverter = deploy_contract(web3, "RevertTest.json")

    with pytest.raises(RuntimeError):
        reverter.functions.revert1().call()

