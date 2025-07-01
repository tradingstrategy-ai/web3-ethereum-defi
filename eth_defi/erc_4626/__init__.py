"""Generic ERC-4624 vault handling.

ERC-4626 is a standard to optimize and unify the technical parameters of yield-bearing vaults. It provides a standard API for tokenized yield-bearing vaults that represent shares of a single underlying ERC-20 token. ERC-4626 also outlines an optional extension for tokenized vaults utilizing ERC-20, offering basic functionality for depositing, withdrawing tokens and reading balances.

- Iterate all protocol ERC-4626 vaults
- Read historical market data
- Deposit and redeem from vaults

Tutorials

- ref:`scan-erC_4626_vaults`

More info

- `Difference between preview and redeem in ERC-4626 <https://x.com/i/grok/share/4wpbfzVDk7PrQO8g41R0cpr6b>`__.
- https://ethereum.org/en/developers/docs/standards/tokens/erc-4626/
- https://docs.openzeppelin.com/contracts/5.x/erc4626

"""
