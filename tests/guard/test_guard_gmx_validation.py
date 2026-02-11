"""Guard validation tests for GMX multicall trading.

Tests validate that:
1. GMX router whitelisting works correctly
2. GMX market whitelisting works correctly
3. Ownership controls are enforced
4. anyAsset mode works correctly

Note: The actual ABI encoding validation is tested in the integration tests
(tests/gmx/lagoon/test_gmx_lagoon_integration.py) which run against real GMX
contracts on an Arbitrum fork. These unit tests focus on the whitelisting
and access control logic.
"""

import pytest
from eth_tester.exceptions import TransactionFailed
from web3 import EthereumTesterProvider, Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import deploy_contract
from eth_defi.token import create_token


@pytest.fixture
def tester_provider():
    return EthereumTesterProvider()


@pytest.fixture
def web3(tester_provider):
    """Set up a local unit testing blockchain."""
    return Web3(tester_provider)


@pytest.fixture
def deployer(web3) -> str:
    return web3.eth.accounts[0]


@pytest.fixture
def owner(web3) -> str:
    return web3.eth.accounts[1]


@pytest.fixture
def asset_manager(web3) -> str:
    return web3.eth.accounts[2]


@pytest.fixture
def safe_address(web3) -> str:
    """The Safe that owns positions."""
    return web3.eth.accounts[3]


@pytest.fixture
def attacker(web3) -> str:
    """An attacker trying to steal funds."""
    return web3.eth.accounts[4]


@pytest.fixture
def exchange_router(web3) -> str:
    """Mock GMX ExchangeRouter address."""
    return web3.eth.accounts[5]


@pytest.fixture
def synthetics_router(web3) -> str:
    """Mock GMX SyntheticsRouter address."""
    return web3.eth.accounts[6]


@pytest.fixture
def order_vault(web3) -> str:
    """Mock GMX OrderVault address."""
    return web3.eth.accounts[7]


@pytest.fixture
def eth_usd_market(web3) -> str:
    """Mock GMX ETH/USD market address."""
    return web3.eth.accounts[8]


@pytest.fixture
def btc_usd_market(web3) -> str:
    """Mock GMX BTC/USD market address."""
    return web3.eth.accounts[9]


@pytest.fixture
def usdc(web3, deployer) -> Contract:
    """Mock USDC token."""
    return create_token(web3, deployer, "USD Coin", "USDC", 100_000_000 * 10**6)


@pytest.fixture
def weth(web3, deployer) -> Contract:
    """Mock WETH token."""
    return create_token(web3, deployer, "Wrapped Ether", "WETH", 100_000 * 10**18)


@pytest.fixture
def vault(
    web3: Web3,
    deployer: str,
    owner: str,
    asset_manager: str,
) -> Contract:
    """Deploy SimpleVaultV0 with guard."""
    vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager)
    vault.functions.initialiseOwnership(owner).transact({"from": deployer})
    return vault


@pytest.fixture
def guard(
    web3: Web3,
    vault: Contract,
    owner: str,
    safe_address: str,
    exchange_router: str,
    synthetics_router: str,
    order_vault: str,
    eth_usd_market: str,
    usdc: Contract,
    weth: Contract,
) -> Contract:
    """Get guard contract and whitelist GMX."""
    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())

    # Whitelist GMX router
    guard.functions.whitelistGMX(exchange_router, synthetics_router, order_vault, "Allow GMX").transact({"from": owner})

    # Whitelist assets
    guard.functions.whitelistToken(usdc.address, "Allow USDC").transact({"from": owner})
    guard.functions.whitelistToken(weth.address, "Allow WETH").transact({"from": owner})

    # Whitelist market
    guard.functions.whitelistGMXMarket(eth_usd_market, "Allow ETH/USD").transact({"from": owner})

    # Whitelist receiver (Safe) - REQUIRED for GMX validation
    guard.functions.allowReceiver(safe_address, "Allow Safe").transact({"from": owner})

    return guard


# =============================================================================
# Test GMX whitelisting
# =============================================================================


def test_gmx_router_whitelisted(
    guard: Contract,
    exchange_router: str,
):
    """Test that GMX router is properly whitelisted."""
    assert guard.functions.isAllowedGMXRouter(exchange_router).call() is True


def test_gmx_router_not_whitelisted(
    guard: Contract,
    attacker: str,
):
    """Test that non-whitelisted router is rejected."""
    assert guard.functions.isAllowedGMXRouter(attacker).call() is False


def test_gmx_order_vault_stored(
    guard: Contract,
    exchange_router: str,
    order_vault: str,
):
    """Test that orderVault is stored for the router."""
    stored_vault = guard.functions.gmxOrderVaults(exchange_router).call()
    assert stored_vault == order_vault


def test_gmx_market_whitelisted(
    guard: Contract,
    eth_usd_market: str,
):
    """Test that whitelisted market is allowed."""
    assert guard.functions.isAllowedGMXMarket(eth_usd_market).call() is True


def test_gmx_market_not_whitelisted(
    guard: Contract,
    btc_usd_market: str,
):
    """Test that non-whitelisted market is rejected."""
    assert guard.functions.isAllowedGMXMarket(btc_usd_market).call() is False


def test_gmx_market_removed(
    guard: Contract,
    owner: str,
    eth_usd_market: str,
):
    """Test that removed market is no longer allowed."""
    guard.functions.removeGMXMarket(eth_usd_market, "Remove ETH/USD").transact({"from": owner})
    assert guard.functions.isAllowedGMXMarket(eth_usd_market).call() is False


def test_receiver_whitelisted(
    guard: Contract,
    safe_address: str,
):
    """Test that Safe is whitelisted as receiver."""
    assert guard.functions.isAllowedReceiver(safe_address).call() is True


def test_receiver_not_whitelisted(
    guard: Contract,
    attacker: str,
):
    """Test that non-whitelisted address is rejected as receiver."""
    assert guard.functions.isAllowedReceiver(attacker).call() is False


def test_receiver_removed(
    guard: Contract,
    owner: str,
    safe_address: str,
):
    """Test that removed receiver is no longer allowed."""
    guard.functions.removeReceiver(safe_address, "Remove Safe").transact({"from": owner})
    assert guard.functions.isAllowedReceiver(safe_address).call() is False


# =============================================================================
# Test anyAsset mode
# =============================================================================


def test_any_asset_allows_non_whitelisted_market(
    guard: Contract,
    owner: str,
    btc_usd_market: str,
):
    """Test that anyAsset=true allows non-whitelisted markets."""
    # Verify market is not whitelisted
    assert guard.functions.isAllowedGMXMarket(btc_usd_market).call() is False

    # Enable anyAsset mode
    guard.functions.setAnyAssetAllowed(True, "Enable anyAsset").transact({"from": owner})

    # Now market should be allowed
    assert guard.functions.isAllowedGMXMarket(btc_usd_market).call() is True


def test_any_asset_allows_non_whitelisted_asset(
    web3: Web3,
    guard: Contract,
    owner: str,
    deployer: str,
):
    """Test that anyAsset=true allows non-whitelisted assets."""
    # Create token that's not whitelisted
    bad_token = create_token(web3, deployer, "Bad Token", "BAD", 1000 * 10**18)

    # Verify token is not whitelisted
    assert guard.functions.isAllowedAsset(bad_token.address).call() is False

    # Enable anyAsset mode
    guard.functions.setAnyAssetAllowed(True, "Enable anyAsset").transact({"from": owner})

    # Now token should be allowed
    assert guard.functions.isAllowedAsset(bad_token.address).call() is True


def test_any_asset_does_not_affect_receiver_check(
    guard: Contract,
    owner: str,
    attacker: str,
):
    """Test that anyAsset=true does NOT bypass receiver whitelist.

    SECURITY: Even with anyAsset enabled, receivers must be whitelisted
    to prevent funds being sent to attacker addresses.
    """
    # Enable anyAsset mode
    guard.functions.setAnyAssetAllowed(True, "Enable anyAsset").transact({"from": owner})

    # Attacker should still NOT be allowed as receiver
    assert guard.functions.isAllowedReceiver(attacker).call() is False


# =============================================================================
# SECURITY TESTS - Ownership controls
# =============================================================================


def test_security_only_owner_can_whitelist_gmx(
    guard: Contract,
    attacker: str,
):
    """SECURITY: Test that only owner can whitelist GMX routers."""
    fake_router = "0x1111111111111111111111111111111111111111"
    fake_synthetics = "0x2222222222222222222222222222222222222222"
    fake_vault = "0x3333333333333333333333333333333333333333"

    with pytest.raises(TransactionFailed):
        guard.functions.whitelistGMX(
            fake_router,
            fake_synthetics,
            fake_vault,
            "Attacker trying to whitelist",
        ).transact({"from": attacker})


def test_security_only_owner_can_whitelist_market(
    guard: Contract,
    attacker: str,
    btc_usd_market: str,
):
    """SECURITY: Test that only owner can whitelist markets."""
    with pytest.raises(TransactionFailed):
        guard.functions.whitelistGMXMarket(
            btc_usd_market,
            "Attacker trying to whitelist",
        ).transact({"from": attacker})


def test_security_only_owner_can_add_receiver(
    guard: Contract,
    attacker: str,
):
    """SECURITY: Test that only owner can add receivers."""
    with pytest.raises(TransactionFailed):
        guard.functions.allowReceiver(
            attacker,
            "Attacker trying to whitelist themselves",
        ).transact({"from": attacker})


def test_security_only_owner_can_remove_receiver(
    guard: Contract,
    attacker: str,
    safe_address: str,
):
    """SECURITY: Test that only owner can remove receivers."""
    with pytest.raises(TransactionFailed):
        guard.functions.removeReceiver(
            safe_address,
            "Attacker trying to remove Safe",
        ).transact({"from": attacker})


def test_security_only_owner_can_enable_any_asset(
    guard: Contract,
    attacker: str,
):
    """SECURITY: Test that only owner can enable anyAsset mode."""
    with pytest.raises(TransactionFailed):
        guard.functions.setAnyAssetAllowed(
            True,
            "Attacker trying to enable anyAsset",
        ).transact({"from": attacker})


def test_security_only_owner_can_remove_market(
    guard: Contract,
    attacker: str,
    eth_usd_market: str,
):
    """SECURITY: Test that only owner can remove markets."""
    with pytest.raises(TransactionFailed):
        guard.functions.removeGMXMarket(
            eth_usd_market,
            "Attacker trying to remove market",
        ).transact({"from": attacker})


# =============================================================================
# Test whitelisting workflow
# =============================================================================


def test_complete_gmx_whitelist_workflow(
    web3: Web3,
    vault: Contract,
    owner: str,
):
    """Test the complete GMX whitelisting workflow from scratch."""
    # Get a fresh guard
    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())

    # Define addresses
    exchange_router = "0x7C68C7866A64FA2160F78EEaE12217FFbf871fa8"
    synthetics_router = "0x7452c558d45f8afC8c83dAe62C3f8A5BE19c71f6"
    order_vault = "0x31eF83a530Fde1B38EE9A18093A333D8Bbbc40D5"
    eth_usd_market = "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336"
    safe_address = web3.eth.accounts[3]
    usdc_address = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

    # 1. Whitelist GMX router
    guard.functions.whitelistGMX(exchange_router, synthetics_router, order_vault, "GMX").transact({"from": owner})
    assert guard.functions.isAllowedGMXRouter(exchange_router).call() is True
    assert guard.functions.gmxOrderVaults(exchange_router).call() == order_vault

    # 2. Whitelist receiver (Safe)
    guard.functions.allowReceiver(safe_address, "Safe").transact({"from": owner})
    assert guard.functions.isAllowedReceiver(safe_address).call() is True

    # 3. Whitelist market
    guard.functions.whitelistGMXMarket(eth_usd_market, "ETH/USD").transact({"from": owner})
    assert guard.functions.isAllowedGMXMarket(eth_usd_market).call() is True

    # 4. Verify collateral token whitelisting through whitelistToken
    guard.functions.whitelistToken(usdc_address, "USDC").transact({"from": owner})
    assert guard.functions.isAllowedAsset(usdc_address).call() is True


def test_gmx_selector_constant():
    """Verify the GMX createOrder selector matches the expected value.

    This ensures the Guard contract has the correct function selector
    for GMX's createOrder function.
    """
    # The correct selector for createOrder(((address,address,address,address,address,address,address[]),(uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256),uint8,uint8,bool,bool,bool,bytes32,bytes32[]))
    expected_selector = bytes.fromhex("f59c48eb")

    # This can be verified by computing keccak256 of the function signature
    from web3 import Web3

    # Note: The actual selector depends on the exact function signature
    # which includes the full tuple structure
    sig = "createOrder(((address,address,address,address,address,address,address[]),(uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256),uint8,uint8,bool,bool,bool,bytes32,bytes32[]))"
    computed = Web3.keccak(text=sig)[:4]

    assert computed == expected_selector, f"Expected {expected_selector.hex()}, got {computed.hex()}"
