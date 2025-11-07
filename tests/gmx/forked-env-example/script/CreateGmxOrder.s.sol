// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.0;

import "forge-std/Script.sol";
import "forge-std/console.sol";
import { IERC20 } from "forge-std/interfaces/IERC20.sol";

import "../contracts/interfaces/IGmxV2.sol";
import "../contracts/constants/GmxArbitrumAddresses.sol";

/**
 * Create GMX Order on Tenderly
 *
 * Usage:
 *   forge script script/CreateGmxOrder.s.sol --rpc-url $TENDERLY_RPC_URL --broadcast -vv
 *
 * Broadcasts 2 transactions to Tenderly:
 *   1. ExchangeRouter.sendWnt() (as user)
 *   2. ExchangeRouter.createOrder() (as user)
 *
 * Order execution must be done separately via bash script using cast send --unlocked --from <keeper>
 */
contract CreateGmxOrder is Script {
    // GMX contracts
    IExchangeRouter exchangeRouter;

    // Test parameters (must match bash script oracle prices!)
    uint256 constant ETH_PRICE_USD = 3344; // Matches bash script: 3343923406460000 / 1e12 = 3343.92
    uint256 constant USDC_PRICE_USD = 1;
    uint256 constant COLLATERAL_AMOUNT = 0.01 ether; // 0.01 ETH collateral
    uint256 constant EXECUTION_FEE = 0.0003 ether; // Execution fee
    uint256 constant LEVERAGE = 2.5e30; // 2.5x leverage

    function run() external {
        console.log("\n=== Creating GMX Order on Tenderly ===\n");

        // Load exchange router
        exchangeRouter = IExchangeRouter(GmxArbitrumAddresses.EXCHANGE_ROUTER);

        // Get user
        uint256 privateKey = vm.envUint("PRIVATE_KEY");
        address user = vm.addr(privateKey);

        console.log("User:", user);
        console.log("User balance:", user.balance / 1e18, "ETH");

        // Check if user has enough ETH for transaction
        uint256 requiredEth = COLLATERAL_AMOUNT + EXECUTION_FEE + 0.001 ether; // collateral + fee + gas buffer
        if (user.balance < requiredEth) {
            console.log("\n[ERROR] Insufficient balance!");
            console.log("Required:", requiredEth / 1e18, "ETH");
            console.log("Available:", user.balance / 1e18, "ETH");
            console.log("\nPlease fund this account on Tenderly:");
            console.log("  1. Go to Tenderly Dashboard");
            console.log("  2. Navigate to your Virtual Testnet");
            console.log("  3. Use 'Fund Account' to add ETH to:", user);
            console.log("  4. Add at least", requiredEth / 1e18, "ETH\n");
            revert("Insufficient ETH balance - please fund account on Tenderly first");
        }

        // Calculate position size
        uint256 positionSizeUsd = (COLLATERAL_AMOUNT * ETH_PRICE_USD * LEVERAGE) / 1e18;

        console.log("\nOrder details:");
        console.log("- Collateral:", COLLATERAL_AMOUNT / 1e18, "ETH");
        console.log("- Execution fee:", EXECUTION_FEE / 1e18, "ETH");
        console.log("- Position size:", positionSizeUsd / 1e30, "USD");
        console.log("- Leverage: 2.5x");
        console.log("- Direction: LONG\n");

        // === Create Order (BROADCASTS TO TENDERLY) ===
        bytes32 orderKey = createOrder(privateKey, user, positionSizeUsd);
        console.log("Order created. Order key:", vm.toString(orderKey));

        console.log("\n=== SUCCESS ===");
        console.log("Order created successfully on Tenderly!");
        console.log("\nBroadcasted transactions:");
        console.log("  1. ExchangeRouter.sendWnt()");
        console.log("  2. ExchangeRouter.createOrder()");
        console.log("\nOrder execution must be done separately via bash script with --unlocked keeper.");
    }

    /// Create order (broadcasts to Tenderly)
    function createOrder(uint256 privateKey, address user, uint256 positionSizeUsd)
        internal
        returns (bytes32 orderKey)
    {
        // Build order params
        address[] memory emptyPath = new address[](0);
        bytes32[] memory emptyDataList = new bytes32[](0);

        IExchangeRouter.CreateOrderParams memory orderParams = IExchangeRouter.CreateOrderParams({
            addresses: IExchangeRouter.CreateOrderParamsAddresses({
                receiver: user,
                cancellationReceiver: user,
                callbackContract: address(0),
                uiFeeReceiver: address(0),
                market: GmxArbitrumAddresses.ETH_USD_MARKET,
                initialCollateralToken: GmxArbitrumAddresses.WETH,
                swapPath: emptyPath
            }),
            numbers: IExchangeRouter.CreateOrderParamsNumbers({
                sizeDeltaUsd: positionSizeUsd,
                initialCollateralDeltaAmount: COLLATERAL_AMOUNT + EXECUTION_FEE,
                triggerPrice: 0,
                acceptablePrice: type(uint256).max,
                executionFee: EXECUTION_FEE,
                callbackGasLimit: 200000,
                minOutputAmount: 1,
                validFromTime: 0
            }),
            orderType: IExchangeRouter.OrderType.MarketIncrease,
            decreasePositionSwapType: IExchangeRouter.DecreasePositionSwapType.NoSwap,
            isLong: true,
            shouldUnwrapNativeToken: false,
            autoCancel: true,
            referralCode: bytes32(0),
            dataList: emptyDataList
        });

        // Start broadcasting
        vm.startBroadcast(privateKey);

        // Transaction 1: Send WETH to OrderVault
        console.log("  Sending WETH to OrderVault...");
        uint256 totalEth = COLLATERAL_AMOUNT + EXECUTION_FEE;
        exchangeRouter.sendWnt{ value: totalEth }(GmxArbitrumAddresses.ORDER_VAULT, totalEth);
        console.log("  [OK] WETH sent (transaction #1)");

        // Transaction 2: Create order
        console.log("  Creating order...");
        orderKey = exchangeRouter.createOrder{ value: 0 }(orderParams);
        console.log("  [OK] Order created (transaction #2)");

        vm.stopBroadcast();
    }
}
