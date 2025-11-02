// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.0;

import "forge-std/Test.sol";
import "forge-std/console.sol";
import {IERC20} from "forge-std/interfaces/IERC20.sol";

import "./interfaces/IGmxV2.sol";
import "./constants/GmxArbitrumAddresses.sol";
import "./utils/GmxForkHelpers.sol";

/**
 * GMX Order Executor Contract
 * @dev Provides external methods for executing GMX orders with proper oracle setup
 *
 * This contract uses GmxArbitrumAddresses for all contract references.
 * It handles:
 * - Mock oracle provider setup (Chainlink Data Streams)
 * - Order execution with proper oracle parameters
 * - Position key derivation
 *
 * Usage:
 * 1. Deploy contract (no initialization needed - uses GmxArbitrumAddresses)
 * 2. Call executeOrderWithOracle() with the order key and oracle prices
 * 3. The contract handles all oracle setup and keeper management internally
 */

// TODO: First fix order execution then the prank part
contract GmxOrderExecutor is Test, GmxForkHelpers {
    using GmxArbitrumAddresses for *;

    // Active keeper address (cached after first lookup)
    address internal keeper;

    // User address (set when executing)
    address internal user;

    // Test prices for oracle provider
    uint256 internal ethPrice;
    uint256 internal usdcPrice;

    /**
     * Initialize the executor (loads keeper from RoleStore)
     * This is called implicitly on first order execution
     */
    function _ensureInitialized() internal {
        if (keeper == address(0)) {
            // Set all GMX contracts from GmxArbitrumAddresses
            exchangeRouter = IExchangeRouter(GmxArbitrumAddresses.EXCHANGE_ROUTER);
            orderHandler = IOrderHandler(GmxArbitrumAddresses.ORDER_HANDLER);
            oracle = IOracle(GmxArbitrumAddresses.ORACLE);
            reader = IReader(GmxArbitrumAddresses.READER);
            dataStore = IDataStore(GmxArbitrumAddresses.DATA_STORE);
            roleStore = IRoleStore(GmxArbitrumAddresses.ROLE_STORE);
            oracleStore = IOracleStore(GmxArbitrumAddresses.ORACLE_STORE);

            // Get active keeper
            keeper = getActiveKeeper();
        }
    }

    /**
     * Execute an order with oracle setup
     * @dev Handles mock oracle provider setup and keeper execution
     * @param orderKey The order to execute
     * @param ethPriceUsd ETH price in USD (unscaled, e.g., 3892 for $3,892)
     * @param usdcPriceUsd USDC price in USD (typically 1)
     * @param executingUser The user who created the order (for position key derivation)
     * @return positionKey The resulting position key (for long/short positions)
     */
    function executeOrderWithOracle(
        bytes32 orderKey,
        uint256 ethPriceUsd,
        uint256 usdcPriceUsd,
        address executingUser
    ) external returns (bytes32 positionKey) {
        _ensureInitialized();

        user = executingUser;
        ethPrice = ethPriceUsd;
        usdcPrice = usdcPriceUsd;

        // Setup mock oracle provider with prices
        _setupMockOracleProvider();

        // Execute order as keeper
        vm.startPrank(keeper);
        _executeOrderInternal(orderKey);
        vm.stopPrank();

        // Return position key for assertion
        return getPositionKey(user, GmxArbitrumAddresses.ETH_USD_MARKET, GmxArbitrumAddresses.WETH, true);
    }

    /**
     * Execute an order with default oracle prices
     * @dev Convenience method using standard test prices
     * @param orderKey The order to execute
     * @param executingUser The user who created the order
     * @return positionKey The resulting position key
     */
    function executeOrderWithDefaultPrices(
        bytes32 orderKey,
        address executingUser
    ) external returns (bytes32 positionKey) {
        _ensureInitialized();

        user = executingUser;
        ethPrice = 3892;  // ETH price ($3,892)
        usdcPrice = 1;    // USDC price ($1)

        // Setup mock oracle provider with prices
        _setupMockOracleProvider();

        // Execute order as keeper
        // TODO: We can't just do this in solidity. Use unlocked address in anvil
        vm.startPrank(keeper);
        _executeOrderInternal(orderKey);
        vm.stopPrank();

        // Return position key for assertion
        return getPositionKey(user, GmxArbitrumAddresses.ETH_USD_MARKET, GmxArbitrumAddresses.WETH, true);
    }

    /**
     * Execute a decrease order (closing a position)
     * @dev Similar to executeOrderWithOracle but for position closing
     * @param orderKey The close order to execute
     * @param ethPriceUsd ETH price for oracle
     * @param usdcPriceUsd USDC price for oracle
     */
    function executeDecreaseOrder(
        bytes32 orderKey,
        uint256 ethPriceUsd,
        uint256 usdcPriceUsd
    ) external {
        _ensureInitialized();

        ethPrice = ethPriceUsd;
        usdcPrice = usdcPriceUsd;

        // Setup mock oracle provider
        _setupMockOracleProvider();

        // Execute as keeper
        vm.startPrank(keeper);
        _executeOrderInternal(orderKey);
        vm.stopPrank();
    }

    // ============================================================================
    // Internal Functions
    // ============================================================================

    /**
     * Setup mock oracle provider bytecode
     * @dev Replaces oracle provider contract with mock that returns configured prices
     */
    function _setupMockOracleProvider() internal {
        // This would use vm.etch to set mock bytecode
        setupMockOracleProvider(ethPrice, usdcPrice);
    }

    /**
     * Execute order with oracle parameters
     * @dev Internal function that builds oracle params and calls orderHandler.executeOrder()
     */
    function _executeOrderInternal(bytes32 orderKey) internal {
        vm.startPrank(keeper);
        OracleUtils.SetPricesParams memory oracleParams;
        oracleParams.tokens = new address[](2);
        oracleParams.tokens[0] = address(GmxArbitrumAddresses.WETH);
        oracleParams.tokens[1] = address(GmxArbitrumAddresses.USDC);
        oracleParams.providers = new address[](2);
        oracleParams.providers[0] = GmxArbitrumAddresses.CHAINLINK_DATA_STREAM_PROVIDER;
        oracleParams.providers[1] = GmxArbitrumAddresses.CHAINLINK_DATA_STREAM_PROVIDER;
        oracleParams.data = new bytes[](2);
        orderHandler.executeOrder(orderKey, oracleParams);
        vm.stopPrank();
    }

    // For debugging. Because we all know I can mess things up
    /**
     * Get keeper address
     * @return The active order keeper address
     */
    function getKeeperAddress() external view returns (address) {
        return keeper;
    }

    /**
     * Get stored user address
     * @return The user whose order is being executed
     */
    function getUserAddress() external view returns (address) {
        return user;
    }
}
