# Current

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
