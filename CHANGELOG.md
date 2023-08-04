# 0.22.4

- [JSON-RPC fallback and MEV protection tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/multi-rpc-configuration.html)
- Added missing `sigfig` lib dependency


# 0.22.3

- Fix: `eth_defi.chain.has_graphql_support` to support `MultiProviderWeb3`

# 0.22.2

- Add: `eth_defi.provider.multi_provider.create_multi_provider_web3`: An easy way to configure a Web3 instance with multiple providers

# 0.22.1

- Add logging to `swap_with_slippage_protection()` on Uniswap v3 
  to trace slippage issues

# 0.22

- Refactor a lot of functionality to a new submodule [eth_defi.provider](https://web3-ethereum-defi.readthedocs.io/api/provider/index.html) 
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
- Add: `figure_reorganisation_and_new_blocks(max_range)` to catch situations you somehow feed a too long block range to scan
- Add: `analyse_trade_by_receipt(input_args)` to analyse the success of Uni v3 trades when trading on Enzyme

# 0.18.4

- Fix: Use `web3.eth.wait_for_transaction_receipt` in appropriate places
- Add: Helper functions to interact with `UniswapV3PriceHelper`

# 0.18.3

- Add: TQDM progress bar support for event reading in the form of `eth_defi.event_reader.progress_update.TQDMProgressUpdate`
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
- Add [eth_defi.reader.multithread.MultithreadEventReader for easy to use high-speed Solidity event reading](https://web3-ethereum-defi.readthedocs.io/tutorials/multithread-reader.html)
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

- Added [a script for verifying the integrity of your EVM JSON-RPC node data](https://web3-ethereum-defi.readthedocs.io/tutorials/index.html)
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
  [fork_network_anvil](https://web3-ethereum-defi.readthedocs.io/api/_autosummary/eth_defi.anvil.html#module-eth_defi.anvil) that is a drop-in replacement for old
  Ganache based `fork_network`.
- Move internal test suite to use Anvil instead of Ganache. This allows us to remove
  `flaky` decorators on tests.
- Add `analysis.py` for Uniswap V3 and relevant tests
- Add `mock_partial_deployment` function for V3
- Abstract `TradeResult`, `TradeSuccess`, and `TradeFailure` out of Uniswap V2 and into eth_defi.trade, since also used in Uniswap V3
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

- Feature: [High speed Solidity events / eth_getLogs fetching and decoding](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/scripts/read-uniswap-v2-pairs-and-swaps-concurrent.py)
- Feature: [JSON-RPC retry middleware with sleep and backoff](https://web3-ethereum-defi.readthedocs.io/_autosummary/eth_defi.middleware.http_retry_request_with_sleep_middleware.html#eth_defi.middleware.http_retry_request_with_sleep_middleware)
- Feature: Added [decode_signed_transaction](https://web3-ethereum-defi.readthedocs.io/_autosummary/eth_defi.tx.decode_signed_transaction.html#eth_defi.tx.decode_signed_transaction) with EIP-2718 and EIP-2930 tx support
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
- Feature: Added support for [transfer fee, token tax and honeypot checks](https://tradingstrategy.ai/docs/programming/token-tax.html)
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

- Feature: [eth_defi.ganache module](https://smart-contracts-for-testing.readthedocs.io/en/latest/_autosummary/eth_defi.ganache.html#module-eth_defi.ganache) to support ganache-cli mainnet forks
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
