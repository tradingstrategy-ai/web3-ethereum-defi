# Current

- Update web3.py dependency to 5.28.0
- Feature: Added support for `fee` and `slippage` to Uniswap v2 price calculations
- Feature: Added Uniswap v2 swap helper function with slippage protection
- Feature: Added Uniswap v2 pair liquidity fetch
- Feature: Added support for three-way swap (swap through an intermediate token) and price calculations
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