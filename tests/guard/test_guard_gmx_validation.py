"""Guard validation tests for GMX multicall trading.

Tests validate that:
1. GMX router whitelisting works correctly
2. GMX market whitelisting works correctly
3. Multicall payload validation catches invalid receivers
4. Multicall payload validation enforces asset whitelist
5. anyAsset mode bypasses whitelist checks
"""

import pytest
from eth_abi import encode
from eth_tester.exceptions import TransactionFailed
from web3 import EthereumTesterProvider, Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import deploy_contract
from eth_defi.token import create_token


# GMX function selectors (must match GuardV0Base.sol)
SEL_GMX_MULTICALL = bytes.fromhex("ac9650d8")
SEL_GMX_SEND_WNT = bytes.fromhex("7d39aaf1")
SEL_GMX_SEND_TOKENS = bytes.fromhex("e6d66ac8")
SEL_GMX_CREATE_ORDER = bytes.fromhex("296ea41f")


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

    # Whitelist receiver (Safe)
    guard.functions.allowReceiver(safe_address, "Allow Safe").transact({"from": owner})

    return guard


def encode_send_wnt(receiver: str, amount: int) -> bytes:
    """Encode sendWnt(address,uint256) call data."""
    return SEL_GMX_SEND_WNT + encode(["address", "uint256"], [receiver, amount])


def encode_send_tokens(token: str, receiver: str, amount: int) -> bytes:
    """Encode sendTokens(address,address,uint256) call data."""
    return SEL_GMX_SEND_TOKENS + encode(["address", "address", "uint256"], [token, receiver, amount])


def encode_create_order(
    receiver: str,
    cancellation_receiver: str,
    callback_contract: str,
    ui_fee_receiver: str,
    market: str,
    initial_collateral_token: str,
) -> bytes:
    """Encode createOrder call data (simplified - just the addresses we validate)."""
    # CreateOrderParams has addresses at fixed offsets
    # We encode the first 6 addresses + empty swapPath
    return SEL_GMX_CREATE_ORDER + encode(["address", "address", "address", "address", "address", "address", "address[]"], [receiver, cancellation_receiver, callback_contract, ui_fee_receiver, market, initial_collateral_token, []])


def encode_multicall(calls: list[bytes]) -> bytes:
    """Encode multicall(bytes[]) call data."""
    return encode(["bytes[]"], [calls])


class TestGMXWhitelisting:
    """Test GMX router and market whitelisting."""

    def test_gmx_router_whitelisted(
        self,
        guard: Contract,
        exchange_router: str,
    ):
        """Test that GMX router is properly whitelisted."""
        assert guard.functions.isAllowedGMXRouter(exchange_router).call() is True

    def test_gmx_router_not_whitelisted(
        self,
        guard: Contract,
        attacker: str,
    ):
        """Test that non-whitelisted router is rejected."""
        assert guard.functions.isAllowedGMXRouter(attacker).call() is False

    def test_gmx_order_vault_stored(
        self,
        guard: Contract,
        exchange_router: str,
        order_vault: str,
    ):
        """Test that orderVault is stored for the router."""
        stored_vault = guard.functions.gmxOrderVaults(exchange_router).call()
        assert stored_vault == order_vault

    def test_gmx_market_whitelisted(
        self,
        guard: Contract,
        eth_usd_market: str,
    ):
        """Test that whitelisted market is allowed."""
        assert guard.functions.isAllowedGMXMarket(eth_usd_market).call() is True

    def test_gmx_market_not_whitelisted(
        self,
        guard: Contract,
        btc_usd_market: str,
    ):
        """Test that non-whitelisted market is rejected."""
        assert guard.functions.isAllowedGMXMarket(btc_usd_market).call() is False

    def test_gmx_market_removed(
        self,
        guard: Contract,
        owner: str,
        eth_usd_market: str,
    ):
        """Test that removed market is no longer allowed."""
        guard.functions.removeGMXMarket(eth_usd_market, "Remove ETH/USD").transact({"from": owner})
        assert guard.functions.isAllowedGMXMarket(eth_usd_market).call() is False


class TestGMXMulticallValidation:
    """Test GMX multicall payload validation."""

    def test_validate_sendwnt_valid_receiver(
        self,
        guard: Contract,
        exchange_router: str,
        safe_address: str,
        order_vault: str,
    ):
        """Test that sendWnt to orderVault is valid."""
        call_data = encode_send_wnt(order_vault, 10**17)  # 0.1 ETH
        multicall_data = encode_multicall([call_data])

        # Should not revert
        guard.functions.validate_gmxMulticall(exchange_router, safe_address, multicall_data).call()

    def test_validate_sendwnt_invalid_receiver(
        self,
        guard: Contract,
        exchange_router: str,
        safe_address: str,
        attacker: str,
    ):
        """Test that sendWnt to wrong address reverts."""
        call_data = encode_send_wnt(attacker, 10**17)
        multicall_data = encode_multicall([call_data])

        with pytest.raises(TransactionFailed, match="GMX sendWnt: invalid receiver"):
            guard.functions.validate_gmxMulticall(exchange_router, safe_address, multicall_data).call()

    def test_validate_sendtokens_valid(
        self,
        guard: Contract,
        exchange_router: str,
        safe_address: str,
        order_vault: str,
        usdc: Contract,
    ):
        """Test that sendTokens with whitelisted token to orderVault is valid."""
        call_data = encode_send_tokens(usdc.address, order_vault, 1000 * 10**6)
        multicall_data = encode_multicall([call_data])

        guard.functions.validate_gmxMulticall(exchange_router, safe_address, multicall_data).call()

    def test_validate_sendtokens_invalid_receiver(
        self,
        guard: Contract,
        exchange_router: str,
        safe_address: str,
        attacker: str,
        usdc: Contract,
    ):
        """Test that sendTokens to wrong address reverts."""
        call_data = encode_send_tokens(usdc.address, attacker, 1000 * 10**6)
        multicall_data = encode_multicall([call_data])

        with pytest.raises(TransactionFailed, match="GMX sendTokens: invalid receiver"):
            guard.functions.validate_gmxMulticall(exchange_router, safe_address, multicall_data).call()

    def test_validate_sendtokens_non_whitelisted_token(
        self,
        web3: Web3,
        guard: Contract,
        exchange_router: str,
        safe_address: str,
        order_vault: str,
        deployer: str,
    ):
        """Test that sendTokens with non-whitelisted token reverts."""
        # Create a token that's not whitelisted
        bad_token = create_token(web3, deployer, "Bad Token", "BAD", 1000 * 10**18)

        call_data = encode_send_tokens(bad_token.address, order_vault, 1000 * 10**18)
        multicall_data = encode_multicall([call_data])

        with pytest.raises(TransactionFailed, match="GMX sendTokens: token not allowed"):
            guard.functions.validate_gmxMulticall(exchange_router, safe_address, multicall_data).call()

    def test_validate_create_order_valid(
        self,
        guard: Contract,
        exchange_router: str,
        safe_address: str,
        eth_usd_market: str,
        usdc: Contract,
    ):
        """Test that createOrder with valid params is accepted."""
        call_data = encode_create_order(
            receiver=safe_address,
            cancellation_receiver=safe_address,
            callback_contract="0x0000000000000000000000000000000000000000",
            ui_fee_receiver="0x0000000000000000000000000000000000000000",
            market=eth_usd_market,
            initial_collateral_token=usdc.address,
        )
        multicall_data = encode_multicall([call_data])

        guard.functions.validate_gmxMulticall(exchange_router, safe_address, multicall_data).call()

    def test_validate_create_order_wrong_receiver(
        self,
        guard: Contract,
        exchange_router: str,
        safe_address: str,
        attacker: str,
        eth_usd_market: str,
        usdc: Contract,
    ):
        """Test that createOrder with wrong receiver reverts."""
        call_data = encode_create_order(
            receiver=attacker,  # Wrong!
            cancellation_receiver=safe_address,
            callback_contract="0x0000000000000000000000000000000000000000",
            ui_fee_receiver="0x0000000000000000000000000000000000000000",
            market=eth_usd_market,
            initial_collateral_token=usdc.address,
        )
        multicall_data = encode_multicall([call_data])

        with pytest.raises(TransactionFailed, match="GMX createOrder: receiver must be Safe"):
            guard.functions.validate_gmxMulticall(exchange_router, safe_address, multicall_data).call()

    def test_validate_create_order_wrong_cancellation_receiver(
        self,
        guard: Contract,
        exchange_router: str,
        safe_address: str,
        attacker: str,
        eth_usd_market: str,
        usdc: Contract,
    ):
        """Test that createOrder with wrong cancellationReceiver reverts."""
        call_data = encode_create_order(
            receiver=safe_address,
            cancellation_receiver=attacker,  # Wrong!
            callback_contract="0x0000000000000000000000000000000000000000",
            ui_fee_receiver="0x0000000000000000000000000000000000000000",
            market=eth_usd_market,
            initial_collateral_token=usdc.address,
        )
        multicall_data = encode_multicall([call_data])

        with pytest.raises(TransactionFailed, match="GMX createOrder: cancellationReceiver must be Safe"):
            guard.functions.validate_gmxMulticall(exchange_router, safe_address, multicall_data).call()

    def test_validate_create_order_non_whitelisted_market(
        self,
        guard: Contract,
        exchange_router: str,
        safe_address: str,
        btc_usd_market: str,  # Not whitelisted
        usdc: Contract,
    ):
        """Test that createOrder with non-whitelisted market reverts."""
        call_data = encode_create_order(
            receiver=safe_address,
            cancellation_receiver=safe_address,
            callback_contract="0x0000000000000000000000000000000000000000",
            ui_fee_receiver="0x0000000000000000000000000000000000000000",
            market=btc_usd_market,
            initial_collateral_token=usdc.address,
        )
        multicall_data = encode_multicall([call_data])

        with pytest.raises(TransactionFailed, match="GMX createOrder: market not allowed"):
            guard.functions.validate_gmxMulticall(exchange_router, safe_address, multicall_data).call()

    def test_validate_create_order_non_whitelisted_collateral(
        self,
        web3: Web3,
        guard: Contract,
        exchange_router: str,
        safe_address: str,
        eth_usd_market: str,
        deployer: str,
    ):
        """Test that createOrder with non-whitelisted collateral reverts."""
        bad_token = create_token(web3, deployer, "Bad Token", "BAD", 1000 * 10**18)

        call_data = encode_create_order(
            receiver=safe_address,
            cancellation_receiver=safe_address,
            callback_contract="0x0000000000000000000000000000000000000000",
            ui_fee_receiver="0x0000000000000000000000000000000000000000",
            market=eth_usd_market,
            initial_collateral_token=bad_token.address,
        )
        multicall_data = encode_multicall([call_data])

        with pytest.raises(TransactionFailed, match="GMX createOrder: collateral not allowed"):
            guard.functions.validate_gmxMulticall(exchange_router, safe_address, multicall_data).call()

    def test_validate_unknown_function_in_multicall(
        self,
        guard: Contract,
        exchange_router: str,
        safe_address: str,
    ):
        """Test that unknown function selector in multicall reverts."""
        # Create call with unknown selector
        unknown_call = bytes.fromhex("deadbeef") + encode(["uint256"], [123])
        multicall_data = encode_multicall([unknown_call])

        with pytest.raises(TransactionFailed, match="GMX: Unknown function in multicall"):
            guard.functions.validate_gmxMulticall(exchange_router, safe_address, multicall_data).call()

    def test_validate_non_whitelisted_router(
        self,
        guard: Contract,
        attacker: str,
        safe_address: str,
        order_vault: str,
    ):
        """Test that non-whitelisted router reverts."""
        call_data = encode_send_wnt(order_vault, 10**17)
        multicall_data = encode_multicall([call_data])

        with pytest.raises(TransactionFailed, match="GMX router not allowed"):
            guard.functions.validate_gmxMulticall(
                attacker,  # Not whitelisted
                safe_address,
                multicall_data,
            ).call()


class TestGMXAnyAssetMode:
    """Test that anyAsset mode bypasses whitelist checks."""

    def test_any_asset_allows_non_whitelisted_market(
        self,
        web3: Web3,
        guard: Contract,
        owner: str,
        exchange_router: str,
        safe_address: str,
        btc_usd_market: str,  # Not whitelisted
        usdc: Contract,
    ):
        """Test that anyAsset=true allows non-whitelisted markets."""
        # Enable anyAsset mode
        guard.functions.setAnyAssetAllowed(True, "Enable anyAsset").transact({"from": owner})

        call_data = encode_create_order(
            receiver=safe_address,
            cancellation_receiver=safe_address,
            callback_contract="0x0000000000000000000000000000000000000000",
            ui_fee_receiver="0x0000000000000000000000000000000000000000",
            market=btc_usd_market,
            initial_collateral_token=usdc.address,
        )
        multicall_data = encode_multicall([call_data])

        # Should not revert with anyAsset=true
        guard.functions.validate_gmxMulticall(exchange_router, safe_address, multicall_data).call()

    def test_any_asset_allows_non_whitelisted_collateral(
        self,
        web3: Web3,
        guard: Contract,
        owner: str,
        exchange_router: str,
        safe_address: str,
        eth_usd_market: str,
        deployer: str,
    ):
        """Test that anyAsset=true allows non-whitelisted collateral."""
        # Enable anyAsset mode
        guard.functions.setAnyAssetAllowed(True, "Enable anyAsset").transact({"from": owner})

        # Create token that's not whitelisted
        bad_token = create_token(web3, deployer, "Bad Token", "BAD", 1000 * 10**18)

        call_data = encode_create_order(
            receiver=safe_address,
            cancellation_receiver=safe_address,
            callback_contract="0x0000000000000000000000000000000000000000",
            ui_fee_receiver="0x0000000000000000000000000000000000000000",
            market=eth_usd_market,
            initial_collateral_token=bad_token.address,
        )
        multicall_data = encode_multicall([call_data])

        # Should not revert with anyAsset=true
        guard.functions.validate_gmxMulticall(exchange_router, safe_address, multicall_data).call()

    def test_any_asset_still_validates_receivers(
        self,
        guard: Contract,
        owner: str,
        exchange_router: str,
        safe_address: str,
        attacker: str,
        eth_usd_market: str,
        usdc: Contract,
    ):
        """Test that anyAsset=true still validates receiver addresses."""
        # Enable anyAsset mode
        guard.functions.setAnyAssetAllowed(True, "Enable anyAsset").transact({"from": owner})

        # Try to send to attacker - should still fail
        call_data = encode_create_order(
            receiver=attacker,  # Wrong receiver - still fails even with anyAsset
            cancellation_receiver=safe_address,
            callback_contract="0x0000000000000000000000000000000000000000",
            ui_fee_receiver="0x0000000000000000000000000000000000000000",
            market=eth_usd_market,
            initial_collateral_token=usdc.address,
        )
        multicall_data = encode_multicall([call_data])

        with pytest.raises(TransactionFailed, match="GMX createOrder: receiver must be Safe"):
            guard.functions.validate_gmxMulticall(exchange_router, safe_address, multicall_data).call()


class TestGMXCompleteMulticall:
    """Test complete multicall scenarios with multiple inner calls."""

    def test_complete_order_multicall(
        self,
        guard: Contract,
        exchange_router: str,
        safe_address: str,
        order_vault: str,
        eth_usd_market: str,
        usdc: Contract,
        weth: Contract,
    ):
        """Test a complete order multicall with sendWnt + sendTokens + createOrder."""
        calls = [
            # Send execution fee (WETH/native)
            encode_send_wnt(order_vault, 10**16),  # 0.01 ETH
            # Send collateral
            encode_send_tokens(usdc.address, order_vault, 1000 * 10**6),  # 1000 USDC
            # Create order
            encode_create_order(
                receiver=safe_address,
                cancellation_receiver=safe_address,
                callback_contract="0x0000000000000000000000000000000000000000",
                ui_fee_receiver="0x0000000000000000000000000000000000000000",
                market=eth_usd_market,
                initial_collateral_token=usdc.address,
            ),
        ]
        multicall_data = encode_multicall(calls)

        # Should not revert
        guard.functions.validate_gmxMulticall(exchange_router, safe_address, multicall_data).call()

    def test_close_position_multicall(
        self,
        guard: Contract,
        exchange_router: str,
        safe_address: str,
        order_vault: str,
        eth_usd_market: str,
        usdc: Contract,
    ):
        """Test closing a position - only execution fee, no collateral."""
        calls = [
            # Send execution fee only
            encode_send_wnt(order_vault, 10**16),  # 0.01 ETH
            # Create close order (no collateral sent)
            encode_create_order(
                receiver=safe_address,
                cancellation_receiver=safe_address,
                callback_contract="0x0000000000000000000000000000000000000000",
                ui_fee_receiver="0x0000000000000000000000000000000000000000",
                market=eth_usd_market,
                initial_collateral_token=usdc.address,
            ),
        ]
        multicall_data = encode_multicall(calls)

        guard.functions.validate_gmxMulticall(exchange_router, safe_address, multicall_data).call()
