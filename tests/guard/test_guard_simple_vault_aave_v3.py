"""Check guard against Aave v3 calls.

- Validates that supply onBehalfOf is checked against allowed receivers
- Uses SimpleVaultV0 on Base Anvil fork
"""

import os

import pytest
from eth_abi import encode
from eth_typing import HexAddress, HexStr
from web3 import Web3
from web3.contract import Contract

from eth_defi.aave_v3.constants import AAVE_V3_DEPLOYMENTS
from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details, TokenDetails
from eth_defi.trace import (
    TransactionAssertionError,
    assert_transaction_success_with_explanation,
)

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")
CI = os.environ.get("CI") == "true"

pytestmark = pytest.mark.skipif(
    JSON_RPC_BASE is None,
    reason="Set JSON_RPC_BASE env",
)

#: Aave V3 supply function selector: supply(address,uint256,address,uint16)
SEL_SUPPLY = bytes.fromhex("617ba037")


@pytest.fixture
def large_usdc_holder() -> HexAddress:
    return HexAddress(HexStr("0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A"))


@pytest.fixture
def anvil_base_chain_fork(request, large_usdc_holder) -> AnvilLaunch:
    """Create a testable fork of live Base chain."""
    mainnet_rpc = os.environ["JSON_RPC_BASE"]
    launch = fork_network_anvil(
        mainnet_rpc,
        unlocked_addresses=[large_usdc_holder],
        fork_block_number=30_659_990,
    )
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture
def web3(anvil_base_chain_fork: AnvilLaunch):
    web3 = create_multi_provider_web3(
        anvil_base_chain_fork.json_rpc_url,
        default_http_timeout=(3, 250.0),
    )
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture
def usdc(web3) -> TokenDetails:
    return fetch_erc20_details(web3, "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")


@pytest.fixture()
def deployer(web3) -> str:
    return web3.eth.accounts[0]


@pytest.fixture()
def owner(web3) -> str:
    return web3.eth.accounts[1]


@pytest.fixture()
def asset_manager(web3) -> str:
    return web3.eth.accounts[2]


@pytest.fixture()
def attacker(web3) -> str:
    return web3.eth.accounts[3]


@pytest.fixture()
def vault(
    web3: Web3,
    usdc: TokenDetails,
    deployer: str,
    owner: str,
    asset_manager: str,
) -> Contract:
    """SimpleVaultV0 with Aave V3 pool whitelisted."""
    vault = deploy_contract(
        web3,
        "guard/SimpleVaultV0.json",
        deployer,
        asset_manager,
        libraries=GUARD_LIBRARIES,
    )

    tx_hash = vault.functions.initialiseOwnership(owner).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)

    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())

    # Whitelist Aave V3 pool
    base_pool = AAVE_V3_DEPLOYMENTS["base"]["pool"]
    tx_hash = guard.functions.whitelistAaveV3(
        base_pool,
        "Allow Aave v3",
    ).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Whitelist USDC token
    guard.functions.whitelistToken(usdc.address, "Allow USDC").transact({"from": owner})

    return vault


@pytest.fixture()
def guard(web3: Web3, vault: Contract) -> Contract:
    return get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())


@pytest.mark.skipif(CI, reason="Flaky on CI due to Anvil fork block range errors")
def test_guard_aave_supply_wrong_on_behalf_of(
    web3: Web3,
    asset_manager: str,
    vault: Contract,
    usdc: TokenDetails,
    attacker: str,
):
    """Aave V3 supply with a non-whitelisted onBehalfOf address should revert.

    The guard decodes the supply() calldata and validates onBehalfOf
    against isAllowedReceiver(). An attacker address as receiver is rejected
    with "Receiver not whitelisted".
    """
    pool_address = AAVE_V3_DEPLOYMENTS["base"]["pool"]

    # Encode supply(usdc, 1000e6, attacker, 0) — attacker as onBehalfOf
    call_data = SEL_SUPPLY + encode(
        ["address", "uint256", "address", "uint16"],
        [usdc.address, 1_000 * 10**6, attacker, 0],
    )

    tx_hash = vault.functions.performCall(pool_address, call_data).transact({"from": asset_manager})
    with pytest.raises(TransactionAssertionError, match="Receiver not whitelisted"):
        assert_transaction_success_with_explanation(web3, tx_hash)
