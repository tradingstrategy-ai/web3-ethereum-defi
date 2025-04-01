# Current

- Add: Abstract ERC-4626 vault base class out from Lagoon implementation
- Add: Multicall historical reader
- Add: ERC-4626 automatic discovery onchain
- Add: ERC-4626 vault type classification
- Add: IPOR vault support
- Add: Morpho vault support

# 0.28.1

- Expose `TokenSnifferError.status_code` attribute so clients can handle sniffer errors

# 0.28

- Add: Google Cloud HSM hardware wallet support in `eth_defi.gcloud_hsm_wallet`
- Add Multicall3 support in `multicall_batcher` module
- Add `SwapRouter02` support on Base for doing Uniswap v3 swaps
- Add Uniswap V3 quoter for the valuation
- Add `buy_tokens()` helper to buy multiple tokens once, automatically look up best routes
- Fix: Base MEV protected broadcast failed
- Add: Integrate `TradingStrategyModuleV0` module to Gnosis Safe-based protocols using Zodiac module. Mainly needed for Lagoon vaults, but can work for others: vanilla Safe, DAOs.
- Change: Default to Anvil 0.3.0, Cancun EVM hardfork


# 0.27

- Add: Support for [Velvet Capital vaults](https://www.velvet.capital/)
- Add: Support for [Lagoon vaults](https://lagoon.finance/)
- Add: Support for Gnosis Safe [Lagoon vaults](https://safe.global/) via `safe-eth-py` library integration
- Add: Vault abstraction framework to easily work with different onchain vaults. Abstract away vault interactions to its own encapsulating interface.
- Add: `wait_and_broadcast_multiple_nodes_mev_blocker()` for [MEV Blocker](https://mevblocker.io) - because the tx
  broadcast must be sequential
- Add: `fetch_erc20_balances_multicall` and `fetch_erc20_balances_fallback` read multiple ERC-20 balances using Multicall library
- Add: `QuoterV2` support for Uniswap v3 - needed to get Base prices
- Change `launch_anvil()` to use latest hardfork by default instead of `london`
- Various smaller bug fixes and optimisations

# 0.26.1 

- Add: TokenSniffer API wrapper with a persistent cache
- Add: Enzyme vault deployments on Arbitrum
- Add: Custom cache interface support for `CachedTokenSniffer()`

# 0.26

- Add: dRPC `x-drpc-provider-id` header support for troubleshooting issues with decentralised node providers
- Fixed: Whitelist HTTP 403 Forbidden for dRPC as a retryable error
- Add: `wait_and_broadcast_multiple_nodes(inter_node_delay)` to fix Alchemy https://github.com/ethereum/go-ethereum/issues/26890
- Internal change: Move `deploy_guard()` to its own function and refactor Enzyme vault deployment to more manageable
- Dependencies: Numpy < 2.x for now as it breaks too much stuff, updating dependencies is a headache
- Add and fixed: Various logging and diagnostics lines 
- Fixed: [Uniswap Subgraphs now require an API key](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/215)

# 0.25.7

- Update Aave deploymenet list

# 0.25.6

- Add Aave v2 event reader support


# 0.25.5

- Handle HTTP 410 retryable, as returned by dRPC

# 0.25.4

- Make it possible to deploy in-house contracts without installing Enzyme toolchain:
  node.js, hardhat and node-gyp were un-co-operative. Instead, now we just flatten out Enzyme sol
  files and store them in the source tree as copies.
- Improved error messages for `GuardV0`
- Handle HTTP 520 retryable, as returned by Alchemy JSON-RPC 
- Handle `ValueError: {'code': -32000, 'message': 'execution aborted (timeout = 5s)'}` as returned by Alchemy RPC

# 0.25.3

- Improve graphql support check in `has_graphql_support()`

# 0.25.2

- Handle HTTP 525 retryable, as returned by Alchemy JSON-RPC 

# 0.25.1

- Add: `VaultPolicyConfiguration.shares_action_timelock` Have a safe redemption time lock on Enzyme vault deployments
- Add: [header not found](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/201) in retryable exceptions

# 0.25

- Bump web3.py to 6.12.x
- Add Foundry and Forge integration: `deploy_contract_with_forge()`
- Add initial Etherscan integration
- Add [Terms of Service acceptance manager integration](https://github.com/tradingstrategy-ai/terms-of-service)
- Add GuardV0 and SimpleVaultV0 implementations for creating safe automated asset managers
- Add support for Enzyme policies
- Added GuardV0 support for Enzyme vaults and generic adapters
- Add `get_native_token_price_with_chainlink()` to easily convert native token prices to USD\
- Add 1delta price estimation helper `OneDeltaPriceHelper`
- Add `fetch-all-vaults.py` export all Enzyme vaults from the chain to a CSV file
- Add `deploy_generic_adapter_vault` for correctly configured policy and safe vault deployment
- Add Enzyme vault deployment tutorial
- Improve logging in `wait_and_broadcast_multiple_nodes` for post-mortem analysis
- `hash(SignedTransactionWithNonce)` now is `SignedTransactionWithNonce.hash`, Ethereum transaction hash
- Improve various utility functions
- Fix issues cleaning AST information from Enzyme contracts on certain UNIX shells
- Fix log message in the fallback provider that if we have only a single 
  provider don't call error handling "switching"
- Fix Sphinx dependencies to be dev dependencies

# 0.24.6

- Fix: invalid None check for sign_bound_call_with_new_nonce"
- Fix: Python pinning to 3.12.x

# 0.24.5

- Fix: `HotWallet.sign_bound_call_with_new_nonce` tries to avoid calling broken Web3 gas estimation
  machine if the gas parameters are already given as the arguments
- Fix: Raise `OutOfGasFunds` in `_broadcast_multiple_nodes` and
  avoid transaction broadcast retry if we do not have gas money
- Fix: Don't swallow nonce errors and chain id errors in `broadcast_multiple_nodes`
- Fix type normalisation of `tx_hash` in `fetch_transaction_revert_reason`

# 0.24.4

- Figure out how to tackle Anvil unreliability issues
- Mark `evm_mine` JSON-RPC method not retryable
- Fix `anvil.mine()` without parameters do not attempt to guess next block timestamp, as this 
  was wrong under a load, probably due to Anvil's internal race conditions
- Add `is_retryable_http_exception(method, params)` to allow decide the retry of a JSON-RPC request based
  on its inputs, not just the output exception
- Add `eth_defi.timestamp.get_latest_block_timestamp()`
- Add `eth_defi.timestamp.get_block_timestamp()`

# 0.24.3

- Change 1delta `close_short_position()` API to be able to
  be able to specify the amount of collateral to withdraw

# 0.24.2

- Add `is_anvil(web3)` method
- Add `fetch_erc20_balances_by_token_list(decimalise=True)` to 
  get multiple token balances with decimal conversaion 
  as a batch operation
- Fix: `set_block_tip_latency()` defauts to 0 when
  connected to `create_multi_provider_web3` to simplify testing
- Remove LlamaNodes from Github CI configuration as was causing too much maintenance
  work and random failures

# 0.24.1

- Unpin some dependencies to make package installation easier

# 0.24

- Debian Bullseye and pyenv was picking up old web3-ethereum-defi version
- Create a Docker script to check installation on Debian Bullseye
- This did not then use the correct version of [safe-pysha3](https://github.com/5afe/pysha3), but picked up the old pysha3 package
- Make `pyproject.toml` to say we are compatible all they way to Python 3.12
- [pkgutil compatibility fixes](https://stackoverflow.com/questions/77364550/attributeerror-module-pkgutil-has-no-attribute-impimporter-did-you-mean).
- [Upgrade to Pandas 2.x](https://github.com/pandas-dev/pandas/issues/53665), needed for Python 3.12 compatibility
- Upgrade to the latest Web3.py 6.x version
- Python 3.12 changes `ast` module and this has breaking changes with `eth_trace` library. Workaround them.
- Disable `test_fallback_double_fault` because new Web3.py does not like `MagicMock` results
- Bump to `zope.dottedname` 6.0 needed [for Python 3.11 compatibility](https://pypi.org/project/zope.dottedname/)

# 0.23.2

- Fix installation error on Debian Bullseye and Python 3.11: `fatal error: pystrhex.h: No such file or directory`
- Bump compatibility all the way up to Python 3.12

# 0.23.1

- Feature: Add 1delta integration position handlers

# 0.23

- Various improvements when working with low quality JSON-RPC nodes
- Uniswap v3 price tutorial is now runnable with low quality nodes
- API chance: `fetch_erc20_details(cache)` has now an internal cache, implemented
  with Python's cachetools package.
- Add: `static_call_cache_middleware` to reduce the amount of `eth_chainId` API calls
- Add: `TrackedLazyTimestampReader` to help working with slow nodes
- Add: `MultiProviderWeb3.get_api_call_counts` to see JSON-RPC API call stats across all providers
- Fix: `swap_with_slippage_protection(max_slippage)` is BPS 
- API change: `swap_with_slippage_protection(max_slippage=15)` - change the default Uniswap v3
  trade slippage tolerance from (unrealistic) 0.1 BPS to 15 BPS.
- Fix: The madness of JSON-RPC providers abuse the error code `-32000`.
  We check for *error message* now instead of error code.
- Internal change: When reading events, only notify progress bar when we have an event hit,
  to avoid unnecessary `eth_getBlockByNumber` calls for timestamps.

# 0.22.30

- API change: Handle `wait_and_broadcast_multiple_nodes()` so that it will attempt 
  to retry multiple providers multiple times before raising the last exception

# 0.22.29

- Add `launch_anvil(fork_block_number)` option to create mainnet works on a specific block number.
  Naturally works only with archive nodes.
- API change: If all providers fail in `wait_and_broadcast_multiple_nodes()`,
  raise the exception from the last provider.

# 0.22.28

- More retryable JSON-RPC errors whitelisted. Now `ValueError: {'code': -32701, 'message': 'Please specify address in your request or, to remove restrictions, order a dedicated full node here: https://www.allnodes.com/bnb/host'}`.


# 0.22.27

- More retryable JSON-RPC errors whitelisted. Now `{'code': -32005, 'message': 'limit exceeded'}`.

# 0.22.26

- Add `eth_defi.confirmation.check_nonce_mismatch` to verify our signed transactions
  have good nonces based on on-chain data
- Add `wait_and_broadcast_multiple_nodes(check_nonce_validity)` and by default 
  try to figure nonce issues before attemping to broadcast transactions

# 0.22.25

- Internal change: Increased logging for transaction broadcast issues
- Internal change: more aggressive change reading nodes in multi-node tx broadcast

# 0.22.24

- Internal change: more verbose logging for `wait_and_broadcast_multiple_nodes`

# 0.22.23

- API change: add `fetch_erc20_balances_by_token_list(block_identifier)`

# 0.22.22

- Add: `wait_and_broadcast_multiple_nodes` to work around transaction broadcasts and confirmations failing on LlamaNodes
- Fix: First workaround for `JSON-RPC error: {'code': -32003, 'message': 'max priority fee per gas higher than max fee per gas'}` in `eth_defi.gas`

# 0.22.21

- Don't pin down `pyarrow` version to make it easier to use different Arrow
  reading backends

# 0.22.20 

- Add `eth_defi.provider.broken_provider.get_almost_latest_block_number()`
  for developer ergonomics when working with Ankr and LlamaNodes
- If using `FallbackProvider` switch node providers in `wait_transactions_to_complete`
  because Ankr and LlamaNodes low service quality issues

# 0.22.19

- Work around `web3.exceptions.BlockNotFound` with LlamaNodes.com

# 0.22.18

- Added `ChunkedEncodingError` to automatically retryable errors. 
  This error happens on LlamaNodes.com and is likely a quality of a service issue
  on their behalf.

# 0.22.17

- Make testing and `launch_anvil` distrubuted safe by randomising Anvil localhost port it binds.
  Test now run in few minutes instead of tens of minutes. Tests must be still run with
  `pytest --dist loadscope` as individual test modules are not parallel safe.
- Add ``eth_defi.broken_provider.set_block_tip_latency()`` to control the default delays 
  for which we expect the chain tip to stabilise.

# 0.22.16

- Work around ``BadFunctionCallOutput``: Insufficient bytes exception: A special case of eth_call returning an empty result.
  This happens if you call a smart contract for a block number
  for which the node does not yet have data or is still processing data.
  This happens often on low-quality RPC providers (Ankr)
  that route your call between different nodes between subsequent calls, and those nodes
  see a different state of EVM.
  Down the line, not in the middleware stack, this would lead to `BadFunctionCallOutput` output. We work around this by detecting this condition in the middleware stack and triggering the middleware fall-over node switch if the condition is detected.
- Set `FallbackProvider` to have the default `4` blocks latency for all `latest` calls,
  in `get_default_block_tip_latency()` so that fail over switches are more robust.

# 0.22.15
    
- Fix [FallbackProvider](https://web3-ethereum-defi.readthedocs.io/api/provider/_autosummary_provider/eth_defi.provider.fallback.html) to work with [certain problematic error codes](https://twitter.com/moo9000/status/1707672647264346205)
- Log non-retryable exceptions in fallback middleware, so 
  there is better diagnostics why fallback fails
- Add `HotWallet.fill_in_gas_estimations()`

# 0.22.14

- Add `{'code': -32043, 'message': 'Requested data is not available'}` to RPC exceptions where we assume it's
  an error we can either resume or switch to the next node provider. This error was encoureted with `eth_getLogs`
  when using LlamaNodes.

# 0.22.13

- Allow passing `request_kwargs` to [create_multi_provider_web3](https://web3-ethereum-defi.readthedocs.io/api/provider/_autosummary_provider/eth_defi.provider.multi_provider.create_multi_provider_web3.html#eth_defi.provider.multi_provider.create_multi_provider_web3)
- When setting up [TunedWeb3Factory](https://web3-ethereum-defi.readthedocs.io/api/event_reader/_autosummary_enzyne/eth_defi.event_reader.web3factory.TunedWeb3Factory.html?highlight=tunedweb3factory) use `create_multi_provider_web3` to set up the connections
  instead pooled threads and processed
- Switch to ujson for JSON-RPC decoding by default with `create_multi_provider_web3`
- Fix `test_block_reader` tests

# 0.22.12

- Retry [nonce too low errors](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/153),
  (related to LLamaNodes). 

# 0.22.11

- Add `eth_defi.provider.llamanodes` and work around issues with LlamaNodes.com

# 0.22.10

- Move Ankr specific functionality to its own `eth_defi.provider.ankr` module 
  that will see more changes in the future

# 0.22.9

- Add `eth_defi.rpc.broken_provider` for workaround for the quirks and features of different JSON-RPC providers 
- Ankr workaround for `BlockNotFound` exception. 

# 0.22.8

- Add: Aave v3 reserve data queries
- Add: More logging to `swap_with_slippage_tolerance` for Uniswap v3 to diagnose failed trades

# 0.22.7

- Fix: Decimal place adjustment when calculating Uniswap v3 fees

# 0.22.6

- Fix: Aave v3 event reader dealing with different
  block number formats from JSON-RPC nodes

# 0.22.5

- Add: Uniswap v3 LP fees are now accounted in the trade analysis
- Fix: Documentation now generates proper title and description HTML
  meta tags for automatically generated API documentation

# 0.22.4

- [JSON-RPC fallback and MEV protection tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/multi-rpc-configuration.html)
- Added missing `sigfig` lib dependency

# 0.22.3

- Fix: `eth_defi.chain.has_graphql_support` to support `MultiProviderWeb3`

# 0.22.2

- Add: `eth_defi.provider.multi_provider.create_multi_provider_web3`: An easy way to configure a Web3 instance with
  multiple providers

# 0.22.1

- Add logging to `swap_with_slippage_protection()` on Uniswap v3
  to trace slippage issues

# 0.22

- Refactor a lot of functionality to a new
  submodule [eth_defi.provider](https://web3-ethereum-defi.readthedocs.io/api/provider/index.html)
- Add MEV blocking support in the form of `eth_defi.mev_blocker.MEVBlockerProvider`
- Add JSON-RPC fallback switching in the form of `eth_defi.fallback_provider.FallbackProvider`
- Add `HotWallet.create_for_testing`
- Add utility function `get_onchain_price()` to ask on-chain price of a
  Uniswap v3 pool at any given block number
- Add `eth_defi.event_reader.logresult.decode_log` and better
  documentation for `LogResult` class
- Deprecate `eth_defi.anvil` -> `eth_defi.provider.anvil`
- Deprecate `eth_defi.ganache` -> `eth_defi.provider.ganache`

# 0.21.8

- Add test coverage for `extract_timestamps_json_rpc_lazy`
- Expose API call counter in `LazyTimestampContainer`

# 0.21.7

- Add `block_identifier` parameteter to `estimate_buy_received_amount() / estimate_sell_received_amount()`,
  so we can ask historical prices and also track the price information per block
- Fix `0x` hash prefix missing in `LazyTimestampContainer` - looks like live RPC nodes  
  where returning JSON-RPC responses differently formatted

# 0.21.6

- Add `HotWallet.sign_bound_call_with_new_nonce`

# 0.21.5

- Create `extract_timestamps_json_rpc_lazy` that instead of reading block timestamps upfront for the given range,
  only calls JSON-RPC API when requested. It works on the cases where sparse event data is read over long block range
  and it is likely only few timestamps need to be fetched in this range.

# 0.21.4

- Added `eth_defi.enzyme.erc_20` helpers

# 0.21.3

- Fix error message `fetch_transaction_revert_reason()` crashing.
  Also made the error message prettier and more helpful.

# 0.21.2

- Add `AssetDelta.__mul__` method

# 0.21.1

- Attempt to fix packaging to [workaround the new PyPi ZIP bomb check](https://github.com/pypi/warehouse/issues/13962).
  Enzyme ABI files no longer include AST data.
- Add `fetch_vault_balances()` state reading balance support for Enzyme vaults.

# 0.21

- Add EIP-3009 `transferWithAuthorization` support.
  Related refactoring of EIP-3009 module.

# 0.20.1

- Fix: Token equality: `TokenDetails` does a logical comparison with chain id and address,
  instaed of object comparison. This makes TokenDetails good for ifs and hash maps. This
  adds `TokenDetails.__eq__` and `TokenDetails.__hash__`.
- Fix `TradeSuccess.price` is in Python `Decimal`
- Add: `TradeSucces.get_human_price(reverse_token_order: bool)`

# 0.20

- Add USDC (Centre FiatToken)
- Add EIP-712
- Add EIP-3009
- Add `transferWithAuthorization` and `receivedWithAuthorization`
- Add Enzyme vault USDC payment forwarder allowing single click purchases (no `approve` step)
- Fix: Don't try to `trace_transaction` unless we know we are on Anvil
- Add Aave v3 loan support in `eth_defi.aave_v3.loan` module

# 0.19.2

- Add: Enzyme's FundValueCalculator contract as part of the deployment

# 0.19.1

- Fix: Excessive log output if `__repr__` on GraphQLReorganisationMonitor
- Fix: Aave deployer tests fail on Github

# 0.19

- Add [Aave v3 deployer support](https://github.com/aave/aave-v3-deploy) in`eth_defi.aave_v3.deployer` module
- Add Solidity library linking support for Hardhat-based deployments in `eth_defi.abi.get_linked_contract`
- Add: More logging and better error messages to some parts
- Add: `figure_reorganisation_and_new_blocks(max_range)` to catch situations you somehow feed a too long block range to
  scan
- Add: `analyse_trade_by_receipt(input_args)` to analyse the success of Uni v3 trades when trading on Enzyme

# 0.18.4

- Fix: Use `web3.eth.wait_for_transaction_receipt` in appropriate places
- Add: Helper functions to interact with `UniswapV3PriceHelper`

# 0.18.3

- Add: TQDM progress bar support for event reading in the form
  of `eth_defi.event_reader.progress_update.TQDMProgressUpdate`
- Add: Enzyme price feed removal support
- Add: `eth_defi.chain.fetch_block_timestamp` shortcut method
- Fix: Web3 6.0 compatibility
- Fix: Better error message when reorganisation monitor is missing blocks
- Fix: `EnzymePriceFeed.primitive_token` resolution fails on some Enzyme tokens on Polygon

# 0.18.2

- Add argument `Vault.fetch(generic_adapter_address)`

# 0.18.1

- Fix: Handle `HexBytes` event signatures for Web3 6.0
- API change: No longer allow `HexBytes` results to fall through in `LogResult` to make sure
  all event readers get the data in the same format

# 0.18

- Dependency version updates
- Fix: Various fixes to transaction receipt handling
- Fix: Report the revert reason why Uniswap v2 pair deployment fails
- Fix: `eth_defi.uniswap_v2.analysis.analyse_trade_by_receipt` supports complex compounded transactions
- Add: `eth_defi.deploy.get_registered_contract` for unit test contract diagnosis
- API change: `VaultControllerWallet.sign_transaction_with_new_nonce` has new API
- API change: Use bound `ContractFunction` in `EnzymeVaultTransaction`

# 0.17

- Reorganise ABI compilation process, source dependencies and `eth_defi.abi` folder layout
- In-house contracts are now compiled using [Foundry](https://book.getfoundry.sh/)
- Add `VaultSpecificGenericAdapter.sol` for Enzyme
- Add `eth_defi.enzyme.vault_controlled_vallet`
- Add `eth_defi.tx.AssetDelta`

# 0.16.1

- Add `Vault.fetch_denomination_token_usd_exchange_rate`

# 0.16

- Add initial Chainlink support
-
Add [eth_defi.reader.multithread.MultithreadEventReader for easy to use high-speed Solidity event reading](https://web3-ethereum-defi.readthedocs.io/tutorials/multithread-reader.html)
- Add Enzyme's price feeds
- Add Enzyme's `Vault.fetch`
- Add `eth_defi.utils.to_unix_timestamp`
- Add `eth_defi.reorganisation_monitor.create_reorganisation_monitor`
- Rename: `eth_defi.enzyme.events.Withdrawal` -> `Redemption`
- Optimize `get_contract` with improved caching
- Add preliminary `assert_call_success_with_explanation` - but looks like Anvil support is still missing,
  so currently hacked together

# 0.15.3

- Add `EnzymeDeployment.fetch_vault`
- Add `Vault.fetch_deployment_event`
- Add `BroadcastFailure` exception
- Fix token sorting condition in Uniswap v2 pair deployment
- Fix Anvil launch to do three attempts by default if the process fails to launch
- Web3.py 6.0 release API fixes

# 0.15.2

- Add API call count middleware
- Fix: Clean accidentally released breakpoint code in revert middleware

# 0.15.1

-
Added [a script for verifying the integrity of your EVM JSON-RPC node data](https://web3-ethereum-defi.readthedocs.io/tutorials/index.html)
- Added `TunedWeb3Factory(thread_local_cache)` option for more performant web3 connection when using thread pooling

# 0.15

- Migrate to Web3.py 6.0. Notable Web3.py API changes:
    - `toChecksumAddress` -> `to_checksum_address`
    - `processReceipt` -> `process_receipt`
    - `web3.contract.Contract` -> `web3.contract.contract.Contract`
    - `solidityKeccak` -> `solidity_keccak`
    - `decode_function_input` returns dict instead of tuple
- Support Anvil as the unit test backend ove `EthereumTester` - Anvil is much faster
- `deploy_contract()` tracks deployed contracts and their ABIs so we can print symbolic Solidity stack traces
- Print Solidity stack traces of failed transactions using `print_symbolic_trace()` and `trace_evm_transaction()`
- Adding initial Enzyme Protocol APIs
- Adding dHEDGE Protocol ABI files and compile commands
- Add `revert_reason_middleware`
- Documentation restructure

# 0.14.1

- Add Ethereum to `AAVE_V3_NETWORKS` configuration
- Fix price calculation in Uniswap v3 `analysis.py`

# 0.14

- Replace `ganache` with `anvil` as the mainnet fork solution. Anvil is much more stable
  than Ganache what comes to JSON-RPC. Anvil is much faster. You can now call
  [fork_network_anvil](https://web3-ethereum-defi.readthedocs.io/api/_autosummary/eth_defi.anvil.html#module-eth_defi.anvil)
  that is a drop-in replacement for old
  Ganache based `fork_network`.
- Move internal test suite to use Anvil instead of Ganache. This allows us to remove
  `flaky` decorators on tests.
- Add `analysis.py` for Uniswap V3 and relevant tests
- Add `mock_partial_deployment` function for V3
- Abstract `TradeResult`, `TradeSuccess`, and `TradeFailure` out of Uniswap V2 and into eth_defi.trade, since also used
  in Uniswap V3
- Add Uniswap V3 `increase_liquidity()` and `decrease_liquidity()` by @pbharrin

# 0.13.11

- Add Uniswap V3 decode_path method

# 0.13.10

- Uniswap v3 fixes

# 0.13.9

- Add middleware support for Avalanche C-chain

# 0.13.8

- Fix retry sleep not reset between function calls in `exception_retry_middleware`

# 0.13.7

- Fix `extract_timestamps_json_rpc` to be compatible with both middlewared and non-middlewared JSON-RPC
  request format (string hex numbers vs. converted ints).

# 0.13.6

- Attempt to overcome `ValueError: {'message': 'Internal JSON-RPC error.', 'code': -32603}` if served by a Pokt relay

# 0.13.5

- `has_graphql_support` made more robust

# 0.13.4

- Retry middleware fine tuning

# 0.13.3

- Off by one fix in read_events_concurrent max block range
- More event reader test coverage

# 0.13.2

- Better test and exception coverage if bad `extract_timestamps`
  is passed while reading events. This prevents the library
  user to write a bad timestamp provider function.

# 0.13.1

- Fix `filter` and `event` assert in `read_events_concurrent()`

# 0.13

- Fix: Duplicate events appearing when using the concurrent event reader
- Added `ReorganisationMonitor` and `ChainReorganisationDetected` to deal with unstable chain tips when
  doing event ignestion
- Added `uniswap-v2-pairs-swap-live.py` example that shows real-time
  swaps happening on QuickSwap (Polygon) in a terminal
- Add `has_graphql_support()` to detect GraphQL interface on GoEthereum
- Add `GraphQLReorganisationMonitor` for very fast downloading
  of block headers and timestamps using GoEthereum /graphql API

# 0.12

- Added `generate_fake_uniswap_v2_data()` to generate synthetic Uniswap v2 trade feeds
- Improved `PairDetails` API, added `get_current_mid_price()`
- Add `PairDetails.checksum_free_address` to shortcut getting lowercased Ethereum address
- Added `convert_jsonrpc_value_to_int()` to deal differences between real JSON-RPC and EthereumTester
- Add `install_chain_middleware()` and `install_retry_middleware()`
- Add `measure_block_time()`
- Add multiple contract address filtering to the event reader
- Add `fetch_deployment` for Uniswap v3
- Add `swap_with_slippage_protection` for Uniswap v3

# 0.11.3

- Add new PriceOracle types for unit testing

# 0.11.2

- Adding Trader Joe compatibility. Unlike other clones, Trader Joe uses `Router.WAVAX` instead `Roueter.WETH`
  for the native token variable.
- Document BNB Chain "Limits exceeded" error - BNB Chain eth_getLogs have been
  disabled on public endpoints

# 0.11.1

- Moving `nbsphinx` to optional dependency, was as core dependency by accident

# 0.11

- Feature: generic price oracle implementation with configurable price function
- Feature: time weighted average price (TWAP) price function for price oracle
- Feature: price oracle implementation for Uniswap v2 and v3 pools
- Feature: `update_live_price_feed` for real-time Uniswap v2 and v3 price oracles
- Feature: `fetch_pair_details` to get info on Uniswap v2 pairs
- API change: Refactored event filter implementation to `eth_defi.reader.filter`

# 0.10.0

- Fix: Python 3.9 or later required
- Feature: Added Uniswap V3 price helper (both single hop and multi hops)
- API change: Moved Uniswap V3 `add_liquidity` to its own function
- Fix: Correct slippage calculation to match official Uniswap v2 SDK
- Fix: Microsoft Windows compatibility: Always use utf-8 when reading and writing text files

# 0.9

-
Feature: [High speed Solidity events / eth_getLogs fetching and decoding](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/scripts/read-uniswap-v2-pairs-and-swaps-concurrent.py)
-
Feature: [JSON-RPC retry middleware with sleep and backoff](https://web3-ethereum-defi.readthedocs.io/_autosummary/eth_defi.middleware.http_retry_request_with_sleep_middleware.html#eth_defi.middleware.http_retry_request_with_sleep_middleware)
- Feature:
  Added [decode_signed_transaction](https://web3-ethereum-defi.readthedocs.io/_autosummary/eth_defi.tx.decode_signed_transaction.html#eth_defi.tx.decode_signed_transaction)
  with EIP-2718 and EIP-2930 tx support
- Feature: Added `estimate_buy_received_amount_raw` and `estimate_sell_received_amount_raw`
- Fix: pairFor could give a wrong address for trading pair
- Fix: Cosmetic API improvements and fixes, with more asserts
- API change: Split `analyse_trade` -> `analyse_trade_by_hash` and `analyse_trade_by_receipt`
- API change: Rename module `txmonitor` -> `confirmation`

# 0.8

- Update web3.py dependency to 5.28.0
- Feature: Added Uniswap v2 swap function with slippage protection
- Feature: Added support for `fee` and `slippage` to Uniswap v2 price calculations
- Feature: Added Uniswap v2 pair liquidity fetch
- Feature: Added support for three-way swap (swap through an intermediate token) and price calculations
- Feature: Added support
  for [transfer fee, token tax and honeypot checks](https://tradingstrategy.ai/docs/programming/token-tax.html)
- API change: Moved `get_amount_in` and `get_amount_out` to `UniswapV2FeeCalculator` class
- Fix: Improve exception message when transactions timeout
- Feature: [Added ERC-20 transfer tutorial](https://web3-ethereum-defi.readthedocs.io/transfer.html)

# 0.7.1

- Completed migration to new [web3-ethereum-defi](https://github.com/tradingstrategy-ai/web3-ethereum-defi) package name

# 0.6

- Feature: Added revert reason extraction for failed transactions
- Feature: Added `eth_defi.gas.node_default_gas_price_strategy` to support BNB Chain
- Fix: BNB Chain compatibility fixes because of brokeness in Ethereum JSON-RPC
- Fix: Ganache compatibility fixes because of brokeness in Ethereum JSON-RPC
- Fix: Wait 10 seconds instead of 5 seconds to ganache-cli to launch, as the latter is too slow for some computers
- Fix: Optimize `wait_transactions_to_complete`
- API change: Created a separate `broadcast_transactions` function

# 0.5

- Feature: Added initial Uniswap v3 testing support
- Feature: Allow override init code hash for `eth_defi.uniswap_v2.fetch_deployment`
- Feature: Faster failing if ganache-cli RPS port is already taken
- Feature: Added `fetch_erc20_balances_by_token_list`
- Feature: Added `get_transaction_data_field`
- API change: `uniswap_v2` or `uniswap_v3` are now their respective submodules
- API change: Rename `fetch_erc20_balances` -> `fetch_erc20_balances_by_transfer_event`
- API change: Removed `fetch_erc20_balances_decimal_by_transfer_event`
- API change: Rename `convert_to_decimal` -> `convert_balances_to_decimal`
- Fix: `fetch_erc20_balances`: User friendly error message when trying to grab a too big chunk of transfers once
- Fix: Use `london` hard fork by default for `fork_network`

# 0.4

-
Feature: [eth_defi.ganache module](https://smart-contracts-for-testing.readthedocs.io/en/latest/_autosummary/eth_defi.ganache.html#module-eth_defi.ganache)
to support ganache-cli mainnet forks
- Feature: `HotWallet.get_native_currency_balance` to easier management of hot wallet accounts
- Feature: `HotWallet.from_private_key` to easier management of hot wallet accounts

# 0.3

- Rename module: `eth_defi.portfolio` -> `eth_defi.balances`
- Fix: Documentation now builds correctly with body text for functions
- Fix: ERC-20 balances when there exist debit transactions

# 0.2.0

- Feature: ERC-20 token deployments with custom decimals
- Feature: Wallet ERC-20 token holdings analysis
- Feature: Scaleable Solidity event fetcher
- Feature: Uniswap v2 price impact and fee estimator
- Feature: Fetch Uniswap deployment from on-chain data
- Feature: ERC-20 detail fetcher
- Feature: London hard fork compatible gas estimator
- Feature: Hot wallet with nonce management and batch sending
- Feature: Sending and confirming transactions in batches
- Renamed package to `eth-hentai`

# 0.1

- Initial release
