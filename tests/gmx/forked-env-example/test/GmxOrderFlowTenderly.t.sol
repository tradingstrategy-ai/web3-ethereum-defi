// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.0;

import "forge-std/Test.sol";
import "forge-std/console.sol";
import { IERC20 } from "forge-std/interfaces/IERC20.sol";

import "../contracts/interfaces/IGmxV2.sol";
import "../contracts/constants/GmxArbitrumAddresses.sol";
import "../contracts/utils/GmxForkHelpers.sol";

/**
 * GMX Order Flow Tests for Tenderly RPC
 * @dev Clone of GmxOrderFlowTest.t.sol that runs on real Tenderly RPC (no forking)
 *
 * Usage (ONE COMMAND):
 *   forge test --rpc-url $TENDERLY_RPC_URL -vvv
 *
 *
 * Example:
 *   export TENDERLY_RPC_URL=https://virtual.arbitrum.eu.rpc.tenderly.co/4dd545a1-536a-462d-ae0f-388f6e23793f
 *   export PRIVATE_KEY=0x...
 *   forge test --rpc-url $TENDERLY_RPC_URL -vvv
 */
contract GmxOrderFlowTenderlyTest is Test, GmxForkHelpers {
    // Test user (derived from PRIVATE_KEY)
    address user;

    // Tokens
    IERC20 weth;
    IERC20 usdc;

    // Test parameters
    uint256 constant INITIAL_ETH_BALANCE = 100 ether;

    // GMX price format: price * 1e30 / (10^tokenDecimals)
    uint256 constant ETH_PRICE_USD = 3342; // Match mainnet order execution price ($3,892.32 rounded)
    uint256 constant USDC_PRICE_USD = 1; // $1 per USDC

    uint256 constant ETH_COLLATERAL = 0.001 ether; // 0.001 ETH collateral

    // Active keeper address (queried in setUp)
    address keeper;

    function setUp() public {
        console.log("=== Tenderly RPC Setup ===");
        console.log("Chain ID:", block.chainid);
        console.log("Block number:", block.number);
        console.log("==================");

        // Load GMX contracts using deployed addresses
        exchangeRouter = IExchangeRouter(GmxArbitrumAddresses.EXCHANGE_ROUTER);
        orderHandler = IOrderHandler(GmxArbitrumAddresses.ORDER_HANDLER);
        oracle = IOracle(GmxArbitrumAddresses.ORACLE);
        reader = IReader(GmxArbitrumAddresses.READER);
        dataStore = IDataStore(GmxArbitrumAddresses.DATA_STORE);
        roleStore = IRoleStore(GmxArbitrumAddresses.ROLE_STORE);
        oracleStore = IOracleStore(GmxArbitrumAddresses.ORACLE_STORE);

        // Load token contracts
        weth = IERC20(GmxArbitrumAddresses.WETH);
        usdc = IERC20(GmxArbitrumAddresses.USDC);

        // Get user from PRIVATE_KEY env var
        uint256 privateKey = vm.envUint("PRIVATE_KEY");
        user = vm.addr(privateKey);
        console.log("User address:", user);
        console.log("User ETH balance:", user.balance / 1e18, "ETH");

        // Auto-fund user if needed (using FFI to call Tenderly admin RPC)
        if (user.balance < INITIAL_ETH_BALANCE) {
            console.log("Funding user with", INITIAL_ETH_BALANCE / 1e18, "ETH via Tenderly admin RPC...");
            _tenderlySetBalance(user, INITIAL_ETH_BALANCE);
            console.log("User funded. New balance:", user.balance / 1e18, "ETH");
        }

        // Get active keeper for order execution
        keeper = getActiveKeeper();

        console.log("==================\n");
    }

    // ============================================================================
    // Test 1: Open Long Position
    // ============================================================================

    /// forge test --rpc-url $TENDERLY_RPC_URL --match-test testOpenLongPosition -vvv

    /// Test opening a long ETH position
    /// @dev This test demonstrates the complete flow of creating and executing a MarketIncrease order
    function testOpenLongPosition() public {
        console.log("\n=== TEST: Open Long ETH Position ===\n");

        // Test parameters - MATCH MAINNET ORDER EXACTLY
        // Mainnet reference: 0x68a77542fd9ba2bcd342099158dd17c0918cee70726ecd2e2446b0f16c46da50
        // Mainnet: User sent 0.001 ETH as native ETH, Router wrapped to WETH collateral, 2.5x leverage → ~$9.7 position at $3,892/ETH
        console.log("Opening position with:");
        console.log(
            "- Collateral: %s wei (~$%s at $%s/ETH)",
            ETH_COLLATERAL,
            ETH_COLLATERAL * ETH_PRICE_USD / 1e18,
            ETH_PRICE_USD
        );
        console.log("- Position Size: $%s", (ETH_COLLATERAL * ETH_PRICE_USD * 2.5e30) / 1e18 / 1e30);
        console.log("- Leverage: 2.5x");
        console.log("- Direction: LONG");

        // Record initial balances
        uint256 initialEthBalance = user.balance;
        uint256 initialKeeperEthBalance = keeper.balance;

        console.log("Initial balances:");
        console.log("User ETH --> %s (wei), (~%s ETH)", initialEthBalance, initialEthBalance / 1e18);
        console.log("Keeper ETH --> %s (wei), (~%s ETH)", initialKeeperEthBalance, initialKeeperEthBalance / 1e18);
        console.log("");

        // === Step 1: Record initial state ===
        uint256 orderCount = getOrderCount();
        uint256 userOrderCount = getAccountOrderCount(user);
        uint256 userPositionCount = getAccountPositionCount(user);
        uint256 positionCount = getPositionCount();

        bytes32 actualPositionKey;

        // === Step 2: Create order ===
        {
            bytes32 orderKey = _createOrder(ETH_COLLATERAL, true);
            console.log("Order created. Order key:", vm.toString(orderKey), "\n");

            // Verify order was created
            assertEq(getOrderCount(), orderCount + 1, "Order count +1");
            assertEq(getAccountOrderCount(user), userOrderCount + 1, "User order count +1");
            assertEq(getAccountPositionCount(user), userPositionCount, "Position count unchanged");

            // === Step 3: Execute order ===
            actualPositionKey = _executeOrder(orderKey);
            console.log("Position created. Position key:", vm.toString(actualPositionKey), "\n");
        }

        // === Step 4: Verify final state ===
        assertEq(getOrderCount(), orderCount, "Order count back to initial (order consumed)");
        assertEq(getAccountOrderCount(user), userOrderCount, "User order count back to initial");
        assertEq(getAccountPositionCount(user), userPositionCount + 1, "User position count +1");
        assertEq(getPositionCount(), positionCount + 1, "Global position count +1");

        console.log("Final balances:");
        uint256 finalEthBalance = user.balance;
        uint256 finalKeeperEthBalance = keeper.balance;
        console.log(
            "User ETH --> %s (wei), (~%s ETH), diff: -%s (wei)",
            finalEthBalance,
            finalEthBalance / 1e18,
            initialEthBalance - finalEthBalance
        );
        console.log(
            "Keeper ETH --> %s (wei), (~%s ETH), diff: +%s (wei)",
            finalKeeperEthBalance,
            finalKeeperEthBalance / 1e18,
            finalKeeperEthBalance - initialKeeperEthBalance
        );
    }

    // ============================================================================
    // Test 2: Close Long Position
    // ============================================================================

    /// forge test --rpc-url $TENDERLY_RPC_URL --match-test testCloseLongPosition -vvv

    /// Test closing a long ETH position
    /// @dev This test first opens a position, then closes it completely
    function testCloseLongPosition() public {
        console.log("\n=== TEST: Close Long ETH Position ===\n");

        // === Step 1: Open Position ===
        bytes32 orderKey = _createOrder(ETH_COLLATERAL, true);
        bytes32 positionKey = _executeOrder(orderKey);
        console.log("Position created. Position key:", vm.toString(positionKey), "\n");

        // Record state after opening
        uint256 initialWethBalance = weth.balanceOf(user);

        assertEq(getAccountPositionCount(user), 1, "Should have 1 position after opening");

        // === Step 2: Close Position ===
        uint256 positionSizeUsd = (ETH_COLLATERAL * ETH_PRICE_USD * 2.5e30) / 1e18;
        bytes32 closeOrderKey = _createDecreaseOrder(positionSizeUsd);
        _executeOrder(closeOrderKey);
        console.log("Position closed. Decrease order key:", vm.toString(closeOrderKey), "\n");

        // === Step 3: Verify Results ===
        assertEq(getAccountPositionCount(user), 0, "Position count should be 0 after closing");

        uint256 finalWethBalance = weth.balanceOf(user);
        uint256 wethReceived = finalWethBalance - initialWethBalance;

        console.log("After closing position:");
        console.log("- Initial WETH balance:", initialWethBalance);
        console.log("- Final WETH balance:", finalWethBalance);
        console.log("- WETH received:", wethReceived);

        assertGt(wethReceived, 0, "Should receive WETH back (collateral returned)");
    }

    // ============================================================================
    // Internal Helpers
    // ============================================================================

    /// Create an increase order (long or short)
    /// @param collateralAmount Amount of collateral in ETH
    /// @param isLong true for long, false for short
    /// @return orderKey The order key
    function _createOrder(uint256 collateralAmount, bool isLong) internal returns (bytes32 orderKey) {
        uint256 executionFee = getExecutionFee();
        uint256 leverage = 2.5e30;
        uint256 positionSizeUsd = (collateralAmount * ETH_PRICE_USD * leverage) / 1e18;

        IExchangeRouter.CreateOrderParams memory orderParams = createIncreaseOrderParams({
            market: GmxArbitrumAddresses.ETH_USD_MARKET,
            collateralToken: address(weth),
            collateralAmount: collateralAmount,
            sizeDeltaUsd: positionSizeUsd,
            isLong: isLong
        });

        orderParams.numbers.executionFee = executionFee;
        orderParams.numbers.initialCollateralDeltaAmount = collateralAmount + executionFee;
        orderParams.addresses.receiver = user;
        orderParams.addresses.cancellationReceiver = user;
        orderParams.numbers.acceptablePrice = type(uint256).max;
        orderParams.numbers.callbackGasLimit = 200000;
        orderParams.numbers.minOutputAmount = 1;
        orderParams.autoCancel = true;

        vm.startPrank(user);
        uint256 totalEthNeeded = orderParams.numbers.initialCollateralDeltaAmount;
        exchangeRouter.sendWnt{ value: totalEthNeeded }(GmxArbitrumAddresses.ORDER_VAULT, totalEthNeeded);
        orderKey = exchangeRouter.createOrder{ value: 0 }(orderParams);
        vm.stopPrank();
    }

    /// Execute an order as keeper
    /// @param orderKey The order key to execute
    /// @return positionKey The resulting position key
    function _executeOrder(bytes32 orderKey) internal returns (bytes32 positionKey) {
        setupMockOracleProvider(ETH_PRICE_USD, USDC_PRICE_USD);

        vm.startPrank(keeper);
        OracleUtils.SetPricesParams memory oracleParams;
        oracleParams.tokens = new address[](2);
        oracleParams.tokens[0] = address(weth);
        oracleParams.tokens[1] = address(usdc);
        oracleParams.providers = new address[](2);
        oracleParams.providers[0] = GmxArbitrumAddresses.CHAINLINK_DATA_STREAM_PROVIDER;
        oracleParams.providers[1] = GmxArbitrumAddresses.CHAINLINK_DATA_STREAM_PROVIDER;
        oracleParams.data = new bytes[](2);
        orderHandler.executeOrder(orderKey, oracleParams);
        vm.stopPrank();

        return getPositionKey(user, GmxArbitrumAddresses.ETH_USD_MARKET, address(weth), true);
    }

    /// Create a decrease order to close a position
    /// @param positionSizeUsd The size in USD (30 decimals) to decrease
    /// @return orderKey The order key
    function _createDecreaseOrder(uint256 positionSizeUsd) internal returns (bytes32 orderKey) {
        IExchangeRouter.CreateOrderParams memory orderParams = createDecreaseOrderParams({
            market: GmxArbitrumAddresses.ETH_USD_MARKET,
            collateralToken: address(weth),
            sizeDeltaUsd: positionSizeUsd,
            isLong: true
        });

        orderParams.numbers.acceptablePrice = 0; // 0 = market price
        orderParams.addresses.receiver = user; // Collateral returned to user
        uint256 executionFee = getExecutionFee();
        orderParams.numbers.executionFee = executionFee;

        vm.startPrank(user);
        exchangeRouter.sendWnt{ value: executionFee }(GmxArbitrumAddresses.ORDER_VAULT, executionFee);
        orderKey = exchangeRouter.createOrder{ value: 0 }(orderParams);
        vm.stopPrank();
    }

    // ============================================================================
    // Tenderly Admin RPC Helpers
    // ============================================================================

    /// Set balance using Tenderly admin RPC (via FFI)
    /// @dev Calls: curl $TENDERLY_RPC_URL -X POST -d '{"method":"tenderly_setBalance","params":[...]}'
    function _tenderlySetBalance(address account, uint256 balance) internal {
        string memory rpcUrl = vm.envString("TENDERLY_RPC_URL");

        // Build JSON-RPC request
        string memory balanceHex = vm.toString(balance);

        // Use FFI to call curl
        string[] memory inputs = new string[](9);
        inputs[0] = "curl";
        inputs[1] = "-s";
        inputs[2] = "-X";
        inputs[3] = "POST";
        inputs[4] = rpcUrl;
        inputs[5] = "-H";
        inputs[6] = "Content-Type: application/json";
        inputs[7] = "-d";
        inputs[8] = string(
            abi.encodePacked(
                '{"jsonrpc":"2.0","method":"tenderly_setBalance","params":["',
                vm.toString(account),
                '","',
                balanceHex,
                '"],"id":1}'
            )
        );

        bytes memory result = vm.ffi(inputs);

        // Check if successful (result should contain "result")
        if (result.length == 0) {
            revert("tenderly_setBalance failed");
        }
    }
}
