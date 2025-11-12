// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.0;

import "forge-std/Test.sol";
import "forge-std/console.sol";
import { IERC20 } from "forge-std/interfaces/IERC20.sol";

import "../interfaces/IGmxV2.sol";
import "../constants/GmxArbitrumAddresses.sol";
import "../mock/MockOracleProvider.sol";

/**
 * Helper utilities for GMX fork testing
 * @dev Provides common functions for order creation, oracle mocking, and state queries
 */
abstract contract GmxForkHelpers is Test {
    using GmxArbitrumAddresses for *;

    // GMX contracts (loaded in inheriting contract setUp)
    IExchangeRouter internal exchangeRouter;
    IOrderHandler internal orderHandler;
    IOracle internal oracle;
    IReader internal reader;
    IDataStore internal dataStore;
    IRoleStore internal roleStore;
    IOracleStore internal oracleStore;

    // ============================================================================
    // Token Funding Helpers
    // ============================================================================

    /// Fund an address with native ETH
    function dealETH(address recipient, uint256 amount) internal {
        vm.deal(recipient, amount);
    }

    /// Fund an address with ERC20 tokens using Foundry's deal cheatcode
    /// @dev This works for most standard ERC20 tokens
    function dealTokens(address token, address recipient, uint256 amount) internal {
        deal(token, recipient, amount);
    }

    // ============================================================================
    // Keeper Management
    // ============================================================================

    /// Get first active ORDER_KEEPER from RoleStore
    /// @return keeper address of an active keeper
    function getActiveKeeper() internal view returns (address keeper) {
        uint256 keeperCount = roleStore.getRoleMemberCount(Keys.ORDER_KEEPER);
        require(keeperCount > 0, "No ORDER_KEEPERs found");

        address[] memory keepers = roleStore.getRoleMembers(Keys.ORDER_KEEPER, 0, 1);
        keeper = keepers[0];

        console.log("Active keeper found:", keeper);
    }

    // ============================================================================
    // Execution Fee
    // ============================================================================

    /// Get execution fee for order creation
    /// @dev Hardcoded for simplicity in this example. In production, this should be calculated
    /// based on gas limits from DataStore, multiplier factors, oracle price counts and current gas price
    function getExecutionFee() internal pure returns (uint256) {
        return 0.0002 ether;
    }

    // ============================================================================
    // Order Parameter Builders
    // ============================================================================

    /// Create parameters for a MarketIncrease order (open/increase long/short position)
    /// @param market Market address
    /// @param collateralToken Token to use as collateral
    /// @param collateralAmount Amount of collateral in token decimals (execution fee will be added if token is WETH)
    /// @param sizeDeltaUsd Position size in USD (scaled by 1e30)
    /// @param isLong true for long, false for short
    function createIncreaseOrderParams(
        address market,
        address collateralToken,
        uint256 collateralAmount,
        uint256 sizeDeltaUsd,
        bool isLong
    ) internal view returns (IExchangeRouter.CreateOrderParams memory params) {
        address[] memory emptySwapPath = new address[](0);
        uint256 executionFee = getExecutionFee();

        // When collateral token is WETH, execution fee is deducted from transferred amount
        // So we need to add it to the collateral amount
        uint256 initialCollateralDeltaAmount = collateralAmount;
        if (collateralToken == GmxArbitrumAddresses.WETH) {
            initialCollateralDeltaAmount = collateralAmount + executionFee;
        }

        params.addresses = IExchangeRouter.CreateOrderParamsAddresses({
            receiver: address(this),
            cancellationReceiver: address(this),
            callbackContract: address(0),
            uiFeeReceiver: address(0),
            market: market,
            initialCollateralToken: collateralToken,
            swapPath: emptySwapPath
        });

        params.numbers = IExchangeRouter.CreateOrderParamsNumbers({
            sizeDeltaUsd: sizeDeltaUsd,
            initialCollateralDeltaAmount: initialCollateralDeltaAmount,
            triggerPrice: 0, // 0 for market orders
            acceptablePrice: isLong ? type(uint256).max : 1, // Match fuzzing: max for long, 1 for short
            executionFee: executionFee,
            callbackGasLimit: 200000, // Match fuzzing: 200k gas for callbacks
            minOutputAmount: 1, // Match fuzzing: minimal output requirement
            validFromTime: 0
        });

        params.orderType = IExchangeRouter.OrderType.MarketIncrease;
        params.decreasePositionSwapType = IExchangeRouter.DecreasePositionSwapType.NoSwap;
        params.isLong = isLong;
        params.shouldUnwrapNativeToken = false;
        params.autoCancel = true; // Match fuzzing: enable auto-cancel
        params.referralCode = bytes32(0);
        params.dataList = new bytes32[](0);
    }

    /// Create parameters for a MarketDecrease order (close/decrease position)
    /// @param market Market address
    /// @param collateralToken Collateral token of the position
    /// @param sizeDeltaUsd Position size to decrease in USD (scaled by 1e30)
    /// @param isLong true for long, false for short
    function createDecreaseOrderParams(address market, address collateralToken, uint256 sizeDeltaUsd, bool isLong)
        internal
        view
        returns (IExchangeRouter.CreateOrderParams memory params)
    {
        address[] memory emptySwapPath = new address[](0);

        params.addresses = IExchangeRouter.CreateOrderParamsAddresses({
            receiver: address(this),
            cancellationReceiver: address(this),
            callbackContract: address(0),
            uiFeeReceiver: address(0),
            market: market,
            initialCollateralToken: collateralToken,
            swapPath: emptySwapPath
        });

        params.numbers = IExchangeRouter.CreateOrderParamsNumbers({
            sizeDeltaUsd: sizeDeltaUsd,
            initialCollateralDeltaAmount: 0, // For decrease, we're closing, not adding collateral
            triggerPrice: 0,
            acceptablePrice: isLong ? 0 : type(uint256).max, // No slippage limit
            executionFee: getExecutionFee(),
            callbackGasLimit: 0,
            minOutputAmount: 0,
            validFromTime: 0
        });

        params.orderType = IExchangeRouter.OrderType.MarketDecrease;
        params.decreasePositionSwapType = IExchangeRouter.DecreasePositionSwapType.NoSwap;
        params.isLong = isLong;
        params.shouldUnwrapNativeToken = false;
        params.autoCancel = false;
        params.referralCode = bytes32(0);
        params.dataList = new bytes32[](0);
    }

    // ============================================================================
    // Event Parsing
    // ============================================================================

    /// Extract order key from OrderCreated event logs
    /// @param logs Transaction logs
    /// @return orderKey The created order's key
    function getOrderKeyFromLogs(Vm.Log[] memory logs) internal pure returns (bytes32 orderKey) {
        // OrderCreated event signature: OrderCreated(bytes32 key, ...)
        bytes32 orderCreatedTopic = keccak256("OrderCreated(bytes32,Order.Props)");

        for (uint256 i = 0; i < logs.length; i++) {
            if (logs[i].topics[0] == orderCreatedTopic) {
                orderKey = logs[i].topics[1]; // First indexed parameter is the key
                return orderKey;
            }
        }

        revert("OrderCreated event not found in logs");
    }

    // ============================================================================
    // Oracle Price Mocking
    // ============================================================================

    /// Mock GMX oracle provider calls for testing
    /// @dev Uses vm.etch to replace the provider contract with our mock
    /// @param wethPrice WETH price in USD (e.g., 5000 for $5000/ETH)
    /// @param usdcPrice USDC price in USD (e.g., 1 for $1/USDC)
    /// @return mockProviderAddress The address of the actual provider being mocked
    function setupMockOracleProvider(uint256 wethPrice, uint256 usdcPrice)
        internal
        returns (address mockProviderAddress)
    {
        // GMX price format: price * 10^30 / 10^tokenDecimals
        // For WETH (18 decimals): $5000 = 5000 * 10^30 / 10^18 = 5000 * 10^12
        // For USDC (6 decimals): $1 = 1 * 10^30 / 10^6 = 1 * 10^24
        uint256 wethPriceFormatted = wethPrice * 1e12;
        uint256 usdcPriceFormatted = usdcPrice * 1e24;

        // Use the actual Data Streams provider address from mainnet
        // This is what production uses (verified from etherscan transaction)
        address providerAddress = GmxArbitrumAddresses.CHAINLINK_DATA_STREAM_PROVIDER;

        // Deploy a MockOracleProvider implementation
        MockOracleProvider mockImpl = new MockOracleProvider();

        // Replace the bytecode at the production provider address with our mock
        vm.etch(providerAddress, address(mockImpl).code);

        // Configure prices in the mock (now at the production address)
        MockOracleProvider(providerAddress).setPrice(GmxArbitrumAddresses.WETH, wethPriceFormatted, wethPriceFormatted);

        MockOracleProvider(providerAddress).setPrice(GmxArbitrumAddresses.USDC, usdcPriceFormatted, usdcPriceFormatted);

        console.log("Replaced oracle provider bytecode at:", providerAddress);
        console.log("WETH price set to:", wethPriceFormatted);
        console.log("USDC price set to:", usdcPriceFormatted);

        return providerAddress;
    }

    // ============================================================================
    // Order Queries
    // ============================================================================

    /// Get total order count from global order list
    /// @return count Number of orders in the global list
    function getOrderCount() internal view returns (uint256) {
        return dataStore.getBytes32Count(Keys.ORDER_LIST);
    }

    /// Get order count for a specific account
    /// @param account Account address
    /// @return count Number of orders for the account
    function getAccountOrderCount(address account) internal view returns (uint256) {
        bytes32 accountOrderListKey = Keys.accountOrderListKey(account);
        return dataStore.getBytes32Count(accountOrderListKey);
    }

    // ============================================================================
    // Position Queries
    // ============================================================================

    /// Get position for an account
    /// @param account Position owner
    /// @param market Market address
    /// @param collateralToken Collateral token
    /// @param isLong Long or short position
    function getPosition(address account, address market, address collateralToken, bool isLong)
        internal
        view
        returns (Position.Props memory)
    {
        bytes32 positionKey = getPositionKey(account, market, collateralToken, isLong);
        return reader.getPosition(address(dataStore), positionKey);
    }

    /// Get total position count from global position list
    /// @return count Number of positions in the global list
    function getPositionCount() internal view returns (uint256) {
        return dataStore.getBytes32Count(Keys.POSITION_LIST);
    }

    /// Get position keys from global position list
    /// @param start Starting index
    /// @param end Ending index
    /// @return Position keys in the specified range
    function getPositionKeys(uint256 start, uint256 end) internal view returns (bytes32[] memory) {
        return dataStore.getBytes32ValuesAt(Keys.POSITION_LIST, start, end);
    }

    /// Get position count for a specific account
    /// @param account Account address
    /// @return count Number of positions for the account
    function getAccountPositionCount(address account) internal view returns (uint256) {
        bytes32 accountPositionListKey = Keys.accountPositionListKey(account);
        return dataStore.getBytes32Count(accountPositionListKey);
    }

    /// Get position keys for a specific account
    /// @param account Account address
    /// @param start Starting index
    /// @param end Ending index
    /// @return Position keys for the account in the specified range
    function getAccountPositionKeys(address account, uint256 start, uint256 end)
        internal
        view
        returns (bytes32[] memory)
    {
        bytes32 accountPositionListKey = Keys.accountPositionListKey(account);
        return dataStore.getBytes32ValuesAt(accountPositionListKey, start, end);
    }

    /// Get pending impact amount key for a position
    /// @param positionKey The position key
    /// @return The pending impact amount key
    function getPendingImpactAmountKey(bytes32 positionKey) internal pure returns (bytes32) {
        // PENDING_IMPACT_AMOUNT = keccak256(abi.encode("PENDING_IMPACT_AMOUNT"))
        bytes32 PENDING_IMPACT_AMOUNT = keccak256(abi.encode("PENDING_IMPACT_AMOUNT"));
        return keccak256(abi.encode(positionKey, PENDING_IMPACT_AMOUNT));
    }

    /// Compute position key from parameters (following Hardhat test pattern)
    /// @dev Position key = keccak256(abi.encodePacked(account, market, collateralToken, isLong))
    /// @param account Position owner
    /// @param market Market address
    /// @param collateralToken Collateral token address
    /// @param isLong True for long, false for short
    /// @return Position key (keccak256 hash of parameters)
    function getPositionKey(address account, address market, address collateralToken, bool isLong)
        internal
        pure
        returns (bytes32)
    {
        return keccak256(abi.encodePacked(account, market, collateralToken, isLong));
    }
}
