import { ethers } from "hardhat";
import { BigNumber } from "ethers";
import {
  loadGMXContracts,
  dealETH,
  getActiveKeeper,
  setupMockOracleProvider,
  createIncreaseOrderParams,
  createDecreaseOrderParams,
  createOracleParams,
  getOrderCount,
  getAccountOrderCount,
  getAccountPositionCount,
  getPositionCount,
  getPositionKey,
  getPositionSizeInUsd,
  getAccountPositionKeys,
  logBalances,
  GMX_ADDRESSES,
} from "./helpers";

/**
 * Test script demonstrating how to open and close a long ETH position on GMX V2
 *
 * This script:
 * 1. Forks Arbitrum at block 392496384 (using Anvil)
 * 2. Funds a test user with ETH
 * 3. Mocks the Chainlink oracle provider to return preset prices
 * 4. Creates a MarketIncrease order (2.5x leverage long ETH)
 * 5. Executes the order as a keeper
 * 6. Verifies the position was created/increased
 * 7. Creates a MarketDecrease order to close the position
 * 8. Executes the close order as a keeper
 * 9. Verifies the position was closed and collateral returned
 *
 * Prerequisites:
 * - Anvil must be running: `npm run anvil:start`
 * - Run with: `npm run test:open`
 */

async function main() {
  console.log("\n╔══════════════════════════════════════════════╗");
  console.log("║          Open Long Position (Anvil)          ║");
  console.log("╚══════════════════════════════════════════════╝\n");

  // ============================================================================
  // Setup
  // ============================================================================

  console.log("=== Fork Setup ===");
  const chainId = (await ethers.provider.getNetwork()).chainId;
  const blockNumber = await ethers.provider.getBlockNumber();
  console.log(`Chain ID: ${chainId}`);
  console.log(`Block number: ${blockNumber}`);

  const gmx = await loadGMXContracts();
  const [user] = await ethers.getSigners();
  console.log(`User address: ${user.address}`);
  const keeperAddress = await getActiveKeeper(gmx.roleStore);

  // Top-up user & keeper balances if low (preserve keeper's fork balance for realistic execution)
  const initialUserBalance = await ethers.provider.getBalance(user.address);
  if (initialUserBalance.lt(ethers.utils.parseEther("1"))) {
    await dealETH(user.address, initialUserBalance.add(ethers.utils.parseEther("100")));
  }

  const initialKeeperBalance = await ethers.provider.getBalance(keeperAddress);
  if (initialKeeperBalance.lt(ethers.utils.parseEther("1"))) {
    await dealETH(keeperAddress, initialKeeperBalance.add(ethers.utils.parseEther("100")));
  }

  // ============================================================================
  // Test Parameters - Match Mainnet Order
  // ============================================================================

  // Reference: Mainnet tx 0x68a77542fd9ba2bcd342099158dd17c0918cee70726ecd2e2446b0f16c46da50
  // Block 392496384, ETH price = $3,892 (historical price at fork block)
  const ETH_PRICE_USD = 3892;
  const USDC_PRICE_USD = 1;
  const ETH_COLLATERAL = ethers.utils.parseEther("0.001"); // 0.001 ETH collateral
  const LEVERAGE = 2.5;

  console.log("\n=== Order Parameters ===");
  const ethCollateral = Number(ethers.utils.formatEther(ETH_COLLATERAL));
  console.log(`Collateral: ${ethCollateral} ETH (~$${ethCollateral * ETH_PRICE_USD} at $${ETH_PRICE_USD}/ETH)`);
  console.log(`Leverage: ${LEVERAGE}x`);
  console.log(`Size: ~$${ethCollateral * ETH_PRICE_USD * LEVERAGE}`);
  console.log(`Direction: LONG\n`);

  // ============================================================================
  // Step 1: Record Initial State
  // ============================================================================

  const initialOrderCount = await getOrderCount(gmx.dataStore);
  const initialUserOrderCount = await getAccountOrderCount(gmx.dataStore, user.address);
  const initialUserPositionCount = await getAccountPositionCount(gmx.dataStore, user.address);
  const initialPositionCount = await getPositionCount(gmx.dataStore);

  // Calculate position key for this specific position
  const positionKey = getPositionKey(user.address, GMX_ADDRESSES.ETH_USD_MARKET, GMX_ADDRESSES.WETH, true);

  console.log("=== Initial State ===");
  console.log(`Global order count: ${initialOrderCount}`);
  console.log(`User order count: ${initialUserOrderCount}`);
  console.log(`User position count: ${initialUserPositionCount}`);
  console.log(`Global position count: ${initialPositionCount}`);
  console.log(`Position key: ${positionKey}`);

  // ============================================================================
  // Step 2: Setup Mock Oracle
  // ============================================================================

  await setupMockOracleProvider(ETH_PRICE_USD, USDC_PRICE_USD);

  // ============================================================================
  // Step 3: Create Order
  // ============================================================================

  await logBalances("Initial Balances", [
    { name: "User", address: user.address },
    { name: "Keeper", address: keeperAddress },
  ]);

  console.log("\n=== Creating Order ===");

  // Calculate position size in USD (30 decimals)
  // GMX uses 30 decimals for USD values
  // positionSizeUsd = collateral (18 decimals) * price * leverage (30 decimals) / 1e18
  // Example: 0.001 ETH * $3892 * 2.5x = $9.73 → 9.73e30 in GMX format
  const leverageWith30Decimals = ethers.utils.parseUnits(LEVERAGE.toString(), 30);
  const positionSizeUsd = ETH_COLLATERAL.mul(BigNumber.from(ETH_PRICE_USD))
    .mul(leverageWith30Decimals)
    .div(BigNumber.from(10).pow(18));

  const orderParams = createIncreaseOrderParams({
    market: GMX_ADDRESSES.ETH_USD_MARKET,
    collateralToken: GMX_ADDRESSES.WETH,
    collateralAmount: ETH_COLLATERAL,
    sizeDeltaUsd: positionSizeUsd,
    isLong: true,
    receiver: user.address,
  });

  // Send WETH + execution fee to OrderVault
  const totalEthNeeded = orderParams.numbers.initialCollateralDeltaAmount;
  await gmx.exchangeRouter.connect(user).sendWnt(GMX_ADDRESSES.ORDER_VAULT, totalEthNeeded, {
    value: totalEthNeeded,
  });

  // Get order key from callStatic (simulates the call and returns the value)
  const orderKey = await gmx.exchangeRouter.connect(user).callStatic.createOrder(orderParams, {
    value: 0,
  });

  // Create order with value 0 (tokens already sent via sendWnt)
  const createOrderTx = await gmx.exchangeRouter.connect(user).createOrder(orderParams, {
    value: 0,
  });
  await createOrderTx.wait();

  console.log(`Create order key: ${orderKey}`);

  // Verify order was created
  const afterCreateOrderCount = await getOrderCount(gmx.dataStore);
  const afterCreateUserOrderCount = await getAccountOrderCount(gmx.dataStore, user.address);

  console.log(`Global order count: ${afterCreateOrderCount} (expected: ${initialOrderCount.add(1)})`);
  console.log(`User order count: ${afterCreateUserOrderCount} (expected: ${initialUserOrderCount.add(1)})`);

  if (!afterCreateOrderCount.eq(initialOrderCount.add(1))) {
    throw new Error("Order count did not increase by 1!");
  }
  if (!afterCreateUserOrderCount.eq(initialUserOrderCount.add(1))) {
    throw new Error("User order count did not increase by 1!");
  }

  const positionSizeBefore = await getPositionSizeInUsd(gmx.dataStore, positionKey);

  // ============================================================================
  // Step 4: Execute Order (as Keeper)
  // ============================================================================

  console.log("\n=== Executing Order (as Keeper) ===");

  // Impersonate keeper using Anvil RPC
  // We need to use a direct JsonRpcProvider to bypass Hardhat's account checks
  const anvilProvider = new ethers.providers.JsonRpcProvider("http://127.0.0.1:8545");
  await anvilProvider.send("anvil_impersonateAccount", [keeperAddress]);

  // Get an unchecked signer from the direct provider
  const keeperSigner = anvilProvider.getUncheckedSigner(keeperAddress);

  // Connect the order handler to the keeper signer
  const keeperOrderHandler = gmx.orderHandler.connect(keeperSigner);

  // Create oracle params
  const oracleParams = createOracleParams();

  // Execute order
  const executeOrderTx = await keeperOrderHandler.executeOrder(orderKey, oracleParams);
  const executeOrderReceipt = await executeOrderTx.wait();

  // Debug: Log transaction details
  console.log(`Transaction hash: ${executeOrderReceipt.transactionHash}`);
  console.log(`Gas used: ${executeOrderReceipt.gasUsed.toString()}`);
  console.log(`Number of logs: ${executeOrderReceipt.logs.length}`);

  // Check for key events
  const positionIncreaseHash = ethers.utils.id("PositionIncrease");
  const hasPositionIncrease = executeOrderReceipt.logs.some(
    (log) => log.topics.length > 1 && log.topics[1] === positionIncreaseHash
  );

  console.log(`PositionIncrease event: ${hasPositionIncrease ? "YES" : "NO"}`);
  console.log("Order executed successfully!");

  // Stop impersonating keeper
  await anvilProvider.send("anvil_stopImpersonatingAccount", [keeperAddress]);

  // ============================================================================
  // Step 5: Verify Position Was Created/Increased
  // ============================================================================

  console.log("\n=== Position Verification ===");
  console.log(`Position key: ${positionKey}`);

  const positionSizeAfter = await getPositionSizeInUsd(gmx.dataStore, positionKey);
  const positionSizeIncrease = positionSizeAfter.sub(positionSizeBefore);
  if (positionSizeAfter.gt(positionSizeBefore)) {
    console.log(`Position size after order execution: ${ethers.utils.formatUnits(positionSizeAfter, 30)} USD`);
    console.log(`Position size increase: ${ethers.utils.formatUnits(positionSizeIncrease, 30)} USD`);
  } else {
    throw new Error("Position size did not increase after order execution!");
  }

  // ============================================================================
  // Step 6: Verify Final State
  // ============================================================================

  const finalOrderCount = await getOrderCount(gmx.dataStore);
  const finalUserOrderCount = await getAccountOrderCount(gmx.dataStore, user.address);
  const finalUserPositionCount = await getAccountPositionCount(gmx.dataStore, user.address);
  const finalPositionCount = await getPositionCount(gmx.dataStore);

  // Determine if position was created or increased based on position count changes
  // If position count didn't change, the position already existed and was increased
  const positionCountIncreased = finalUserPositionCount.gt(initialUserPositionCount);

  console.log("\n=== Final State ===");
  console.log(`Global order count: ${finalOrderCount} (expected: ${initialOrderCount})`);
  console.log(`User order count: ${finalUserOrderCount} (expected: ${initialUserOrderCount})`);
  console.log(`User position count: ${finalUserPositionCount} (initial: ${initialUserPositionCount})`);
  console.log(`Global position count: ${finalPositionCount} (initial: ${initialPositionCount})`);
  console.log(`Position was: ${positionCountIncreased ? "CREATED (new)" : "INCREASED (existing)"}`);

  // Check account-specific position list after execution
  const accountPositionKeys = await getAccountPositionKeys(gmx.dataStore, user.address);
  console.log(`All user's position keys (${accountPositionKeys.length} total):`);
  accountPositionKeys.forEach((key: string, index: number) => {
    const isOurPosition = key.toLowerCase() === positionKey.toLowerCase();
    console.log(`  [${index}]: ${key}${isOurPosition ? " <-- CURRENT POSITION" : ""}`);
  });

  // Verify state changes
  if (!finalOrderCount.eq(initialOrderCount)) {
    throw new Error("Global order count did not return to initial!");
  }
  if (!finalUserOrderCount.eq(initialUserOrderCount)) {
    throw new Error("User order count did not return to initial!");
  }

  // Positions count should either stay same (increase) or go up by 1 (create)
  const positionCountDiff = finalUserPositionCount.sub(initialUserPositionCount);
  if (!positionCountDiff.eq(0) && !positionCountDiff.eq(1)) {
    throw new Error(`Unexpected position count change: ${positionCountDiff.toString()}`);
  }

  await logBalances("Balances After Increase Order", [
    { name: "User", address: user.address },
    { name: "Keeper", address: keeperAddress },
  ]);

  // ============================================================================
  // CLOSE POSITION
  // ============================================================================

  console.log("\n\n╔══════════════════════════════════════════════╗");
  console.log("║         Close Long Position (Anvil)          ║");
  console.log("╚══════════════════════════════════════════════╝\n");

  // ============================================================================
  // Step 7: Close Position
  // ============================================================================

  console.log("=== Closing Position ===");

  // Record WETH balance before closing
  const weth = await ethers.getContractAt("IERC20", GMX_ADDRESSES.WETH);
  const wethBalanceBefore = await weth.balanceOf(user.address);

  // Create decrease order to close entire position
  const closeOrderParams = createDecreaseOrderParams({
    market: GMX_ADDRESSES.ETH_USD_MARKET,
    collateralToken: GMX_ADDRESSES.WETH,
    sizeDeltaUsd: positionSizeUsd, // close the FULL position to receive collateral back
    isLong: true,
    receiver: user.address,
  });

  // Send execution fee
  const executionFee = closeOrderParams.numbers.executionFee;
  await gmx.exchangeRouter.connect(user).sendWnt(GMX_ADDRESSES.ORDER_VAULT, executionFee, {
    value: executionFee,
  });

  // Get close order key from callStatic
  const closeOrderKey = await gmx.exchangeRouter.connect(user).callStatic.createOrder(closeOrderParams, {
    value: 0,
  });

  // Create decrease order
  const createCloseOrderTx = await gmx.exchangeRouter.connect(user).createOrder(closeOrderParams, {
    value: 0,
  });
  await createCloseOrderTx.wait();

  console.log(`Close order key: ${closeOrderKey}`);

  // ============================================================================
  // Step 8: Execute Close Order (as Keeper)
  // ============================================================================

  await setupMockOracleProvider(ETH_PRICE_USD, USDC_PRICE_USD);

  console.log("\n=== Executing Close Order (as Keeper) ===");

  await anvilProvider.send("anvil_impersonateAccount", [keeperAddress]);
  const keeperSignerForClose = anvilProvider.getUncheckedSigner(keeperAddress);
  const keeperOrderHandlerForClose = gmx.orderHandler.connect(keeperSignerForClose);

  const executeCloseTx = await keeperOrderHandlerForClose.executeOrder(closeOrderKey, oracleParams);
  const executeCloseReceipt = await executeCloseTx.wait();

  console.log(`Transaction hash: ${executeCloseReceipt.transactionHash}`);
  console.log(`Gas used: ${executeCloseReceipt.gasUsed.toString()}`);

  // Check for key events
  const positionDecreaseHash = ethers.utils.id("PositionDecrease");
  const orderExecutedHash = ethers.utils.id("OrderExecuted");

  const eventsFound = {
    PositionDecrease: executeCloseReceipt.logs.some(
      (log) => log.topics.length > 1 && log.topics[1] === positionDecreaseHash
    ),
    OrderExecuted: executeCloseReceipt.logs.some((log) => log.topics.length > 1 && log.topics[1] === orderExecutedHash),
  };

  console.log("\n=== Order Execution Events ===");
  console.log(`PositionDecrease: ${eventsFound.PositionDecrease ? "YES" : "NO"}`);
  console.log(`OrderExecuted: ${eventsFound.OrderExecuted ? "YES" : "NO"}`);

  await anvilProvider.send("anvil_stopImpersonatingAccount", [keeperAddress]);

  // ============================================================================
  // Step 9: Verify Position Closed
  // ============================================================================

  console.log("\n=== Close Verification ===");

  const finalPositionSize = await getPositionSizeInUsd(gmx.dataStore, positionKey);
  const positionSizeDecrease = positionSizeAfter.sub(finalPositionSize);

  if (finalPositionSize.eq(0)) {
    console.log(`Position fully closed!`);
    console.log(`Position size decrease: ${ethers.utils.formatUnits(positionSizeDecrease, 30)} USD`);
  } else if (finalPositionSize.lt(positionSizeAfter)) {
    console.log(`Position size after close: ${ethers.utils.formatUnits(finalPositionSize, 30)} USD`);
    console.log(`Position size decrease: ${ethers.utils.formatUnits(positionSizeDecrease, 30)} USD`);
  } else {
    throw new Error("Position size did not decrease after close order execution!");
  }

  await logBalances("Balances After Decrease Order", [
    { name: "User", address: user.address },
    { name: "Keeper", address: keeperAddress },
  ]);

  // Check WETH balance - should have received collateral back
  const wethBalanceAfter = await weth.balanceOf(user.address);
  const wethReceived = wethBalanceAfter.sub(wethBalanceBefore);

  console.log(`\n=== Collateral Returned ===`);
  console.log(`WETH received: ${ethers.utils.formatEther(wethReceived)} WETH`);

  if (wethReceived.eq(0)) {
    if (finalPositionSize.eq(0)) {
      throw new Error("No collateral received after close (WETH balance did not increase)!");
    } else {
      console.log("⚠️ Position partially closed --> no collateral received yet (position still open)!");
    }
  } else {
    console.log("Position fully closed!");
  }

  // ============================================================================
  // Final Summary
  // ============================================================================

  await logBalances("Final Balances", [
    { name: "User", address: user.address },
    { name: "Keeper", address: keeperAddress },
  ]);
  console.log(
    "User diff: ",
    ethers.utils.formatEther((await ethers.provider.getBalance(user.address)).sub(initialUserBalance)),
    "ETH"
  );
  console.log(
    "Keeper diff: ",
    ethers.utils.formatEther((await ethers.provider.getBalance(keeperAddress)).sub(initialKeeperBalance)),
    "ETH"
  );
}

// Execute the script
main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error("\n❌ Error:", error);
    process.exit(1);
  });
