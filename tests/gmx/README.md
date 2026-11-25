# Testing GMX orders

A GMX order has two transactions. The trader submits the order, then a keeper executes it. A successful submission receipt alone does not show that a position opened. The [fork fixtures](conftest.py) provide the trader and market state. The [keeper helper](../../eth_defi/gmx/testing/keeper.py) executes a pending order using the key from its submission receipt.

## Running a test

Load the test environment and select a module or test case. For example, the error decoder tests run without a fork.

```shell
source .local-test.env && timeout 180s poetry run pytest tests/gmx/test_errors_decoder.py
```

For fork selection, cached blocks and test isolation, see the [Anvil testing guide](../../eth_defi/testing/README.md).

## Inspecting an order on a fork

The [debug script](debug.py) opens an ETH long with USDC collateral and runs the keeper step. All modes require a test wallet key in `PRIVATE_KEY`. For a fresh Anvil fork, the script reads `ARBITRUM_CHAIN_JSON_RPC`. For a Tenderly fork, it reads `TD_ARB`. Load the private environment containing those settings before running the relevant command.

```shell
poetry run python tests/gmx/debug.py --fork
poetry run python tests/gmx/debug.py --td
```

To inspect a failed execution in Tenderly, find the `OrderHandler.executeOrder` call and read its revert reason. For `OrderNotFulfillableAtAcceptablePrice`, compare the execution price with the acceptable price in the submitted order. The [Foundry example](forked-env-example/script/CreateGmxOrder.s.sol) provides a Solidity version of the order flow. See the [fork example quickstart](forked-env-example/QUICKSTART.md) for setup.
