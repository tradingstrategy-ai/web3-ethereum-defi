# Testing for `GMX`

Testing trading on GMX is a bit more complex much like testing all perpetual DEXes. Because most of them uses off-chain keepers to execute order for various security reasons. Orders on `GMX` executes in the following way:
1. User submits an order(Increase/Decrease/Swap).
2. Protocol does the initial validation.
3. Off-chain Keepers pick up the translation & after validating it executes the `OrderHandler.executeOrder` & completes the flow.

A mouthful right. So testing on mainnet fork is a bit tricky. So the tests are structured in the following way:

### Directory Structure

```shell
tests/gmx
├── debug_deploy.py
├── debug_tenderly.py
├── debug.py
├── fork_helpers.py
├── forked-env-example/
[...] 
# other test files
```

The `forked-env-example` folder contains the official tests `GMX` provided to test orders on mainnet fork. Where we have added a shell script `create_and_execute_order.sh` which will execute the flow on tenderly. Why do we need this? Because foundry tests don't broadcast transaction by default so if you just pass the RPC url then you won't be able to see any transactions in the dashboard. So we need to explicitly broadcast a transaction while running a `forge script`. For this a debug script is added called `CreateGmxOrder.s.sol` to broadcast the transaction.


### How the fork tests work?

Deploy Mock oracle contract to bypass the `shouldAdjustTimestamp` & `isChainlinkOnChainProvider` checks & fix the price of the collaterals to bypass the price validations. That is what the `MockOracleProvider` contract is doing. 


#### Catch

Well obviously there is a catch. `forge test` don't mine blocks for ease of testing but `anvil` is totally different. So replicating the tests in python with our `eth_defi` is not that simple. So as new blocks are mined the prices differ & comes the 100 price validation checks to make our life difficult. But all hope is not lost. That is why we have created a `debug.py` file which runs on tenderly, mainnet fork & on given RPC url. 


#### The `debug.py`

#### Usage

```shell
# activate the virtual environment
poetry shell

# Set the RPC
export ARBITRUM_CHAIN_JSON_RPC="https://arb-mainnet.g.alchemy.com/v2/<YOUR_API_KEY>"

# Run the script on fork env
python tests/gmx/debug.py --fork
```

Same thing as the foundry tests. GMX uses chainlink oracle providers so the script first funds the wallets the sets the bytecode for the Oracle with the bytecode of the `MockOracleProvider` so that we can set custom prices & bypass checks mentioned above.

Then opens a long position in the `ETH/USDC` market with `USDC` as collateral. That's not it. Then it unlocks the keeper address & using the `order key` submits the most vital part of the order execution flow, executes  `OrderHandler.executeOrder` & that's it. Also, the script dynamically fetch latest `ETH` price from the chainlink provider(the ones GMX is using) so that the MM accepts the orders.


### Troubleshooting

Even after running the `debug.py` the workflow is not reverting but getting no positions opened? Best way to debug would be to use [tenderly](https://tenderly.co/). Create a RPC for `arbitrum one` mainnet & run the following commands:

```shell
# activate the virtual environment
poetry shell

# Set the RPC
export TD_ARB="https://virtual.arbitrum.eu.rpc.tenderly.co/<Not_giving_my_key>"

# Run the debug.py script

python tests/gmx/debug.py --td
```

Navigate to the `JSON-RPC Calls` section & search for latest `executeOrder` function call. Look at the revert reason & debug further.

### Common Issues

```js
revert Errors.OrderNotFulfillableAtAcceptablePrice(executionPrice, acceptablePrice);
```

Most probably the prices for the market token or the collateral tokens are at fault. Say you are setting ETH price as `3384` but the current price for that block is `3800`. So the `executionPrice` greater than the `acceptablePrice` so this revert will hit. 



