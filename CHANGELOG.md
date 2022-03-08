# Current

- Feature: Allow override init code hash for `eth_hentai.uniswap_v2.fetch_deployment`
- Feature: Faster failing if ganache-cli RPS port is already taken
- Feature: Add `fetch_erc20_balances_by_token_list`
- API change: Rename `fetch_erc20_balances` -> `fetch_erc20_balances_by_transfer_event`
- API change: Removed `fetch_erc20_balances_decimal_by_transfer_event`
- API change: Rename `convert_to_decimal` -> `convert_balances_to_decimal`
- Fix: `fetch_erc20_balances`: User friendly error message when trying to grab a too big chunk of transfers once
- Fix: Use `london` hard fork by default for `fork_network`

# 0.4

- Feature: [eth_hentai.ganache module](https://smart-contracts-for-testing.readthedocs.io/en/latest/_autosummary/eth_hentai.ganache.html#module-eth_hentai.ganache) to support ganache-cli mainnet forks
- Feature: `HotWallet.get_native_currency_balance` to easier management of hot wallet accounts
- Feature: `HotWallet.from_private_key` to easier management of hot wallet accounts

# 0.3

- Rename module: `eth_hentai.portfolio` -> `eth_hentai.balances`
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