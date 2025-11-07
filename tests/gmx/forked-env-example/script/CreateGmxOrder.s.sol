// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.0;

import "forge-std/Script.sol";
import "forge-std/console.sol";
import {IERC20} from "forge-std/interfaces/IERC20.sol";

import "../contracts/interfaces/IGmxV2.sol";
import "../contracts/constants/GmxArbitrumAddresses.sol";
import "../contracts/mock/MockOracleProvider.sol";

/**
 * Create and Execute GMX Order on Tenderly
 *
 * Usage:
 *   forge script script/CreateGmxOrder.s.sol --rpc-url $TENDERLY_RPC_URL --broadcast -vv
 *
 * Broadcasts 2 transactions to Tenderly:
 *   1. ExchangeRouter.sendWnt() (as user)
 *   2. ExchangeRouter.createOrder() (as user)
 *
 * Order execution runs locally (requires keeper role & mock oracle setup via vm.etch)
 */
contract CreateGmxOrder is Script {
    // GMX contracts
    IExchangeRouter exchangeRouter;
    IOrderHandler orderHandler;
    IReader reader;
    IDataStore dataStore;
    IRoleStore roleStore;

    // Tokens
    IERC20 weth;
    IERC20 usdc;

    // Test parameters (match test file)
    uint256 constant ETH_PRICE_USD = 3292;
    uint256 constant USDC_PRICE_USD = 1;
    uint256 constant COLLATERAL_AMOUNT = 0.001 ether;
    uint256 constant EXECUTION_FEE = 0.0003 ether;  // Increased to cover GMX gas estimates
    uint256 constant LEVERAGE = 2.5e30;

    function run() external {
        console.log("\n=== Creating & Executing GMX Order on Tenderly ===\n");

        // Load contracts
        exchangeRouter = IExchangeRouter(GmxArbitrumAddresses.EXCHANGE_ROUTER);
        orderHandler = IOrderHandler(GmxArbitrumAddresses.ORDER_HANDLER);
        reader = IReader(GmxArbitrumAddresses.READER);
        dataStore = IDataStore(GmxArbitrumAddresses.DATA_STORE);
        roleStore = IRoleStore(GmxArbitrumAddresses.ROLE_STORE);

        weth = IERC20(GmxArbitrumAddresses.WETH);
        usdc = IERC20(GmxArbitrumAddresses.USDC);

        // Get user and keeper
        uint256 privateKey = vm.envUint("PRIVATE_KEY");
        address user = vm.addr(privateKey);
        address keeper = getActiveKeeper();

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

        console.log("Keeper:", keeper);

        // Calculate position size
        uint256 positionSizeUsd = (COLLATERAL_AMOUNT * ETH_PRICE_USD * LEVERAGE) / 1e18;

        console.log("\nOrder details:");
        console.log("- Collateral:", COLLATERAL_AMOUNT / 1e18, "ETH");
        console.log("- Execution fee:", EXECUTION_FEE / 1e18, "ETH");
        console.log("- Position size:", positionSizeUsd / 1e30, "USD");
        console.log("- Leverage: 2.5x");
        console.log("- Direction: LONG\n");

        // Record initial state
        uint256 initialPositionCount = getAccountPositionCount(user);
        console.log("Initial position count:", initialPositionCount);

        // === STEP 1: Create Order (BROADCASTS TO TENDERLY) ===
        console.log("\n=== STEP 1: Creating Order (Broadcasting to Tenderly) ===");
        bytes32 orderKey = createOrder(privateKey, user, positionSizeUsd);
        console.log("Order created. Order key:", vm.toString(orderKey));

        // === STEP 2: Execute Order (LOCAL) ===
        console.log("\n=== STEP 2: Executing Order (Local) ===");
        bytes32 positionKey = executeOrder(orderKey, keeper);
        console.log("Order executed. Position key:", vm.toString(positionKey));

        // === STEP 3: Verify Position ===
        console.log("\n=== STEP 3: Verifying Position ===");
        uint256 finalPositionCount = getAccountPositionCount(user);
        console.log("Final position count:", finalPositionCount);

        require(finalPositionCount == initialPositionCount + 1, "Position not created");
        console.log("[OK] Position verified!");

        console.log("\n=== SUCCESS ===");
        console.log("Order created AND executed successfully!");
        console.log("\nCheck Tenderly dashboard for 2 transactions:");
        console.log("  1. ExchangeRouter.sendWnt()");
        console.log("  2. ExchangeRouter.createOrder()");
        console.log("\nNote: Order execution runs locally (requires keeper role & mock oracle)");
        console.log("Position key:", vm.toString(positionKey));
    }

    /// Create order (broadcasts to Tenderly)
    function createOrder(uint256 privateKey, address user, uint256 positionSizeUsd) internal returns (bytes32 orderKey) {
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
        exchangeRouter.sendWnt{value: totalEth}(GmxArbitrumAddresses.ORDER_VAULT, totalEth);
        console.log("  [OK] WETH sent (transaction #1)");

        // Transaction 2: Create order
        console.log("  Creating order...");
        orderKey = exchangeRouter.createOrder{value: 0}(orderParams);
        console.log("  [OK] Order created (transaction #2)");

        vm.stopBroadcast();
    }

    /// Execute order as keeper (local execution only)
    /// Note: Cannot broadcast this because it requires keeper private key and mock oracle setup
    function executeOrder(bytes32 orderKey, address keeper) internal returns (bytes32 positionKey) {
        // Setup mock oracle (local only - uses vm.etch)
        console.log("  Setting up mock oracle...");
        setupMockOracleProvider(ETH_PRICE_USD, USDC_PRICE_USD);
        console.log("  [OK] Mock oracle deployed");

        // Build oracle params
        OracleUtils.SetPricesParams memory oracleParams;
        oracleParams.tokens = new address[](2);
        oracleParams.tokens[0] = address(weth);
        oracleParams.tokens[1] = address(usdc);
        oracleParams.providers = new address[](2);
        oracleParams.providers[0] = GmxArbitrumAddresses.CHAINLINK_DATA_STREAM_PROVIDER;
        oracleParams.providers[1] = GmxArbitrumAddresses.CHAINLINK_DATA_STREAM_PROVIDER;
        oracleParams.data = new bytes[](2);

        // Execute as keeper (local simulation only)
        console.log("  Executing order as keeper (local simulation)...");
        vm.prank(keeper);
        orderHandler.executeOrder(orderKey, oracleParams);
        console.log("  [OK] Order executed locally");

        // Return position key
        positionKey = getPositionKey(
            vm.addr(vm.envUint("PRIVATE_KEY")),
            GmxArbitrumAddresses.ETH_USD_MARKET,
            address(weth),
            true
        );
    }

    /// Setup mock oracle provider (copied from GmxForkHelpers)
    function setupMockOracleProvider(uint256 wethPrice, uint256 usdcPrice) internal {
        // GMX price format: price * 10^30 / 10^tokenDecimals
        uint256 wethPriceFormatted = wethPrice * 1e12; // 18 decimals
        uint256 usdcPriceFormatted = usdcPrice * 1e24; // 6 decimals

        address providerAddress = GmxArbitrumAddresses.CHAINLINK_DATA_STREAM_PROVIDER;

        // Deploy mock implementation
        MockOracleProvider mockImpl = new MockOracleProvider();

        // Replace bytecode at provider address
        vm.etch(providerAddress, address(mockImpl).code);

        // Set prices
        MockOracleProvider(providerAddress).setPrice(
            GmxArbitrumAddresses.WETH,
            wethPriceFormatted,
            wethPriceFormatted
        );

        MockOracleProvider(providerAddress).setPrice(
            GmxArbitrumAddresses.USDC,
            usdcPriceFormatted,
            usdcPriceFormatted
        );

        console.log("    Oracle provider:", providerAddress);
        console.log("    WETH price:", wethPriceFormatted);
        console.log("    USDC price:", usdcPriceFormatted);
    }

    /// Get active keeper (copied from GmxForkHelpers)
    function getActiveKeeper() internal view returns (address keeper) {
        uint256 keeperCount = roleStore.getRoleMemberCount(Keys.ORDER_KEEPER);
        require(keeperCount > 0, "No ORDER_KEEPERs found");

        address[] memory keepers = roleStore.getRoleMembers(Keys.ORDER_KEEPER, 0, 1);
        keeper = keepers[0];
    }

    /// Get position key (copied from GmxForkHelpers)
    function getPositionKey(
        address account,
        address market,
        address collateralToken,
        bool isLong
    ) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked(account, market, collateralToken, isLong));
    }

    /// Get account position count (copied from GmxForkHelpers)
    function getAccountPositionCount(address account) internal view returns (uint256) {
        bytes32 ACCOUNT_POSITION_LIST = keccak256(abi.encode("ACCOUNT_POSITION_LIST"));
        bytes32 accountPositionListKey = keccak256(abi.encode(ACCOUNT_POSITION_LIST, account));
        return dataStore.getBytes32Count(accountPositionListKey);
    }
}
