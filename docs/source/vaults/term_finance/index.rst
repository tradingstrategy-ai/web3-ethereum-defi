Term Finance API
----------------

`Term Finance <https://www.term.finance/>`__ integration.

Term Finance is a noncustodial fixed-rate liquidity protocol modelled on tri-party
repo arrangements common in traditional finance (TradFi). Liquidity suppliers and
takers are matched through a unique weekly auction process where liquidity takers
submit bids and suppliers submit offers to the protocol.

The protocol determines a "market clearing rate" that matches supply and demand.
Bidders who bid more than the clearing rate receive loans and lenders asking less
than the clearing rate supply liquidity.

Key features:

- Fixed-rate DeFi lending and borrowing via auctions
- Scalable transactions with no spread, no slippage, and low fees
- Collateral sits in isolated noncustodial smart contracts (repoLocker)
- No rehypothecation - collateral cannot be lent to other borrowers

- `Homepage <https://www.term.finance/>`__
- `App <https://app.term.finance/>`__
- `Documentation <https://developers.term.finance>`__
- `GitHub <https://github.com/term-finance/term-finance-contracts>`__
- `Twitter <https://x.com/term_labs>`__
- `Example vault on Etherscan <https://etherscan.io/address/0xa10c40f9e318b0ed67ecc3499d702d8db9437228>`__


.. autosummary::
   :toctree: _autosummary_term_finance
   :recursive:

   eth_defi.erc_4626.vault_protocol.term_finance.vault
